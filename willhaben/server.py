import argparse
import asyncio
import functools
import inspect
import json
import logging
import uuid
from typing import Annotated, Literal, Optional, TypeVar, Union

import httpx
from pydantic import BeforeValidator
from mcp.server import MCPServer
from mcp.server.caching import CacheHint
from mcp.server.mcpserver import Image
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from willhaben import api, autos, immobilien, marktplatz, matching, regions, summaries

HOST = "127.0.0.1"
PORT = 8010
MCP_PATH = "/mcp"

# Sent once when a client connects, so what every tool shares is said here
# instead of in each tool's description, which the model reads on every turn.
INSTRUCTIONS = """\
willhaben.at, Austria's classifieds site: the marketplace, used cars (Auto & \
Motor) and real estate (Immobilien). Prices are in EUR.

Which tool to use:
- something to buy: search_willhaben. Scope it with a category from \
list_categories; brands come from search_brands.
- a car: search_autos, with makes from list_car_makes and models from \
list_car_models.
- a flat, house or plot: search_immobilien, with types from \
list_immobilien_types.
- one ad in full: get_ad_detail (full text, attributes, shipping), \
get_ad_images (the photos themselves) or get_ad_seller (who sells it).

Shared parameters: area is an Austrian state (wien, niederoesterreich, ...); \
district narrows it to Bezirke, by name ("Graz", "Innsbruck-Land", "Neubau") \
or Vienna postcode ("1070"). \
sort_by is actuality (default), price_asc, price_desc, nearest or relevance. \
rows is at most 50; page on with the next_offset a result returns, has_more \
says whether anything is left.

status is "active", "reserved" or "sold". willhaben always reports an ad as \
active, so this is read from the seller's marker in the title. Search hits \
carry it only when it is not "active".

Fields without a value are left out of results. Search hits carry no link: an \
ad's page is https://www.willhaben.at/iad/object?adId={id}.
"""

# The tool list only changes with a new release, so clients on the 2026-07-28
# protocol may keep it for a day instead of asking again on every connect.
mcp = MCPServer("willhaben-mcp", instructions=INSTRUCTIONS,
                cache_hints={"tools/list": CacheHint(ttl_ms=24 * 60 * 60 * 1000, scope="public")})


logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s: %(message)s')

MAX_ROWS = 50


T = TypeVar("T")


def _listify(value):
    return value if value is None or isinstance(value, list) else [value]


# One value or a list of them. The schema shows only the list, so an enum is
# listed once instead of twice (as a value and as list items); a bare value is
# still accepted and wrapped.
Many = Annotated[list[T], BeforeValidator(_listify)]


def _prune(value):
    """Drop the null fields from every dict in `value`; lists keep their items."""
    if isinstance(value, dict):
        return {k: _prune(v) for k, v in value.items() if v is not None}
    if isinstance(value, list):
        return [_prune(v) for v in value]
    return value


# Every tool only reads, so clients can run them without asking each time.
# list_categories, list_car_makes and list_immobilien_types answer from the
# shipped data; the rest go out to willhaben.
ONLINE = ToolAnnotations(readOnlyHint=True, openWorldHint=True)
LOCAL = ToolAnnotations(readOnlyHint=True, openWorldHint=False)


def _slim_schema(node):
    """Cut what pydantic adds to every parameter but no model needs: a
    ``title`` that repeats the name, and for an optional parameter the
    ``"default": null`` and the ``null`` branch of its ``anyOf`` (leaving it out
    is how it is omitted). Validation still runs on the full Python signature,
    so this only shortens what the model reads on every turn."""
    if not isinstance(node, dict):
        return node
    node.pop("title", None)
    if "default" in node and node["default"] is None:
        del node["default"]
        branches = [b for b in node.get("anyOf", []) if b != {"type": "null"}]
        if branches:
            del node["anyOf"]
            if len(branches) == 1:
                node.update(branches[0])
            else:
                node["anyOf"] = branches
    for key in ("properties", "$defs"):
        for child in node.get(key, {}).values():
            _slim_schema(child)
    for key in ("items", "additionalProperties"):
        _slim_schema(node.get(key))
    for branch in node.get("anyOf", []):
        _slim_schema(branch)
    return node


def _register(fn, annotations: ToolAnnotations):
    """Register `fn` (its docstring dedented, since only Python 3.13+ does that
    itself) and slim its parameter schema."""
    mcp.tool(description=inspect.cleandoc(fn.__doc__ or ""), annotations=annotations,
             structured_output=False)(fn)
    tool = mcp._tool_manager.get_tool(fn.__name__)
    tool.parameters = _slim_schema(tool.parameters)
    return fn


def _tool(fn=None, *, annotations: ToolAnnotations = ONLINE):
    """Register `fn` as a tool whose dict result goes out as compact JSON with
    its null fields left out. Left to the SDK, a dict is pretty-printed with a
    two-space indent, which is about a third of a typical result's tokens.
    structured_output stays off: it would send every result a second time as
    structuredContent and add an output schema to the tool listing."""
    if fn is None:
        return functools.partial(_tool, annotations=annotations)

    @functools.wraps(fn)
    async def wrapper(*args, **kwargs):
        result = await fn(*args, **kwargs)
        return json.dumps(_prune(result), ensure_ascii=False, separators=(",", ":"))
    return _register(wrapper, annotations)


def _content_tool(fn):
    """Register `fn` as is, for a tool that returns content blocks rather than a
    dict."""
    return _register(fn, ONLINE)


def _check_ranges(*bounds) -> None:
    """Reject an inverted range instead of letting willhaben drop the filter and
    quietly answer with everything."""
    for name, low, high in bounds:
        if low is not None and high is not None and low > high:
            raise ToolError(
                f"{name}_from ({low}) is above {name}_to ({high}) -- an inverted range is "
                f"ignored by willhaben and would silently return unfiltered results. Swap them."
            )


# A keyword or exclude filter is applied here, not by willhaben, so pages are
# pulled until enough matches are collected -- bounded, to stay polite.
FILTER_MAX_SCAN = 250
# Whether a seller ships is only in the ad detail, so a shipping filter costs
# one detail request per candidate. Hard cap so a search can't turn into a crawl.
MAX_DETAIL_LOOKUPS = 40

Handover = Literal["versand", "abholung"]


async def _handover(ad_id) -> Optional[list[str]]:
    """The ad's handover options, or None if the detail could not be read."""
    try:
        detail = await api.fetch_detail(ad_id)
    except (httpx.HTTPError, ToolError):
        return None
    return summaries.handover_options(summaries.detail_attributes(detail))


def _rows(rows: int, offset: int = 0) -> int:
    """Validate a page size rather than quietly clamping 0 up to 1, and the
    offset that goes with it."""
    rows = int(rows)
    if rows < 1:
        raise ToolError(f"rows must be at least 1 (got {rows}).")
    if offset < 0:
        raise ToolError(f"offset must not be negative (got {offset}).")
    return min(rows, MAX_ROWS)


# A list tool must not be able to flood the model's context: every category at
# once is ~190k tokens. Larger asks are clamped; `query` narrows the rest.
MAX_LIST = 100


def _limit(limit: int) -> int:
    """`limit` clamped to 1..MAX_LIST, for the list tools."""
    if limit < 1:
        raise ToolError(f"limit must be at least 1 (got {limit}).")
    return min(limit, MAX_LIST)


def _pagination(rows_found, scanned_to: int, returned: int) -> dict:
    """Where the caller stands in the result set, so paging can be stopped on a
    signal instead of on a repeated page."""
    more = not (rows_found is not None and scanned_to >= rows_found)
    return {
        "rows_found": rows_found,
        "rows_returned": returned,
        "next_offset": scanned_to if more else None,
        "has_more": more,
    }


def _keeper(keyword: Optional[str], *, title_only: bool, require, exclude, hide_reserved: bool,
            extra=None):
    """The filters run here rather than at willhaben, as one ``keep(hit)``, and
    whether any is set (then pages are pulled until enough hits pass).
    ``extra`` is a further check that does not by itself call for paging on."""
    wanted = matching.tokens(keyword) if (title_only and keyword) else set()
    excluded = [matching.term_forms(t) for t in (exclude or []) if str(t).strip()]
    required = [matching.term_forms(t) for t in (require or []) if str(t).strip()]

    def keep(ad: dict) -> bool:
        if extra and not extra(ad):
            return False
        tokens = matching.tokens(ad["title"] or "")
        if wanted and not wanted <= tokens:
            return False
        if any(matching.has_term(tokens, forms) for forms in excluded):
            return False
        if required:
            # a spec is written wherever the seller felt like it, so look at the
            # whole ad rather than demanding it in the title
            text = tokens | matching.tokens(ad.get("description") or "")
            if not all(matching.has_term(text, forms) for forms in required):
                return False
        return not (hide_reserved and ad.get("status"))

    return keep, bool(wanted or excluded or required or hide_reserved)


def _headers() -> dict:
    return {
        "x-wh-client": api.WH_CLIENT,
        "x-wh-visitor-id": str(uuid.uuid4()),
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


async def _scan(url: str, params: dict, *, rows: int, offset: int, summarize_page, keep,
                post_filtered: bool, handover: Optional[str] = None) -> dict:
    """Page through a willhaben search from ``offset`` until ``rows`` hits pass
    ``keep``. Without a filter one page of ``rows`` is all it takes; with one,
    full pages are pulled until enough pass or FILTER_MAX_SCAN ads were looked
    at. ``summarize_page`` turns a page of raw adverts into hits."""
    headers = _headers()
    results: list = []
    cursor, lookups, rows_found = offset, 0, None
    while True:
        params["rows"] = MAX_ROWS if post_filtered else rows
        params["offset"] = cursor
        logger.debug("Requesting: '%s'\nWith parameters: %s", url, params)
        data = (await api.get(url, params=params, headers=headers)).json()
        rows_found = data.get("rowsFound")
        ads = data.get("advertSummaryList", {}).get("advertSummary", [])
        for summary in await summarize_page(ads):
            # out of detail lookups: stop before this ad, so the next page
            # starts with it rather than skipping it unchecked
            if handover and lookups >= MAX_DETAIL_LOOKUPS and keep(summary):
                break
            cursor += 1
            if not keep(summary):
                continue
            # the expensive check runs last, only on ads everything else kept
            if handover:
                lookups += 1
                offered = await _handover(summary["id"])
                if offered is None:
                    continue
                folded = matching.fold(" ".join(offered))
                ships = "versand" in folded
                # "abholung" asks for pickup to be on offer, not for it to be
                # the only option -- most ads offer both.
                if not (ships if handover == "versand" else "abhol" in folded):
                    continue
                summary.update({"handover": offered, "ships": ships, "pickup_only": not ships})
            results.append(summary)
            if len(results) >= rows:
                break
        scanned = cursor - offset
        if not post_filtered or len(results) >= rows or not ads:
            break
        if scanned >= FILTER_MAX_SCAN or lookups >= MAX_DETAIL_LOOKUPS:
            break
        if rows_found is not None and cursor >= rows_found:
            break

    result = {**_pagination(rows_found, cursor, len(results)), "results": results}
    if post_filtered:
        result["scanned"] = cursor - offset
    if handover:
        result["detail_lookups"] = lookups
    return result


@_tool
@api.tool_errors("search")
async def search_willhaben(
    keyword: Optional[str] = None,
    *,
    category: Optional[Union[int, str]] = None,
    condition: Optional[Many[marktplatz.Condition]] = None,
    clothing_size: Optional[Many[str]] = None,
    shoe_size: Optional[Many[str]] = None,
    color: Optional[Many[marktplatz.Color]] = None,
    pattern: Optional[Many[marktplatz.Pattern]] = None,
    brand: Optional[Many[Union[int, str]]] = None,
    sort_by: api.SortBy = "actuality",
    area: Optional[Many[api.Bundesland]] = None,
    district: Optional[Many[Union[int, str]]] = None,
    seller: Optional[Many[api.Seller]] = None,
    price_from: Optional[float] = None,
    price_to: Optional[float] = None,
    paylivery: Optional[bool] = None,
    title_only: bool = False,
    exclude: Optional[list[str]] = None,
    require: Optional[list[str]] = None,
    hide_reserved: bool = False,
    handover: Optional[Handover] = None,
    last_48h: bool = False,
    rows: int = 4,
    offset: int = 0,
) -> dict:
    """Search the willhaben marketplace. Needs a ``keyword``, a
    ``category`` or both.

    A bare keyword matches the whole ad text and drags in accessories and spare
    parts, so scope to a category when you can. willhaben ignores punctuation:
    "???" in a keyword matches nothing, so write "drei fragezeichen".

    Put the model in ``keyword`` and every spec in ``require``. Sellers write
    "128GB", "128 GB" or leave it out of the title, so ``keyword="iPhone 13",
    require=["128gb"]`` finds what ``keyword="iPhone 13 128 GB"`` misses.

    These filters run here, paging on until ``rows`` matches turn up or 250 ads
    were scanned (``scanned``):
    - ``title_only``: every keyword word must be in the title.
    - ``require``: every term must be in the title or description; a number
      with its unit matches however it is spelled.
    - ``exclude``: drop titles carrying any of these words, e.g. ``["pro",
      "max"]`` to keep a base model clean of its trim levels. A word also hits
      the tail of a compound ("kabel" in "Ladekabel"), never a prefix ("pro"
      spares "Prozessor"). If variants turn up, search again with them excluded
      rather than dropping rows yourself.
    - ``hide_reserved``: drop reserved and sold ads.
    - ``handover`` ("versand" / "abholung"): costs one detail request per
      candidate, at most 40 (``detail_lookups``). ``paylivery`` is a payment
      method, not shipping.

    Don't exclude ordinary nouns like "case" or "akku", real listings use them
    too ("Akku 100%"). Keep accessories out with ``price_from`` at about 15-20%
    of the product's real price instead.

    Args:
        keyword: Search words.
        category: Category id or name; includes its subcategories.
        condition: "neu", "neuwertig", "generalueberholt", "gebraucht",
            "defekt" or "ausstellungsstueck".
        clothing_size / shoe_size: e.g. "38", "46 / S" or "42"; clothing and
            shoe categories only.
        color / pattern: willhaben's colours and patterns, mostly fashion.
        brand: Brand id(s) from ``search_brands``.
        seller: "privat" or "haendler".
        price_from / price_to: Price bounds in EUR.
        last_48h: Only ads from the last two days.
    """
    if not keyword and category is None:
        raise ToolError("Provide 'keyword' and/or 'category'.")
    _check_ranges(("price", price_from, price_to))
    rows = _rows(rows, offset)
    keep, post_filtered = _keeper(keyword, title_only=title_only, require=require, exclude=exclude,
                                  hide_reserved=hide_reserved)
    post_filtered = post_filtered or bool(handover)

    params: dict = {
        "sfId": str(uuid.uuid4()),
        "isLog": "true",
        "rows": rows,
        "sort": api.sort[sort_by],
        "offset": offset,
    }

    if keyword:
        params["keyword"] = keyword

    if category is not None:
        params["ATTRIBUTE_TREE"] = marktplatz.resolve_category(category)

    # condition + sizes all share the multi-valued treeAttributes parameter
    tree_attrs: list[str] = []
    if condition is not None:
        conds = condition if isinstance(condition, list) else [condition]
        tree_attrs += [marktplatz.condition_id[c] for c in conds]
    tree_attrs += marktplatz.resolve_sizes(clothing_size, marktplatz.clothing_size_id, "clothing size")
    tree_attrs += marktplatz.resolve_sizes(shoe_size, marktplatz.shoe_size_id, "shoe size")
    if color is not None:
        cols = color if isinstance(color, list) else [color]
        tree_attrs += [marktplatz.color_id[c] for c in cols]
    if pattern is not None:
        pats = pattern if isinstance(pattern, list) else [pattern]
        tree_attrs += [marktplatz.pattern_id[p] for p in pats]
    if brand is not None:
        brands = brand if isinstance(brand, list) else [brand]
        tree_attrs += [str(b).strip() for b in brands]
    if tree_attrs:
        params["treeAttributes"] = tree_attrs

    areas = regions.area_ids(area, district, api.area_id)
    if areas:
        params["areaId"] = areas

    if seller is not None:
        params["ISPRIVATE"] = (
            [api.verkaeufer[s] for s in seller] if isinstance(seller, list) else api.verkaeufer[seller]
        )

    if price_from is not None:
        params["PRICE_FROM"] = price_from

    if price_to is not None:
        params["PRICE_TO"] = price_to

    if paylivery is not None:
        params["paylivery"] = "true" if paylivery else "false"

    if last_48h:
        params["periode"] = 2

    async def summarize_page(ads):
        return [summaries.summarize_ad(ad) for ad in ads]

    return await _scan(api.BASE_URL, params, rows=rows, offset=offset, summarize_page=summarize_page,
                       keep=keep, post_filtered=post_filtered, handover=handover)


MAX_DETAILS = 10


async def _detail(ad_id: Union[str, int]) -> dict:
    summary = summaries.summarize_ad_detail(await api.fetch_detail(ad_id))
    # the detail API truncates long descriptions near 600 chars; recover the full
    # text from the web page only when it looks cut off.
    current = summary.get("description") or ""
    if len(current) >= api.DESCRIPTION_CAP:
        full = await api.fetch_full_description(ad_id)
        if full and len(full) > len(current):
            summary["description"] = full
    return summary


@_tool
@api.tool_errors("ad")
async def get_ad_detail(ad_id: Optional[Union[str, int]] = None,
                        ids: Optional[list[Union[str, int]]] = None) -> dict:
    """One ad in full: the complete description (also when the app
    API cuts it), condition, attributes, category, location and ``image_count``.
    To compare several, pass up to 10 ``ids`` instead; they come back as
    ``{count, ads, failed}``.

    It is the only place that says how an item changes hands: ``handover``
    ("Selbstabholung", "Versand") with ``ships`` and ``pickup_only``.

    Args:
        ad_id: The ad id from a search.
        ids: Several ad ids, instead of ``ad_id``.
    """
    if (ad_id is None) == (not ids):
        raise ToolError("Pass either 'ad_id' or 'ids'.")
    if not ids:
        return await _detail(ad_id)
    if len(ids) > MAX_DETAILS:
        raise ToolError(f"At most {MAX_DETAILS} ids per call (got {len(ids)}).")
    fetch = api.tool_errors("ad")(_detail)

    # one ad that fails (deleted, sold off) is listed rather than failing the rest
    async def one(i):
        try:
            return await fetch(i), None
        except ToolError as exc:
            return None, {"id": str(i), "error": str(exc)}

    done = await asyncio.gather(*(one(i) for i in dict.fromkeys(str(i) for i in ids)))
    ads = [ad for ad, _ in done if ad]
    return {"count": len(ads), "ads": ads, "failed": [e for _, e in done if e] or None}


_CONTENT_TYPE_FORMAT = {
    "image/webp": "webp",
    "image/jpeg": "jpeg",
    "image/jpg": "jpeg",
    "image/png": "png",
    "image/gif": "gif",
}


@_content_tool
@api.tool_errors("ad")
async def get_ad_images(ad_id: Union[str, int], max_images: int = 4,
                        detail: Literal["low", "high"] = "low") -> list:
    """The ad's photos as images you can look at, up to
    ``max_images`` (at most 10). Use them whenever the answer depends on what
    the item looks like: condition, damage, completeness, or details only a
    picture shows, such as a label or a screen.

    Args:
        ad_id: The ad id from a search.
        max_images: How many photos to fetch. Default 4.
        detail: "low" (300x400, enough for condition and completeness) or
            "high" (900x1200, about nine times the tokens) to read small print
            such as a label, serial number or screen.
    """
    max_images = max(1, min(int(max_images), 10))
    ad = await api.fetch_detail(ad_id)
    images = summaries.widget(ad.get("widgets", []), "PICTURE_SLIDER").get("advertImageList", [])
    # mainImageUrl is the app's 300x400 gallery size, referenceImageUrl the
    # 900x1200 original; either may be missing, so fall back to the other
    keys = ("referenceImageUrl", "mainImageUrl") if detail == "high" else ("mainImageUrl", "referenceImageUrl")
    urls = [next(img[k] for k in keys if img.get(k)) for img in images if any(img.get(k) for k in keys)]
    urls = urls[:max_images]
    if not urls:
        return [f"No images found for ad {ad_id}."]

    img_headers = {"user-agent": "willhaben/613937 okhttp/5.4.0 Android/14"}

    async def fetch(u: str):
        try:
            r = await api.client.get(u, headers=img_headers)
            r.raise_for_status()
            ctype = r.headers.get("content-type", "").split(";")[0].strip().lower()
            fmt = _CONTENT_TYPE_FORMAT.get(ctype, "jpeg")
            return Image(data=r.content, format=fmt)
        except Exception as exc:  # a single broken image shouldn't fail the tool
            logger.warning("image download failed (%s): %s", u, exc)
            return None

    fetched = await asyncio.gather(*(fetch(u) for u in urls))
    pictures = [img for img in fetched if img is not None]

    title = ad.get("description") or f"ad {ad_id}"
    header = f'{len(pictures)} image(s) for "{title}" (ad {ad_id}):'
    return [header, *pictures]


SELLER_TRUST_URL = "https://publicapi.willhaben.at/userprofile/trust-signals"
SELLER_PROFILE_URL = "https://ad-search.willhaben.at/restapi/v2/sellerprofile"
DEALER_PROFILE_URL = "https://api.willhaben.at/restapi/v2/dealerprofile"


@_tool
@api.tool_errors("seller")
async def get_ad_seller(ad_id: Union[str, int]) -> dict:
    """Who sells an ad: name, private or dealer, rating and reply
    time for private sellers, member since, and location. For a trust check the
    ad itself does not answer.

    Args:
        ad_id: The ad id from a search.
    """
    detail = await api.fetch_detail(ad_id)
    tms = detail.get("taggingData", {}).get("tmsDataValues", {}).get("tmsData", {})
    seller_id = tms.get("seller_id")
    name = tms.get("seller_name")
    if not seller_id:
        raise ToolError(f"Ad {ad_id} names no seller id (seller name: {name!r}).")

    if tms.get("is_private") == "true":
        result: dict = {"id": seller_id, "name": name, "type": "private"}
        try:  # rating / trust signals (no token needed)
            trust = (await api.get(f"{SELLER_TRUST_URL}/{seller_id}")).json()
            result.update({
                "rating": trust.get("averageRating"),
                "rating_count": trust.get("numberOfRatings"),
                "rating_note": trust.get("numberOfRatingsText"),
                "reply_time": trust.get("replyTime"),
            })
        except httpx.HTTPError:
            pass
        try:  # profile (member since); token-gated
            sp = (await api.authed_get(f"{SELLER_PROFILE_URL}/{seller_id}/5/profile")).json().get("sellerProfile", {})
            result["member_since"] = sp.get("registerDate")
            result["active_ads"] = sp.get("activeAdCount")
            result["location"] = sp.get("location")
        except httpx.HTTPError:
            pass
        return result

    result = {"id": seller_id, "name": name, "type": "dealer"}
    try:  # dealer / organisation; token-gated
        org = (await api.authed_get(f"{DEALER_PROFILE_URL}/{seller_id}")).json().get("organisation", {})
        addr = org.get("addressDto") or {}
        result.update({
            "name": org.get("displayName") or org.get("orgName") or name,
            "since": org.get("created"),
            "org_type": org.get("orgTypeDescription"),
            "location": " ".join(filter(None, [addr.get("addressPostcode"), addr.get("addressTown")])) or None,
        })
    except httpx.HTTPError:
        pass
    return result


@_tool(annotations=LOCAL)
async def list_categories(
    query: Optional[str] = None,
    parent_id: Optional[int] = None,
    limit: int = 50,
) -> dict:
    """Find category ids for ``search_willhaben``. Returns
    ``{count, categories: [{id, path, has_children}]}``.

    Args:
        query: Part of a category name; searches the whole tree.
        parent_id: List the subcategories of this id. Omit both for the top
            level.
        limit: At most 100. Default 50.
    """
    limit = _limit(limit)
    if query:
        q = matching.norm(query)
        ids = [cid for cid, lab in marktplatz.CATEGORY_LABEL.items() if q in matching.norm(lab)]
    else:
        ids = marktplatz.CATEGORY_CHILDREN.get(parent_id, [])

    ids.sort(key=lambda i: marktplatz.CATEGORY_PATH[i])
    results = [
        {
            "id": cid,
            # the path ends in the label, and the parent is the step before it
            "path": marktplatz.CATEGORY_PATH[cid],
            "has_children": bool(marktplatz.CATEGORY_CHILDREN.get(cid)),
        }
        for cid in ids[:limit]
    ]
    return {"count": len(ids), "returned": len(results), "categories": results}


@_tool
@api.tool_errors("brand")
async def search_brands(
    category: Union[int, str],
    term: str = "",
    limit: int = 40,
) -> dict:
    """Find brand ids for the ``brand`` filter of
    ``search_willhaben``. Brands depend on the category and there can be
    thousands, so pass a ``term``. Broad top categories may have no brand
    filter at all.

    Args:
        category: Category id or name; a specific one works best.
        term: Part of the brand name, e.g. "nik".
        limit: At most 100. Default 40.
    """
    limit = _limit(limit)
    catid = marktplatz.resolve_category(category)
    params = {
        "categoryId": catid,
        "attributeXmlCode": "BRAND",
        "navigatorId": "Marke",
        "term": term or "",
    }
    headers = {
        "x-wh-client": api.WH_CLIENT,
        "x-wh-visitor-id": str(uuid.uuid4()),
        "Accept": "application/json",
    }
    try:
        response = await api.client.get(api.ATTRIBUTE_OPTIONS_URL, params=params, headers=headers)
        response.raise_for_status()
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 500:
            return {"count": 0, "brands": [], "note": "This category has no brand filter. Try a more specific subcategory."}
        raise
    data = response.json()
    brands, seen = [], set()
    for group in data.get("optionGroups", []):
        for opt in group.get("options", []):
            val = opt.get("value")
            if val is None or val in seen:
                continue
            label = api.TAG_RE.sub("", opt.get("label", "")).strip()  # strip <b> highlight
            if label == "Andere Marken":
                continue
            seen.add(val)
            brands.append({"id": val, "label": label})
    return {"count": len(brands), "returned": min(len(brands), limit), "brands": brands[:limit]}


@_tool
@api.tool_errors("car search")
async def search_autos(
    make: Optional[Union[int, str]] = None,
    model: Optional[Union[int, str]] = None,
    *,
    keyword: Optional[str] = None,
    car_type: Optional[Many[str]] = None,
    fuel: Optional[Many[str]] = None,
    transmission: Optional[Many[str]] = None,
    wheel_drive: Optional[Many[str]] = None,
    condition: Optional[Many[str]] = None,
    color: Optional[Many[str]] = None,
    dealer: Optional[Many[str]] = None,
    equipment: Optional[Many[str]] = None,
    price_from: Optional[float] = None,
    price_to: Optional[float] = None,
    year_from: Optional[int] = None,
    year_to: Optional[int] = None,
    mileage_from: Optional[int] = None,
    mileage_to: Optional[int] = None,
    power_from: Optional[int] = None,
    power_to: Optional[int] = None,
    warranty: bool = False,
    condition_report: bool = False,
    exclude: Optional[list[str]] = None,
    require: Optional[list[str]] = None,
    hide_reserved: bool = False,
    sort_by: api.SortBy = "actuality",
    area: Optional[Many[api.Bundesland]] = None,
    district: Optional[Many[Union[int, str]]] = None,
    last_48h: bool = False,
    rows: int = 4,
    offset: int = 0,
) -> dict:
    """Search used cars. Everything is optional.

    Prefer the structured filters to ``keyword``: they match exact fields, a
    keyword searches the free text. Use a keyword for what has no filter, such
    as a chassis code ("E39") on top of make and model.

    Hits name ``make`` and ``model`` and give power as ``power_kw`` and
    ``power_ps``. willhaben tops a make search with a paid listing whatever the
    filters say; hits outside the requested ranges are dropped and counted in
    ``dropped_off_filter``.

    ``exclude``, ``require`` and ``hide_reserved`` work as in
    ``search_willhaben`` and page on until ``rows`` cars pass, e.g.
    ``exclude=["motorschaden", "export", "bastler"]``. Car hits carry no
    description, so ``require`` only looks at the title here.

    Args:
        make: Make id or name, see ``list_car_makes``.
        model: Model id or name, e.g. "3er-Reihe"; needs ``make``.
        keyword: Free-text words.
        car_type: e.g. "Kombi / Family Van", "SUV / Geländewagen".
        fuel: e.g. "Diesel", "Benzin", "Elektro", "Hybrid Elektro/Benzin".
        transmission: "Automatik" or "Schaltgetriebe".
        wheel_drive: "Allrad", "Hinterrad" or "Vorderrad".
        condition: e.g. "Gebrauchtwagen", "Neuwagen", "Oldtimer".
        color: Exterior colour, e.g. "Schwarz".
        dealer: "Händler" or "Privat".
        equipment: e.g. "Sitzheizung vorne", "Anhängerkupplung".
        price_from / price_to: EUR.
        year_from / year_to: First registration.
        mileage_from / mileage_to: Kilometres.
        power_from / power_to: kW, not PS (150 PS is 110 kW).
        warranty / condition_report: Only cars with a warranty / a current
            "Pickerl" (§57a).
        last_48h: Only ads from the last two days.
    """
    _check_ranges(("price", price_from, price_to), ("year", year_from, year_to),
                  ("mileage", mileage_from, mileage_to), ("power", power_from, power_to))
    rows = _rows(rows, offset)
    params: dict = {
        "sfId": str(uuid.uuid4()),
        "isLog": "true",
        "rows": rows,
        "offset": offset,
        "sort": api.sort[sort_by],
    }
    if keyword:
        params["keyword"] = keyword
    if make is not None:
        params["CAR_MODEL/MAKE"] = autos.resolve_make(make)
    if model is not None:
        params["CAR_MODEL/MODEL"] = await autos.resolve_model(model, params.get("CAR_MODEL/MAKE"))

    for value, qp, human in [
        (car_type, "CAR_TYPE", "car type"),
        (fuel, "ENGINE/FUEL", "fuel"),
        (transmission, "TRANSMISSION", "transmission"),
        (wheel_drive, "WHEEL_DRIVE", "drive"),
        (condition, "MOTOR_CONDITION", "condition"),
        (color, "EXTERIOR_COLOUR_MAIN", "colour"),
        (dealer, "DEALER", "seller"),
        (equipment, "EQUIPMENT", "equipment"),
    ]:
        ids = autos.resolve_select(value, qp, human)
        if ids:
            params[qp] = ids

    for val, key in [
        (price_from, "PRICE_FROM"), (price_to, "PRICE_TO"),
        (year_from, "YEAR_MODEL_FROM"), (year_to, "YEAR_MODEL_TO"),
        (mileage_from, "MILEAGE_FROM"), (mileage_to, "MILEAGE_TO"),
        (power_from, "ENGINEEFFECT_FROM"), (power_to, "ENGINEEFFECT_TO"),
    ]:
        if val is not None:
            params[key] = val

    if warranty:
        params["WARRANTY"] = 1
    if condition_report:
        params["CONDITION_REPORT"] = 1
    if last_48h:
        params["periode"] = 2
    areas = regions.area_ids(area, district, api.area_id)
    if areas:
        params["areaId"] = areas

    make_id = params.get("CAR_MODEL/MAKE")

    async def summarize_page(ads):
        model_ids = {str(summaries.ad_attributes(ad).get("CAR_MODEL/MODEL")) for ad in ads}
        if make_id and not model_ids <= autos.MODEL_NAMES.keys():
            try:  # one request per make, then the names are known for good
                await autos.fetch_models(make_id)
            except httpx.HTTPError:
                pass
        return [autos.summarize_car(ad) for ad in ads]

    # willhaben puts a paid dealer listing on top of a make search whatever the
    # filters say (a 48k car for "up to 15k"); drop anything outside them
    bounds = [("price_amount", price_from, price_to), ("year", year_from, year_to),
              ("mileage_km", mileage_from, mileage_to), ("power_kw", power_from, power_to)]
    dropped = 0

    def in_bounds(car: dict) -> bool:
        nonlocal dropped
        ok = all(car.get(field) is None
                 or ((low is None or car[field] >= low) and (high is None or car[field] <= high))
                 for field, low, high in bounds)
        dropped += not ok
        return ok

    keep, post_filtered = _keeper(keyword, title_only=False, require=require, exclude=exclude,
                                  hide_reserved=hide_reserved, extra=in_bounds)
    result = await _scan(autos.AUTO_RESULTS_URL, params, rows=rows, offset=offset,
                         summarize_page=summarize_page, keep=keep, post_filtered=post_filtered)
    if dropped:
        result["dropped_off_filter"] = dropped
    return result


@_tool(annotations=LOCAL)
async def list_car_makes(query: Optional[str] = None, limit: int = 60) -> dict:
    """List car make ids for ``search_autos``.

    Args:
        query: Part of the make name.
        limit: At most 100. Default 60.
    """
    limit = _limit(limit)
    if query:
        q = matching.norm(query)
        ids = [i for i, name in autos.CAR_MAKES.items() if q in matching.norm(name)]
    else:
        ids = list(autos.CAR_MAKES)
    ids.sort(key=lambda i: autos.CAR_MAKES[i].lower())
    makes = [{"id": i, "label": autos.CAR_MAKES[i]} for i in ids[:limit]]
    return {"count": len(ids), "returned": len(makes), "makes": makes}


@_tool
@api.tool_errors("car model")
async def list_car_models(make: Union[int, str]) -> dict:
    """List the models of a car make, for the ``model`` filter of
    ``search_autos``.

    Args:
        make: Make id or name.
    """
    make_id = autos.resolve_make(make)
    models = await autos.fetch_models(make_id)
    items = [{"id": v, "label": l} for v, l in models.items()]
    return {"make": autos.CAR_MAKES.get(int(make_id)), "count": len(items), "models": items}


@_tool
@api.tool_errors("property search")
async def search_immobilien(
    property_type: Union[int, str] = "Alle Immobilien",
    *,
    keyword: Optional[str] = None,
    object_type: Optional[Many[str]] = None,
    rooms: Optional[Union[str, int, list]] = None,
    features: Optional[Many[str]] = None,
    outdoor: Optional[Many[str]] = None,
    price_from: Optional[float] = None,
    price_to: Optional[float] = None,
    area_from: Optional[int] = None,
    area_to: Optional[int] = None,
    plot_from: Optional[int] = None,
    plot_to: Optional[int] = None,
    exclude: Optional[list[str]] = None,
    require: Optional[list[str]] = None,
    hide_reserved: bool = False,
    sort_by: api.SortBy = "actuality",
    area: Optional[Many[api.Bundesland]] = None,
    district: Optional[Many[Union[int, str]]] = None,
    postcode: Optional[Many[str]] = None,
    last_48h: bool = False,
    rows: int = 4,
    offset: int = 0,
) -> dict:
    """Search real estate. ``property_type`` picks buy or rent
    and the kind of property; everything else is optional, and filters that do
    not fit the type are ignored by willhaben.

    Hits carry ``price_amount`` and ``price_per_m2`` next to
    ``living_area_m2``, ``plot_area_m2``, ``rooms``, ``floor``, ``district`` and
    ``address``.

    ``exclude``, ``require`` and ``hide_reserved`` work as in
    ``search_willhaben``, e.g. ``exclude=["befristet", "provision"]``.
    Property hits carry no description, so ``require`` only looks at the
    title here.

    Args:
        property_type: Id or name from ``list_immobilien_types``, e.g.
            "Wohnung mieten", "Haus kaufen", "Grundstücke". Default "Alle
            Immobilien".
        keyword: Free-text words.
        object_type: e.g. "Einfamilienhaus", "Dachgeschosswohnung".
        rooms: "1" to "5", "6-9" or "10+".
        features: e.g. "Garage", "Keller", "Einbauküche".
        outdoor: e.g. "Balkon", "Terrasse", "Garten".
        price_from / price_to: EUR; rent or purchase price, depending on the type.
        area_from / area_to: Living area in m².
        plot_from / plot_to: Plot area in m².
        postcode: Exact postcodes, e.g. "1070" or "6020"; checked on each hit,
            so pair one outside Vienna with ``area`` or ``district``.
        last_48h: Only ads from the last two days.
    """
    catid = immobilien.resolve_type(property_type)
    # a Vienna postcode is a district willhaben can filter by itself, so the
    # scan below only has to confirm it instead of searching all of Austria
    if postcode and not district:
        codes = [str(c).strip() for c in postcode]
        if all(c in regions.BY_POSTCODE for c in codes):
            district = codes
    _check_ranges(("price", price_from, price_to), ("area", area_from, area_to),
                  ("plot", plot_from, plot_to))
    rows = _rows(rows, offset)
    params: dict = {
        "sfId": str(uuid.uuid4()),
        "isLog": "true",
        "rows": rows,
        "offset": offset,
        "sort": api.sort[sort_by],
    }
    if keyword:
        params["keyword"] = keyword

    object_ids = immobilien.resolve_options(object_type, immobilien.options("PROPERTY_TYPE"), "object type")
    if object_ids:
        params["PROPERTY_TYPE"] = object_ids
    feature_ids = immobilien.resolve_options(features, immobilien.options("ESTATE_PREFERENCE"), "feature")
    if feature_ids:
        params["ESTATE_PREFERENCE"] = feature_ids
    outdoor_ids = immobilien.resolve_options(outdoor, immobilien.options("FREE_AREA/FREE_AREA_TYPE"), "outdoor area")
    if outdoor_ids:
        params["FREE_AREA/FREE_AREA_TYPE"] = outdoor_ids
    room_ids = immobilien.resolve_rooms(rooms)
    if room_ids:
        params["NO_OF_ROOMS_BUCKET"] = room_ids

    for val, key in [
        (price_from, "PRICE_FROM"), (price_to, "PRICE_TO"),
        (area_from, "ESTATE_SIZE/LIVING_AREA_FROM"), (area_to, "ESTATE_SIZE/LIVING_AREA_TO"),
        (plot_from, "PLOT/AREA_FROM"), (plot_to, "PLOT/AREA_TO"),
    ]:
        if val is not None:
            params[key] = val

    if last_48h:
        params["periode"] = 2
    areas = regions.area_ids(area, district, api.area_id)
    if areas:
        params["areaId"] = areas

    url = f"{immobilien.IMMO_RESULTS_BASE}/{immobilien.IMMO_SEARCH_PREFIX}/{catid}"

    async def summarize_page(ads):
        return [immobilien.summarize_immo(ad) for ad in ads]

    wanted_codes = {str(c).strip() for c in (postcode or []) if str(c).strip()}
    keep, post_filtered = _keeper(keyword, title_only=False, require=require, exclude=exclude,
                                  hide_reserved=hide_reserved,
                                  extra=(lambda hit: hit.get("postcode") in wanted_codes) if wanted_codes else None)
    post_filtered = post_filtered or bool(wanted_codes)
    result = await _scan(url, params, rows=rows, offset=offset, summarize_page=summarize_page,
                         keep=keep, post_filtered=post_filtered)
    return {"property_type": immobilien.IMMO_TYPES.get(int(catid)), **result}


@_tool(annotations=LOCAL)
async def list_immobilien_types() -> dict:
    """List the property types for ``search_immobilien``."""
    types = [{"id": i, "label": immobilien.IMMO_TYPES[i]} for i in sorted(immobilien.IMMO_TYPES)]
    return {"count": len(types), "types": types}


def main(argv: Optional[list[str]] = None) -> None:
    """Serve over streamable HTTP, or over stdio with ``--stdio``. The
    ``willhaben-mcp`` command and ``python main.py`` both end up here."""
    parser = argparse.ArgumentParser(description="willhaben MCP server")
    parser.add_argument("--stdio", action="store_true",
                        help="speak MCP over stdin/stdout instead of serving HTTP")
    if parser.parse_args(argv).stdio:
        # stdout carries the protocol; the log already goes to stderr
        mcp.run("stdio")
    else:
        url = f"http://{HOST}:{PORT}{MCP_PATH}"
        logger.info("Starting willhaben-mcp server on %s", url)
        mcp.run("streamable-http", host=HOST, port=PORT, streamable_http_path=MCP_PATH)
