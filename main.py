from mcp.server import MCPServer
from mcp.server.mcpserver import Image
import httpx
import asyncio
import uuid
import json
import re
import html
import functools
import unicodedata
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional, Union, Literal
import logging

BASE_URL = "https://ad-search.willhaben.at/restapi/v2/search/atz/seo/kaufen-und-verkaufen/marktplatz"
DETAIL_URL = "https://publicapi.willhaben.at/atdetail/v1"
ATTRIBUTE_OPTIONS_URL = "https://app-aggregator.willhaben.at/api/v1/search/attribute-options"
APPLICATION_DATA_URL = "https://app-aggregator.willhaben.at/api/v1/application-data"

WH_CLIENT = "api@tailored-apps.com;willhabenapp;android;8.57.0;responsive_app"

# The detail API (HTTP/2) is gated by an `x-wh-application-token` that willhaben's
# server ISSUES: the app POSTs `application-data` with a signed
# `applicationTokenRequest` and gets back a token valid for 30 days. The
# signature is an HMAC over (organization, salt, timestamp) made with a key baked
# into the app -- we cannot compute a fresh one, but the server does not check the
# timestamp's age, so replaying one captured signed request keeps minting fresh
# 30-day tokens. So instead of hardcoding a token that expires, we store that
# signed request and fetch a live token from it (see `_fetch_application_token`).
#
# HOW TO REFRESH IF WILLHABEN ROTATES THE SIGNING KEY (stored request -> 401):
#   1. Run the app under an mitmproxy MITM with SSL-unpinning (see README/notes).
#   2. Cold-start willhaben and find the first `POST .../api/v1/application-data`.
#   3. Copy its JSON body's `applicationTokenRequest` object into WH_TOKEN_REQUEST
#      below (organization, salt, signature, timestamp). That's the only part that
#      needs the app; the token itself then comes from the server again.
WH_SECURITY_VERSION = "20130527022532"
WH_TOKEN_REQUEST = {
    "organization": "api@tailored-apps.com",
    "salt": "_xzcrY0SjCsTS6Uo",
    "signature": "3oC9/ga9Z7pIxTdx/sccKjmycnw=",
    "timestamp": "2026-08-24T09:25:52+0200",
}

HOST = "127.0.0.1"
PORT = 8010
MCP_PATH = "/mcp"

mcp = MCPServer("willhaben-mcp")
# HTTP/2 is required by the detail API; the search API negotiates it too.
client = httpx.AsyncClient(http2=True)

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s: %(message)s')

MAX_ROWS = 50
RETRYABLE_STATUS = {429, 500, 502, 503, 504}
MAX_RETRIES = 3


class UpstreamError(RuntimeError):
    """A willhaben failure, already phrased for the caller. Tools turn it into
    ``{"error": ...}`` instead of leaking the internal url and an MDN link."""


def _upstream_message(status: int, what: str) -> str:
    """Phrase an HTTP failure in terms the caller can act on, without the
    internal url and MDN link httpx puts in its own message."""
    if status == 404:
        return f"no {what} found -- wrong id, or the ad has been taken down"
    if status == 400:
        return f"willhaben rejected the {what} request (400); check the arguments you passed"
    if status in (401, 403):
        return (f"willhaben refused the {what} request ({status}) -- the application token "
                f"could not be refreshed; see WH_TOKEN_REQUEST in main.py")
    return f"willhaben failed on the {what} request (HTTP {status})"


def _tool_errors(what: str):
    """Return upstream failures as ``{"error": ...}`` rather than raising a raw
    httpx error, so every tool fails the same readable way. ``what`` names the
    thing being fetched, for the message."""
    def decorator(fn):
        @functools.wraps(fn)
        async def wrapper(*args, **kwargs):
            try:
                return await fn(*args, **kwargs)
            except UpstreamError as exc:
                return {"error": str(exc)}
            except httpx.HTTPStatusError as exc:
                return {"error": _upstream_message(exc.response.status_code, what)}
            except httpx.HTTPError as exc:
                return {"error": f"could not reach willhaben for the {what} request "
                                 f"({type(exc).__name__})"}
        return wrapper
    return decorator


async def _get(url: str, *, params: Optional[dict] = None, headers: Optional[dict] = None) -> httpx.Response:
    """GET a willhaben endpoint, retrying transient failures (timeouts, 429,
    5xx) with exponential backoff. Other 4xx errors fail immediately."""
    for attempt in range(MAX_RETRIES + 1):
        try:
            response = await client.get(url, params=params, headers=headers)
        except httpx.TransportError:
            if attempt == MAX_RETRIES:
                raise
        else:
            if response.status_code not in RETRYABLE_STATUS or attempt == MAX_RETRIES:
                response.raise_for_status()
                return response
        delay = 0.5 * (2 ** attempt)
        logger.warning("Request to '%s' failed (attempt %d/%d), retrying in %.1fs",
                        url, attempt + 1, MAX_RETRIES + 1, delay)
        await asyncio.sleep(delay)


# --- application token (for the detail API) --------------------------------
# Fetched lazily from willhaben and cached; refreshed automatically on a 401.
_app_token: Optional[str] = None
_app_token_lock = asyncio.Lock()


async def _fetch_application_token() -> str:
    """Obtain a fresh 30-day ``x-wh-application-token`` from willhaben by replaying
    the stored signed ``applicationTokenRequest`` (see WH_TOKEN_REQUEST)."""
    headers = {
        "Accept": "application/json",
        "x-wh-client": WH_CLIENT,
        "x-wh-visitor-id": str(uuid.uuid4()),
    }
    try:
        response = await client.post(
            APPLICATION_DATA_URL,
            json={"applicationTokenRequest": WH_TOKEN_REQUEST},
            headers=headers,
        )
    except httpx.HTTPError as exc:
        raise RuntimeError(f"Could not reach willhaben to obtain an application token: {exc}") from exc
    if response.status_code == 401:
        raise RuntimeError(
            "willhaben rejected the stored token-refresh credential (401). Its signing "
            "key was most likely rotated -- capture a fresh applicationTokenRequest from "
            "the app and update WH_TOKEN_REQUEST in main.py (see the comment there)."
        )
    response.raise_for_status()
    token = (response.json().get("applicationToken") or {}).get("value")
    if not token:
        raise RuntimeError(
            "willhaben returned no application token -- the application-data response "
            "format may have changed."
        )
    logger.debug("Fetched a fresh willhaben application token")
    return token


async def _get_application_token(*, force_refresh: bool = False) -> str:
    """Return the cached application token, fetching or refreshing it as needed."""
    global _app_token
    async with _app_token_lock:
        if _app_token is None or force_refresh:
            _app_token = await _fetch_application_token()
    return _app_token


SortBy = Literal["actuality", "nearest", "price_asc", "price_desc", "relevance"]
Bundesland = Literal["burgenland", "kaernten", "niederoesterreich", "oberoesterreich",
                     "salzburg", "steiermark", "tirol", "vorarlberg", "wien", "andere_laender"]
Seller = Literal["haendler", "privat"]

sort = {
    "actuality": 1,
    "nearest": 2,
    "price_asc": 3,
    "price_desc": 4,
    "relevance": 5,
}

area_id = {
    "burgenland": 1,
    "kaernten": 2,
    "niederoesterreich": 3,
    "oberoesterreich": 4,
    "salzburg": 5,
    "steiermark": 6,
    "tirol": 7,
    "vorarlberg": 8,
    "wien": 900,
    "andere_laender": 22000,
}

verkaeufer = {
    "haendler": 0,
    "privat": 1,
}

# ---------------------------------------------------------------------------
# Categories & tree attributes (condition / sizes)
#
# willhaben constrains a search to a category via the ``ATTRIBUTE_TREE`` query
# parameter (the category id). Condition, clothing size and shoe size are all
# carried by the *same* multi-valued ``treeAttributes`` parameter -- the ids
# below were harvested from the app's own filter responses.
# ---------------------------------------------------------------------------

Condition = Literal[
    "neu", "neuwertig", "generalueberholt", "gebraucht", "defekt", "ausstellungsstueck"
]
condition_id = {
    "neu": "22",
    "neuwertig": "2546",
    "generalueberholt": "5013256",
    "gebraucht": "23",
    "defekt": "24",
    "ausstellungsstueck": "2539",
}

# Clothing size -> treeAttributes id. Keys are willhaben's own labels.
# willhaben uses two clothing size systems depending on the item type; both are
# accepted here. Pass the one that matches the garment (numeric for most
# women's/men's wear, letters for tops/knitwear etc.):
#   * numeric / combined: "36", "38", ... "46 / S", "48 / M", ... "ab 58"
#   * pure letters:        "XS", "S", "M", "L", "XL"
clothing_size_id = {
    # numeric / combined system
    "bis 34": "5014962", "36": "5014963", "38": "5014964", "40": "5014965",
    "42": "5014966", "44": "3226", "46 / S": "3227", "48 / M": "3228",
    "50 / L": "3229", "52 / L": "3230", "54 / XL": "3231", "56 / XL": "3232",
    "ab 58": "3233",
    # pure letter system
    "bis XS": "4313", "XS": "4313", "S": "4314", "M": "4315", "L": "4316",
    "XL": "4317", "ab XL": "4317",
}
# Colour -> treeAttributes id.
color_id = {
    "mehrfarbig": "3200", "schwarz": "3201", "braun": "3202", "blau": "3203",
    "tuerkis": "3204", "gruen": "3205", "gelb": "3206", "orange": "3207",
    "rot": "3208", "rosa": "3209", "violett": "3210", "grau": "3211",
    "silber": "3212", "gold": "3213", "beige": "3214", "weiss": "3215",
}

# Pattern -> treeAttributes id.
pattern_id = {
    "einfarbig": "5010052", "mustermix": "5010064", "farbverlauf": "5010059",
    "floral": "5010053", "gepunktet": "5010058", "gestreift": "5010054",
    "nadelstreifen": "5010062", "kariert": "5010057", "fischgraet": "5010063",
    "camouflage": "5010061", "paisley": "5010060", "motivprint": "5010055",
    "animalprint": "5010056", "andere muster": "5010065",
}

Color = Literal[
    "mehrfarbig", "schwarz", "braun", "blau", "tuerkis", "gruen", "gelb",
    "orange", "rot", "rosa", "violett", "grau", "silber", "gold", "beige", "weiss",
]
Pattern = Literal[
    "einfarbig", "mustermix", "farbverlauf", "floral", "gepunktet", "gestreift",
    "nadelstreifen", "kariert", "fischgraet", "camouflage", "paisley",
    "motivprint", "animalprint", "andere muster",
]

# Shoe size -> treeAttributes id.
shoe_size_id = {
    "bis 15": "4319", "16": "5014897", "17": "5014898", "18": "5014899",
    "19": "5014900", "20": "5014901", "21": "5014902", "22": "5014903",
    "23": "5014904", "24": "5014905", "25": "5014906", "26": "5014907",
    "27": "5014908", "28": "5014909", "29": "5014910", "30": "5014911",
    "31": "5014912", "32": "5014913", "33": "5014914", "34": "5014915",
    "35": "5014916", "36": "4330", "37": "4331", "38": "4332", "39": "4333",
    "40": "4334", "41": "4335", "42": "4336", "43": "4337", "44": "4338",
    "45": "4339", "46": "4340", "47": "4341", "ab 48": "4342",
}


def _fold(s: str) -> str:
    """Lowercase and strip accents, so "Grün" and "gruen" compare equal."""
    return unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode().lower()


def _norm(s: str) -> str:
    """Normalise a label for lookup: lowercase, strip accents, drop all spaces."""
    return "".join(_fold(s).split())


_TOKEN_RE = re.compile(r"[a-z0-9]+")
# Ads fuse series and model number ("RTX4070"); split those so a keyword written
# with a space still matches, and the other way round.
_FUSED_RE = re.compile(r"^([a-z]{2,4})(\d{3,5})$")


def _tokens(text: str) -> set:
    """Comparable word tokens of a title or keyword. Fused model codes are
    replaced by their parts on both sides, so "RTX4070" and "RTX 4070" match
    each other whichever one the keyword uses."""
    out = set()
    for token in _TOKEN_RE.findall(_fold(text or "")):
        fused = _FUSED_RE.match(token)
        out.update(fused.groups() if fused else [token])
    return out


def _check_ranges(*bounds) -> None:
    """Reject an inverted range instead of letting willhaben drop the filter and
    quietly answer with everything."""
    for name, low, high in bounds:
        if low is not None and high is not None and low > high:
            raise ValueError(
                f"{name}_from ({low}) is above {name}_to ({high}) -- an inverted range is "
                f"ignored by willhaben and would silently return unfiltered results. Swap them."
            )


def _load_categories() -> list[dict]:
    """Load the crawled willhaben category tree (``[{id,label,parentId,children}]``)
    that sits next to this module. Missing file -> empty tree (search still works
    without category filtering)."""
    path = Path(__file__).parent / "data" / "marktplatz" / "categories.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        logging.getLogger(__name__).warning("marktplatz categories.json not found -- category filter disabled")
        return []


CATEGORY_LABEL: dict[int, str] = {}
CATEGORY_PARENT: dict[int, Optional[int]] = {}
CATEGORY_CHILDREN: dict[Optional[int], list[int]] = {}
CATEGORY_PATH: dict[int, str] = {}
_CATEGORY_BY_NAME: dict[str, list[int]] = {}


def _index_categories(nodes: list[dict], parent: Optional[int] = None, trail: tuple = ()) -> None:
    for nd in nodes:
        cid = nd["id"]
        label = nd.get("label") or str(cid)
        CATEGORY_LABEL[cid] = label
        CATEGORY_PARENT[cid] = parent
        CATEGORY_CHILDREN.setdefault(parent, []).append(cid)
        path = trail + (label,)
        CATEGORY_PATH[cid] = " / ".join(path)
        _CATEGORY_BY_NAME.setdefault(_norm(label), []).append(cid)
        _index_categories(nd.get("children", []), cid, path)


_index_categories(_load_categories())


def _resolve_category(category: Union[int, str]) -> str:
    """Resolve a category given as id or (unique) name to its ATTRIBUTE_TREE id."""
    if isinstance(category, int) or (isinstance(category, str) and category.strip().isdigit()):
        cid = int(str(category).strip())
        if cid not in CATEGORY_LABEL:
            raise ValueError(f"Unknown category id {cid}. Use the list_categories tool.")
        return str(cid)
    matches = _CATEGORY_BY_NAME.get(_norm(category), [])
    if not matches:
        raise ValueError(f"Unknown category '{category}'. Use list_categories to find the id.")
    if len(matches) > 1:
        opts = ", ".join(f"{i} ({CATEGORY_PATH[i]})" for i in matches[:10])
        raise ValueError(f"Category '{category}' is ambiguous ({len(matches)} matches): {opts}. Please pass an id.")
    return str(matches[0])


def _resolve_sizes(values, mapping: dict, kind: str) -> list[str]:
    """Map one or more size labels/ids to treeAttributes ids."""
    if values is None:
        return []
    if not isinstance(values, list):
        values = [values]
    ids_by_norm = {_norm(k): v for k, v in mapping.items()}
    valid_ids = set(mapping.values())
    out = []
    for v in values:
        s = str(v).strip()
        if s in valid_ids:                     # already a treeAttributes id
            out.append(s)
        elif _norm(s) in ids_by_norm:          # a label like "42" / "46 / S"
            out.append(ids_by_norm[_norm(s)])
        else:
            raise ValueError(
                f"Unknown {kind} '{v}'. Valid values: {', '.join(mapping)}"
            )
    return out


def _attributes_to_dict(ad: dict) -> dict:
    """Flatten willhaben's ``attributes.attribute`` list (a list of
    ``{"name": ..., "values": [...]}``) into a plain ``name -> value`` dict.
    Single-element value lists are unwrapped to their only value."""
    result: dict = {}
    for attr in ad.get("attributes", {}).get("attribute", []):
        values = attr.get("values", [])
        result[attr["name"]] = values[0] if len(values) == 1 else values
    return result


def _first_image_url(ad: dict) -> Optional[str]:
    images = ad.get("advertImageList", {}).get("advertImage", [])
    if images:
        return images[0].get("referenceImageUrl") or images[0].get("mainImageUrl")
    return None


def _to_float(value) -> Optional[float]:
    try:
        return float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return None


_PRICE_IN_TEXT_RE = re.compile(r"(\d[\d.\s]*)(?:,(\d{1,2}))?")


def _price_amount(attr: dict) -> Optional[float]:
    """The numeric price. Marketplace and car ads carry ``PRICE/AMOUNT``;
    property ads only have ``PRICE`` (or the rent), so fall back through those
    and finally parse the display string -- otherwise sorting and price-per-m2
    are impossible on a value that is plainly there."""
    for key in ("PRICE/AMOUNT", "PRICE", "RENT/PER_MONTH_LETTINGS", "PRICE/SALES_PRICE"):
        amount = _to_float(attr.get(key))
        if amount is not None:
            return amount
    match = _PRICE_IN_TEXT_RE.search(str(attr.get("PRICE_FOR_DISPLAY") or ""))
    if not match:
        return None
    return _to_float(f"{re.sub(r'[.\s]', '', match[1])}.{match[2] or 0}")


# willhaben has no reserved/sold flag in the API -- sellers write it into the
# title, in every bracket style there is: "(reserviert)", "*reserviert*",
# "RESERVIERT - ...". Only those set-apart forms count, so a title that merely
# talks about reserving ("Artikel werden nicht reserviert") is not a match.
def _marker_re(words: str) -> re.Pattern:
    return re.compile(
        # bracketed or leading, in any case ...
        rf"(?i:(?:^\s*|[(\[*/|-]\s*)(?:{words})\b|\b(?:{words})\s*[)\]*])"
        # ... or shouted anywhere in the title
        rf"|\b(?:{words.upper()})\b"
    )


_RESERVED_RE = _marker_re("reserviert|reserved|vergeben")
_SOLD_RE = _marker_re("verkauft|sold|abgeholt")


def _ad_status(title: Optional[str], ad: Optional[dict] = None) -> tuple[str, bool]:
    """``(status, reserved)`` for an ad. willhaben only ever reports "active",
    so the real state comes from the marker sellers put in the title."""
    status = ((ad or {}).get("advertStatus") or {}).get("id") or "active"
    text = title or ""
    if status == "active" and _SOLD_RE.search(text):
        status = "sold"
    elif status == "active" and _RESERVED_RE.search(text):
        status = "reserved"
    return status, status == "reserved"


def _summarize_ad(ad: dict) -> dict:
    """Reduce a full willhaben advert to the handful of fields that matter
    for an AI response instead of returning the whole raw object."""
    attr = _attributes_to_dict(ad)
    seo_url = attr.get("SEO_URL")
    title = attr.get("HEADING") or ad.get("description")
    status, reserved = _ad_status(title, ad)
    return {
        "id": ad.get("id"),
        "title": title,
        "description": attr.get("BODY_DYN"),
        "price": attr.get("PRICE_FOR_DISPLAY"),
        "price_amount": _price_amount(attr),
        "status": status,
        "reserved": reserved,
        "seller": "privat" if attr.get("ISPRIVATE") == "1" else "haendler",
        "seller_name": attr.get("ORGNAME") or attr.get("CONTACT/NAME"),
        "location": attr.get("LOCATION"),
        "postcode": attr.get("POSTCODE"),
        "state": attr.get("STATE"),
        "published": attr.get("PUBLISHED_String"),
        "paylivery": attr.get("p2penabled") == "true",
        "url": f"https://www.willhaben.at/iad/{seo_url}" if seo_url else None,
        "image_url": _first_image_url(ad),
    }


def _widget(widgets: list[dict], widget_type: str) -> dict:
    """Return the first detail widget of the given type, or an empty dict."""
    return next((w for w in widgets if w.get("type") == widget_type), {})


def _summarize_ad_detail(detail: dict) -> dict:
    """Reduce willhaben's widget-based advert-detail response to the fields that
    matter for an AI: the complete (untruncated) description, every image, the
    itemised attributes and precise location. Complements :func:`_summarize_ad`,
    whose description is truncated."""
    widgets = detail.get("widgets", [])
    tms = detail.get("taggingData", {}).get("tmsDataValues", {}).get("tmsData", {})

    images = _widget(widgets, "PICTURE_SLIDER").get("advertImageList", [])
    title_price = _widget(widgets, "TITLE_WITH_PRICE")
    key_values = _widget(widgets, "KEY_VALUE_PAIRS_LIST").get("keyValuePairsList", [])
    # Several PARAGRAPHED_TEXT widgets exist (e.g. the "Privatperson" label);
    # the real description is the one with the longest body.
    descriptions = [w.get("teaser", "") for w in widgets if w.get("type") == "PARAGRAPHED_TEXT"]
    description = max(descriptions, key=len) if descriptions else None

    attributes = {kv.get("name"): kv.get("value") for kv in key_values}
    title = detail.get("description")
    status, reserved = _ad_status(title, detail)
    # "Übergabe" is the only place shipping vs pickup is stated, and it is not
    # in the search response -- paylivery is a payment method, not an answer.
    handover = [h.strip() for h in str(attributes.get("Übergabe") or "").split(",") if h.strip()]
    exact_price = tms.get("exact_price")
    return {
        "id": detail.get("adId"),
        "title": title,
        "description": description or None,
        "price": title_price.get("formattedPrice"),
        "price_amount": _to_float(exact_price),
        "status": status,
        "reserved": reserved,
        "condition": attributes.get("Zustand"),
        "handover": handover,
        "ships": any("versand" in h.lower() for h in handover),
        "pickup_only": bool(handover) and not any("versand" in h.lower() for h in handover),
        "seller": "privat" if tms.get("is_private") == "true" else "haendler",
        "seller_name": tms.get("seller_name"),
        "paylivery": detail.get("payliveryEnabled"),
        "location": tms.get("region_level_3"),
        "postcode": tms.get("post_code"),
        "state": tms.get("region_level_2"),
        "category": [kv.get("categoryName") for kv in
                     _widget(widgets, "CATEGORIES").get("categoryPath", {}).get("categoryEntryList", [])],
        "attributes": attributes,
        "published": tms.get("publish_date"),
        "delivery": title_price.get("deliveryCosts"),
        "image_urls": [img.get("referenceImageUrl") for img in images],
    }


# A keyword or exclude filter is applied here, not by willhaben, so pages are
# pulled until enough matches are collected -- bounded, to stay polite.
FILTER_MAX_SCAN = 250
# Whether a seller ships is only in the ad detail, so a shipping filter costs
# one detail request per candidate. Hard cap so a search can't turn into a crawl.
MAX_DETAIL_LOOKUPS = 40

Handover = Literal["versand", "abholung"]


async def _handover(ad_id) -> Optional[list[str]]:
    """The ad's "Übergabe" values, or None if the detail could not be read."""
    try:
        detail = await _fetch_detail(ad_id)
    except (httpx.HTTPError, RuntimeError):
        return None
    pairs = _widget(detail.get("widgets", []), "KEY_VALUE_PAIRS_LIST").get("keyValuePairsList", [])
    value = next((kv.get("value") for kv in pairs if kv.get("name") == "Übergabe"), "")
    return [h.strip() for h in str(value or "").split(",") if h.strip()]


def _rows(rows: int) -> int:
    """Validate a page size rather than quietly clamping 0 up to 1."""
    rows = int(rows)
    if rows < 1:
        raise ValueError(f"rows must be at least 1 (got {rows}).")
    return min(rows, MAX_ROWS)


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


@mcp.tool()
@_tool_errors("search")
async def search_willhaben(
    keyword: Optional[str] = None,
    *,
    category: Optional[Union[int, str]] = None,
    condition: Optional[Union[Condition, list[Condition]]] = None,
    clothing_size: Optional[Union[str, list[str]]] = None,
    shoe_size: Optional[Union[str, list[str]]] = None,
    color: Optional[Union[Color, list[Color]]] = None,
    pattern: Optional[Union[Pattern, list[Pattern]]] = None,
    brand: Optional[Union[int, str, list[Union[int, str]]]] = None,
    sort_by: SortBy = "actuality",
    area: Optional[Union[Bundesland, list[Bundesland]]] = None,
    seller: Optional[Union[Seller, list[Seller]]] = None,
    price_from: Optional[float] = None,
    price_to: Optional[float] = None,
    paylivery: Optional[bool] = None,
    title_only: bool = False,
    exclude: Optional[list[str]] = None,
    hide_reserved: bool = False,
    handover: Optional[Handover] = None,
    last_48h: bool = False,
    rows: int = 4,
    offset: int = 0,
) -> dict:
    """Search willhaben.at marketplace listings, with optional filters for
    category, condition, clothing/shoe size, colour, pattern, brand, region,
    seller, price and recency.

    At least one of ``keyword`` or ``category`` must be given.

    When you're after a specific kind of product, prefer scoping the search to
    its category: find it with ``list_categories`` and pass it as ``category``.
    A bare keyword search matches the whole ad text, so it also drags in loosely
    related listings (accessories, bundles, spare parts, other product types) --
    scoping to the category cuts that noise and lets you add the matching
    attribute filters. Keep it keyword-only for broad or one-off queries.

    - ``category``: a willhaben category id (int) or its exact name. Use the
      ``list_categories`` tool to discover ids. Restricts the search to that
      category and all its subcategories.
    - ``condition``: one or more of "neu", "neuwertig", "generalueberholt",
      "gebraucht", "defekt", "ausstellungsstueck".
    - ``clothing_size``: e.g. "38", "40", "46 / S", "48 / M", "ab 58"
      (only meaningful inside clothing categories).
    - ``shoe_size``: e.g. "38", "42", "45", "bis 15", "ab 48"
      (only meaningful inside shoe categories).
    - ``color``: one or more of the 16 willhaben colours (e.g. "schwarz", "blau").
    - ``pattern``: one or more of the 14 willhaben patterns (e.g. "gestreift",
      "floral"). Colour/pattern mostly apply to fashion & home categories.

    To filter by brand, first resolve it with ``search_brands`` and pass the
    returned id(s) via the ``brand`` parameter.

    willhaben matches ``keyword`` against the **whole ad text**, so a search for
    "RTX 4070" also returns mounting brackets, cables and ads that merely
    mention the card -- and with ``sort_by="price_asc"`` exactly that junk takes
    the top spots. Two filters fix it, both applied here over the ads willhaben
    returns:

    - ``title_only=True``: keep only ads whose **title** contains every word of
      ``keyword`` ("RTX4070" and "RTX 4070" match each other).
    - ``exclude``: drop ads whose title contains any of these words, e.g.
      ``["verpackung", "ovp", "halterung", "kabel"]``. Title only, so an ad that
      just mentions the word in its description is kept.

    Use them whenever you are hunting for a specific product. Because they run
    after the fetch, the tool pages through willhaben until it has ``rows``
    matches or has scanned 250 ads; ``scanned`` reports how far it got.

    ``title_only`` does not remove accessories and spare parts: a case, a
    mounting bracket or a replacement back cover legitimately carries the
    product's exact name in its own title. Two things do, and they compose:

    - ``price_from``. An accessory costs a fraction of the item, so a floor at
      roughly 15-20% of the product's real price clears out nearly all of them
      in one move, without you having to guess any vocabulary. Geizhals'
      ``get_model_price_range`` gives you that number; a listing below that
      floor is virtually never the product itself.
    - ``exclude`` with words picked for *this* search -- "huelle", "case",
      "rahmen", "backcover" for a phone; "halterung", "kabel", "wasserkuehler"
      for a graphics card; and "kein" to catch titles that name the product
      only to say they are not it ("SOYES XS15 Pro - kein iPhone 16 Pro").

    Every result carries ``status`` ("active" / "reserved" / "sold") and a
    ``reserved`` flag, read out of the title -- willhaben has no field for it
    and always reports an ad as active. ``hide_reserved=True`` drops them.
    Paging: ``next_offset`` is where to continue and ``has_more`` says whether
    anything is left, so you do not have to guess when the catalogue is
    exhausted.

    Whether a seller ships is **not** in willhaben's search response at all
    (``paylivery`` is a payment method, not shipping), so ``handover="versand"``
    or ``"abholung"`` costs one extra detail request per candidate that got
    past the other filters, capped at 40 per search. Use it when shipping is
    part of the requirement ("anywhere in Austria as long as they post it");
    results then carry a ``handover`` list and ``ships`` / ``pickup_only``, and
    ``detail_lookups`` reports what it cost. For a handful of ads you already
    have, ``get_ad_detail`` is cheaper.
    """
    if not keyword and category is None:
        raise ValueError("Provide 'keyword' and/or 'category'.")
    _check_ranges(("price", price_from, price_to))
    rows = _rows(rows)
    wanted = _tokens(keyword) if (title_only and keyword) else set()
    excluded = [_fold(term) for term in (exclude or []) if str(term).strip()]
    post_filtered = bool(wanted or excluded or hide_reserved or handover)

    params: dict = {
        "sfId": str(uuid.uuid4()),
        "isLog": "true",
        "rows": rows,
        "sort": sort[sort_by],
        "offset": offset,
    }

    if keyword:
        params["keyword"] = keyword

    if category is not None:
        params["ATTRIBUTE_TREE"] = _resolve_category(category)

    # condition + sizes all share the multi-valued treeAttributes parameter
    tree_attrs: list[str] = []
    if condition is not None:
        conds = condition if isinstance(condition, list) else [condition]
        tree_attrs += [condition_id[c] for c in conds]
    tree_attrs += _resolve_sizes(clothing_size, clothing_size_id, "clothing size")
    tree_attrs += _resolve_sizes(shoe_size, shoe_size_id, "shoe size")
    if color is not None:
        cols = color if isinstance(color, list) else [color]
        tree_attrs += [color_id[c] for c in cols]
    if pattern is not None:
        pats = pattern if isinstance(pattern, list) else [pattern]
        tree_attrs += [pattern_id[p] for p in pats]
    if brand is not None:
        brands = brand if isinstance(brand, list) else [brand]
        tree_attrs += [str(b).strip() for b in brands]
    if tree_attrs:
        params["treeAttributes"] = tree_attrs

    if area is not None:
        params["areaId"] = (
            [area_id[a] for a in area] if isinstance(area, list) else area_id[area]
        )

    if seller is not None:
        params["ISPRIVATE"] = (
            [verkaeufer[s] for s in seller] if isinstance(seller, list) else verkaeufer[seller]
        )

    if price_from is not None:
        params["PRICE_FROM"] = price_from

    if price_to is not None:
        params["PRICE_TO"] = price_to

    if paylivery is not None:
        params["paylivery"] = "true" if paylivery else "false"

    if last_48h:
        params["periode"] = 2

    headers = {
        "x-wh-client": "api@tailored-apps.com;willhabenapp;android;8.57.0;responsive_app",
        "x-wh-visitor-id": str(uuid.uuid4()),
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    def keep(ad: dict) -> bool:
        title = _fold(ad["title"] or "")
        if wanted and not wanted <= _tokens(title):
            return False
        if any(term in title for term in excluded):
            return False
        return not (hide_reserved and ad["status"] != "active")

    results: list = []
    cursor, scanned, lookups, rows_found = offset, 0, 0, None
    while True:
        params["rows"] = MAX_ROWS if post_filtered else rows
        params["offset"] = cursor
        logger.debug("Requesting: '%s'\nWith parameters: %s", BASE_URL, params)
        response = await _get(BASE_URL, params=params, headers=headers)
        logger.debug("Response: %d", response.status_code)
        data = response.json()
        rows_found = data.get("rowsFound")
        ads = data.get("advertSummaryList", {}).get("advertSummary", [])
        for ad in ads:
            cursor += 1
            scanned += 1
            summary = _summarize_ad(ad)
            if not keep(summary):
                continue
            # the expensive check runs last, only on ads everything else kept
            if handover:
                if lookups >= MAX_DETAIL_LOOKUPS:
                    break
                lookups += 1
                offered = await _handover(summary["id"])
                if offered is None:
                    continue
                folded = _fold(" ".join(offered))
                ships = "versand" in folded
                # "abholung" asks for pickup to be on offer, not for it to be
                # the only option -- most ads offer both.
                if not (ships if handover == "versand" else "abhol" in folded):
                    continue
                summary.update({"handover": offered, "ships": ships, "pickup_only": not ships})
            results.append(summary)
            if len(results) >= rows:
                break
        if not post_filtered or len(results) >= rows or not ads:
            break
        if scanned >= FILTER_MAX_SCAN or (rows_found is not None and cursor >= rows_found):
            break
        if handover and lookups >= MAX_DETAIL_LOOKUPS:
            break

    result = {**_pagination(rows_found, cursor, len(results)), "results": results}
    if post_filtered:
        result["scanned"] = scanned
    if handover:
        result["detail_lookups"] = lookups
    return result


@mcp.tool()
@_tool_errors("ad")
async def get_ad_detail(ad_id: Union[str, int]) -> dict:
    """Fetch the full detail of a single willhaben advert by its id, including
    the complete (untruncated) description, all images, itemised attributes and
    precise location. Use it after a search to inspect a listing.

    Search results only carry a truncated description, so come here whenever you
    need the full text, the complete attributes, or every detail of one ad (and
    ``get_ad_images`` when you need to see it).

    This is also the only place shipping is stated: ``handover`` lists what the
    seller offers ("Selbstabholung", "Versand"), with ``ships`` and
    ``pickup_only`` as the ready-made booleans -- ``paylivery`` in the search is
    a payment method and says nothing about delivery. ``status`` /
    ``reserved`` come from the title marker, the only place willhaben records
    that an item is already spoken for."""
    summary = _summarize_ad_detail(await _fetch_detail(ad_id))
    # the detail API truncates long descriptions near 600 chars; recover the full
    # text from the web page only when it looks cut off.
    current = summary.get("description") or ""
    if len(current) >= DESCRIPTION_CAP:
        full = await _fetch_full_description(ad_id)
        if full and len(full) > len(current):
            summary["description"] = full
    return summary


_CONTENT_TYPE_FORMAT = {
    "image/webp": "webp",
    "image/jpeg": "jpeg",
    "image/jpg": "jpeg",
    "image/png": "png",
    "image/gif": "gif",
}


async def _authed_get(url: str, *, params: Optional[dict] = None) -> httpx.Response:
    """GET a token-gated willhaben endpoint (detail, seller, dealer), refreshing
    the server-issued app token once on a 401 (it expires)."""
    for attempt in range(2):
        headers = {
            "Accept": "application/json",
            "x-wh-client": WH_CLIENT,
            "x-wh-date": datetime.now(timezone.utc).isoformat(),
            "x-wh-security-version": WH_SECURITY_VERSION,
            "x-wh-application-token": await _get_application_token(force_refresh=attempt > 0),
        }
        try:
            return await _get(url, params=params, headers=headers)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 401 and attempt == 0:
                logger.info("app token expired -- refreshing and retrying")
                continue
            raise
    raise RuntimeError("unreachable")  # pragma: no cover


async def _fetch_detail(ad_id: Union[str, int]) -> dict:
    """GET the raw advert-detail JSON (shared by detail/image/seller tools)."""
    return (await _authed_get(f"{DETAIL_URL}/{ad_id}")).json()


DESCRIPTION_CAP = 580  # the mobile detail API truncates the description near 600 chars


async def _fetch_full_description(ad_id: Union[str, int]) -> Optional[str]:
    """The mobile detail API caps the description at ~600 chars. Pull the full,
    clean text from the ad's web page (embedded __NEXT_DATA__); no token needed."""
    try:
        response = await client.get(
            "https://www.willhaben.at/iad/object",
            params={"adId": str(ad_id)},
            headers={"user-agent": "Mozilla/5.0", "Accept": "text/html"},
            follow_redirects=True,
        )
        response.raise_for_status()
    except httpx.HTTPError:
        return None
    match = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', response.text, re.S)
    if not match:
        return None
    try:
        details = json.loads(match.group(1))["props"]["pageProps"]["advertDetails"]
    except (ValueError, KeyError, TypeError):
        return None
    by_name = {a.get("name"): a.get("values") or [] for a in details.get("attributes", {}).get("attribute", [])}
    values = by_name.get("DESCRIPTION") or by_name.get("BODY_DYN")
    if not values:
        return None
    text = re.sub(r"<br\s*/?>", "\n", values[0], flags=re.I)
    return html.unescape(_TAG_RE.sub("", text)).strip()


@mcp.tool(structured_output=False)
async def get_ad_images(ad_id: Union[str, int], max_images: int = 4) -> list:
    """Fetch an ad's photos and return them as images you can actually look at.

    Downloads up to ``max_images`` photos from willhaben's CDN server-side and
    returns them as image content blocks (base64), so a vision-capable client
    sees the real pictures instead of just URLs. Call it with an id from a search.

    Reach for this whenever the pictures carry information you can't take on trust
    from the text: the description is the seller's claim, the photos are the
    evidence. Typical cases are judging the real condition (wear, scratches,
    damage, completeness), reading details that only appear in an image (a
    screenshot of specs or measurements, a label, a model or serial number, a
    display), or confirming the item matches the description. As a rule of thumb,
    if your answer or recommendation depends on what the thing actually looks
    like, look at it.
    """
    max_images = max(1, min(int(max_images), 10))
    try:
        detail = await _fetch_detail(ad_id)
    except httpx.HTTPStatusError as exc:
        return [_upstream_message(exc.response.status_code, "ad")]
    except httpx.HTTPError as exc:
        return [f"could not reach willhaben for ad {ad_id} ({type(exc).__name__})"]
    images = _widget(detail.get("widgets", []), "PICTURE_SLIDER").get("advertImageList", [])
    urls = [img.get("referenceImageUrl") for img in images if img.get("referenceImageUrl")]
    urls = urls[:max_images]
    if not urls:
        return [f"No images found for ad {ad_id}."]

    img_headers = {"user-agent": "willhaben/613937 okhttp/5.4.0 Android/14"}

    async def fetch(u: str):
        try:
            r = await client.get(u, headers=img_headers)
            r.raise_for_status()
            ctype = r.headers.get("content-type", "").split(";")[0].strip().lower()
            fmt = _CONTENT_TYPE_FORMAT.get(ctype, "jpeg")
            return Image(data=r.content, format=fmt)
        except Exception as exc:  # a single broken image shouldn't fail the tool
            logger.warning("image download failed (%s): %s", u, exc)
            return None

    fetched = await asyncio.gather(*(fetch(u) for u in urls))
    pictures = [img for img in fetched if img is not None]

    title = detail.get("description") or f"ad {ad_id}"
    header = f'{len(pictures)} image(s) for "{title}" (ad {ad_id}):'
    return [header, *pictures]


SELLER_TRUST_URL = "https://publicapi.willhaben.at/userprofile/trust-signals"
SELLER_PROFILE_URL = "https://ad-search.willhaben.at/restapi/v2/sellerprofile"
DEALER_PROFILE_URL = "https://api.willhaben.at/restapi/v2/dealerprofile"


@mcp.tool()
@_tool_errors("seller")
async def get_ad_seller(ad_id: Union[str, int]) -> dict:
    """Look up the seller behind an ad: name, private vs dealer, rating and reply
    time (private sellers), the member-since / created date, and location.

    Use it for a plausibility or trust check the ad itself does not answer -- how
    long the account has existed, how it is rated, whether it is a dealer.
    """
    detail = await _fetch_detail(ad_id)
    tms = detail.get("taggingData", {}).get("tmsDataValues", {}).get("tmsData", {})
    seller_id = tms.get("seller_id")
    name = tms.get("seller_name")
    if not seller_id:
        return {"error": "no seller id on this ad", "name": name}

    if tms.get("is_private") == "true":
        result: dict = {"id": seller_id, "name": name, "type": "private"}
        try:  # rating / trust signals (no token needed)
            trust = (await _get(f"{SELLER_TRUST_URL}/{seller_id}")).json()
            result.update({
                "rating": trust.get("averageRating"),
                "rating_count": trust.get("numberOfRatings"),
                "rating_note": trust.get("numberOfRatingsText"),
                "reply_time": trust.get("replyTime"),
            })
        except httpx.HTTPError:
            pass
        try:  # profile (member since); token-gated
            sp = (await _authed_get(f"{SELLER_PROFILE_URL}/{seller_id}/5/profile")).json().get("sellerProfile", {})
            result["member_since"] = sp.get("registerDate")
            result["active_ads"] = sp.get("activeAdCount")
            result["location"] = sp.get("location")
        except httpx.HTTPError:
            pass
        return result

    result = {"id": seller_id, "name": name, "type": "dealer"}
    try:  # dealer / organisation; token-gated
        org = (await _authed_get(f"{DEALER_PROFILE_URL}/{seller_id}")).json().get("organisation", {})
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


@mcp.tool()
async def list_categories(
    query: Optional[str] = None,
    parent_id: Optional[int] = None,
    limit: int = 50,
) -> dict:
    """Discover willhaben category ids to use as ``category`` in search_willhaben.

    - ``query``: case-insensitive substring match on the category name
      (searches the whole tree).
    - ``parent_id``: list the direct child categories of this id. Omit both
      ``query`` and ``parent_id`` to get the top-level categories.

    Returns ``{count, categories: [{id, label, parent_id, path, has_children}]}``.
    ``has_children`` tells you whether you can drill further with ``parent_id``.
    """
    if query:
        q = _norm(query)
        ids = [cid for cid, lab in CATEGORY_LABEL.items() if q in _norm(lab)]
    else:
        ids = CATEGORY_CHILDREN.get(parent_id, [])

    ids.sort(key=lambda i: CATEGORY_PATH[i])
    results = [
        {
            "id": cid,
            "label": CATEGORY_LABEL[cid],
            "parent_id": CATEGORY_PARENT[cid],
            "path": CATEGORY_PATH[cid],
            "has_children": bool(CATEGORY_CHILDREN.get(cid)),
        }
        for cid in ids[:limit]
    ]
    return {"count": len(ids), "returned": len(results), "categories": results}


_TAG_RE = re.compile(r"<[^>]+>")


@mcp.tool()
@_tool_errors("brand")
async def search_brands(
    category: Union[int, str],
    term: str = "",
    limit: int = 40,
) -> dict:
    """Look up brand ids for a category, for use as ``brand`` in search_willhaben.

    Brands are category-specific and there can be over a thousand per category,
    so this is a type-ahead: pass a ``term`` (e.g. "nik") to narrow it down.

    - ``category``: category id or exact name (as in ``list_categories``). Use a
      specific (leaf) category -- broad top categories may have no brand filter.
    - ``term``: optional search string for the brand name.

    Returns ``{count, brands: [{id, label}]}``. Pass the ``id`` back as
    ``brand`` to search_willhaben. Brand ids are strings, category ids ints --
    both parameters accept either.
    """
    catid = _resolve_category(category)
    params = {
        "categoryId": catid,
        "attributeXmlCode": "BRAND",
        "navigatorId": "Marke",
        "term": term or "",
    }
    headers = {
        "x-wh-client": WH_CLIENT,
        "x-wh-visitor-id": str(uuid.uuid4()),
        "Accept": "application/json",
    }
    try:
        response = await client.get(ATTRIBUTE_OPTIONS_URL, params=params, headers=headers)
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
            label = _TAG_RE.sub("", opt.get("label", "")).strip()  # strip <b> highlight
            if label == "Andere Marken":
                continue
            seen.add(val)
            brands.append({"id": val, "label": label})
    return {"count": len(brands), "returned": min(len(brands), limit), "brands": brands[:limit]}


# ---------------------------------------------------------------------------
# Auto & Motor (cars / Gebrauchtwagen)
#
# The car vertical lives under `atz/3/2`. Navigation/filter metadata comes from
# app-aggregator, result lists from ad-search (same hosts as the marketplace).
# Enumerated filters (car type, fuel, ...) and the make list ship in
# data/auto-motor/filters.json; models are make-specific and fetched on demand.
# ---------------------------------------------------------------------------

AUTO_SEARCH_PATH = "atz/3/2"
AUTO_RESULTS_URL = f"https://ad-search.willhaben.at/restapi/v2/search/{AUTO_SEARCH_PATH}"
AUTO_NAV_URL = f"https://app-aggregator.willhaben.at/restapi/v2/search/{AUTO_SEARCH_PATH}"


def _load_auto_data() -> dict:
    path = Path(__file__).parent / "data" / "auto-motor" / "filters.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        logging.getLogger(__name__).warning("auto-motor filters.json not found -- car search disabled")
        return {"selects": {}, "ranges": {}, "toggles": {}, "makes": {}}


_AUTO = _load_auto_data()
CAR_MAKES: dict[int, str] = {int(k): v for k, v in _AUTO.get("makes", {}).items()}
_CAR_MAKE_BY_NAME: dict[str, list[int]] = {}
for _mid, _mlabel in CAR_MAKES.items():
    _CAR_MAKE_BY_NAME.setdefault(_norm(_mlabel), []).append(_mid)


def _resolve_car_make(make: Union[int, str]) -> str:
    """Resolve a car make given as id or (unique) name to its CAR_MODEL/MAKE id."""
    if isinstance(make, int) or (isinstance(make, str) and make.strip().isdigit()):
        mid = int(str(make).strip())
        if mid not in CAR_MAKES:
            raise ValueError(f"Unknown make id {mid}. Use list_car_makes.")
        return str(mid)
    matches = _CAR_MAKE_BY_NAME.get(_norm(make), [])
    if not matches:
        raise ValueError(f"Unknown make '{make}'. Use list_car_makes.")
    if len(matches) > 1:
        opts = ", ".join(f"{i} ({CAR_MAKES[i]})" for i in matches[:10])
        raise ValueError(f"Make '{make}' is ambiguous: {opts}. Please pass an id.")
    return str(matches[0])


def _resolve_car_select(value, qp: str, human: str) -> list[str]:
    """Map one or more labels/ids to ids for an enumerated car filter."""
    options = _AUTO.get("selects", {}).get(qp, {}).get("options", {})  # {id: label}
    by_norm = {_norm(l): i for i, l in options.items()}
    if value is None:
        return []
    values = value if isinstance(value, list) else [value]
    out = []
    for v in values:
        s = str(v).strip()
        if s in options:
            out.append(s)
        elif _norm(s) in by_norm:
            out.append(by_norm[_norm(s)])
        else:
            raise ValueError(f"Unknown {human} '{v}'. Options: {', '.join(options.values())}")
    return out


def _car_models_from_response(data: dict) -> dict:
    """Pull the CAR_MODEL/MODEL options out of a navigation response."""
    out: dict[str, str] = {}

    def collect(node):
        if isinstance(node, dict):
            v, l = node.get("value"), node.get("label")
            if v is not None and l is not None:
                out[str(v)] = l
            for child in node.values():
                collect(child)
        elif isinstance(node, list):
            for child in node:
                collect(child)

    def find(node):
        if isinstance(node, dict):
            if node.get("queryParameterName") == "CAR_MODEL/MODEL":
                collect(node)
            for child in node.values():
                find(child)
        elif isinstance(node, list):
            for child in node:
                find(child)

    find(data.get("filterContainer", {}))
    return out


async def _fetch_car_models(make_id: str) -> dict:
    """Fetch a make's models ({id: label}) from the navigation endpoint."""
    params = {"isNavigation": "true", "CAR_MODEL/MAKE": make_id, "sfId": str(uuid.uuid4())}
    headers = {
        "x-wh-client": WH_CLIENT,
        "x-wh-visitor-id": str(uuid.uuid4()),
        "Accept": "application/json",
    }
    response = await _get(AUTO_NAV_URL, params=params, headers=headers)
    return _car_models_from_response(response.json())


async def _resolve_car_model(model: Union[int, str], make_id: Optional[str]) -> str:
    """Resolve a model given as id or name (within a make) to its CAR_MODEL/MODEL id."""
    s = str(model).strip()
    if s.isdigit():
        return s
    if make_id is None:
        raise ValueError("To search by model name, also pass 'make' (or use a model id).")
    models = await _fetch_car_models(make_id)  # {id: label}
    by_norm = {_norm(l): i for i, l in models.items()}
    mid = by_norm.get(_norm(s))
    if mid is None:  # fall back to a substring match ("3er" -> "3er-Reihe")
        cands = [(i, l) for i, l in models.items() if _norm(s) in _norm(l)]
        if len(cands) == 1:
            mid = cands[0][0]
        elif len(cands) > 1:
            opts = ", ".join(f"{l} ({i})" for i, l in cands[:10])
            raise ValueError(f"Model '{model}' is ambiguous: {opts}. Be more specific or pass an id.")
    if mid is None:
        raise ValueError(f"Unknown model '{model}' for this make. Use list_car_models.")
    return mid


KW_TO_PS = 1.35962


def _summarize_car(ad: dict) -> dict:
    """Like :func:`_summarize_ad`, plus the car-specific fields that matter."""
    result = _summarize_ad(ad)
    attr = _attributes_to_dict(ad)
    mileage = attr.get("MILEAGE")
    # ENGINE/EFFECT is kW, the same unit the power_from/power_to filters take --
    # ads and buyers talk in PS, so give both rather than one mislabelled number.
    power_kw = _to_float(attr.get("ENGINE/EFFECT"))
    result.update({
        "make": attr.get("CAR_MODEL/MAKE"),
        "model": attr.get("CAR_MODEL/MODEL"),
        "variant": attr.get("CAR_MODEL/MODEL_SPECIFICATION"),
        "year": attr.get("YEAR_MODEL"),
        "mileage_km": int(mileage) if mileage not in (None, "") else None,
        "fuel": attr.get("ENGINE/FUEL_RESOLVED"),
        "transmission": attr.get("TRANSMISSION_RESOLVED"),
        "power_kw": int(power_kw) if power_kw is not None else None,
        "power_ps": round(power_kw * KW_TO_PS) if power_kw is not None else None,
        "condition": attr.get("CONDITION_RESOLVED"),
        "owners": attr.get("NO_OF_OWNERS"),
    })
    return result


@mcp.tool()
@_tool_errors("car search")
async def search_autos(
    make: Optional[Union[int, str]] = None,
    model: Optional[Union[int, str]] = None,
    *,
    keyword: Optional[str] = None,
    car_type: Optional[Union[str, list[str]]] = None,
    fuel: Optional[Union[str, list[str]]] = None,
    transmission: Optional[Union[str, list[str]]] = None,
    wheel_drive: Optional[Union[str, list[str]]] = None,
    condition: Optional[Union[str, list[str]]] = None,
    color: Optional[Union[str, list[str]]] = None,
    dealer: Optional[Union[str, list[str]]] = None,
    equipment: Optional[Union[str, list[str]]] = None,
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
    sort_by: SortBy = "actuality",
    area: Optional[Union[Bundesland, list[Bundesland]]] = None,
    last_48h: bool = False,
    rows: int = 4,
    offset: int = 0,
) -> dict:
    """Search willhaben's Auto & Motor cars (Gebrauchtwagen).

    Everything is optional; combine what you need.

    Prefer the structured filters over ``keyword`` when a filter exists for what
    you want: they match exact fields, while ``keyword`` searches the free text
    and pulls in ads that merely mention a term. Use ``keyword`` for things that
    have no filter, such as a chassis-generation code (e.g. "E39", where make and
    model only go down to "5er-Reihe") or a specific equipment phrase, ideally on
    top of a structured make/model search rather than on its own.

    - ``make``: make id or name (see ``list_car_makes``).
    - ``model``: model id or name, e.g. "3er-Reihe" or "7er" (needs ``make``;
      resolved for you). See ``list_car_models`` for the exact names.
    - ``car_type``: Cabrio / Roadster, Klein-/ Kompaktwagen, Kleinbus,
      Kombi / Family Van, Limousine, Mopedauto, Sportwagen / Coupé,
      SUV / Geländewagen.
    - ``fuel``: Benzin, Diesel, Elektro, Gas, Hybrid Elektro/Benzin,
      Hybrid Elektro/Diesel, Wasserstoff.
    - ``transmission``: Automatik, Schaltgetriebe.
    - ``wheel_drive``: Allrad, Hinterrad, Vorderrad.
    - ``condition``: Gebrauchtwagen, Jahreswagen, Neuwagen, Oldtimer,
      Tageszulassung, Unfallwagen, Vorführwagen.
    - ``color``: exterior colour (Schwarz, Weiß, ...).
    - ``dealer``: Händler or Privat.
    - ``equipment``: one or more equipment features (e.g. "Sitzheizung vorne").
    - ranges: ``price_from/to`` (EUR), ``year_from/to`` (first registration),
      ``mileage_from/to`` (km), ``power_from/to`` (**kW**, not PS -- 150 PS is
      110 kW).
    - ``warranty`` / ``condition_report`` (Pickerl §57a) toggles.

    Names and ids for the enumerated filters are accepted interchangeably, and
    an inverted range (``price_from`` above ``price_to``) is rejected rather
    than silently ignored by willhaben.

    Each result gives engine power as both ``power_kw`` and ``power_ps``, so a
    comparison against the "150 PS" in an ad title and against the kW-based
    ``power_from``/``power_to`` filters both work without converting anything.
    Paging: ``next_offset`` and ``has_more`` tell you when the list is
    exhausted.
    """
    _check_ranges(("price", price_from, price_to), ("year", year_from, year_to),
                  ("mileage", mileage_from, mileage_to), ("power", power_from, power_to))
    rows = _rows(rows)
    params: dict = {
        "sfId": str(uuid.uuid4()),
        "isLog": "true",
        "rows": rows,
        "offset": offset,
        "sort": sort[sort_by],
    }
    if keyword:
        params["keyword"] = keyword
    if make is not None:
        params["CAR_MODEL/MAKE"] = _resolve_car_make(make)
    if model is not None:
        params["CAR_MODEL/MODEL"] = await _resolve_car_model(model, params.get("CAR_MODEL/MAKE"))

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
        ids = _resolve_car_select(value, qp, human)
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
    if area is not None:
        params["areaId"] = (
            [area_id[a] for a in area] if isinstance(area, list) else area_id[area]
        )

    headers = {
        "x-wh-client": WH_CLIENT,
        "x-wh-visitor-id": str(uuid.uuid4()),
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    logger.debug("Requesting autos: %s params=%s", AUTO_RESULTS_URL, params)
    response = await _get(AUTO_RESULTS_URL, params=params, headers=headers)
    data = response.json()
    ads = data.get("advertSummaryList", {}).get("advertSummary", [])
    return {**_pagination(data.get("rowsFound"), offset + len(ads), len(ads)),
            "results": [_summarize_car(ad) for ad in ads]}


@mcp.tool()
async def list_car_makes(query: Optional[str] = None, limit: int = 60) -> dict:
    """List car make ids for ``search_autos``. Optional ``query`` filters by name."""
    if query:
        q = _norm(query)
        ids = [i for i, name in CAR_MAKES.items() if q in _norm(name)]
    else:
        ids = list(CAR_MAKES)
    ids.sort(key=lambda i: CAR_MAKES[i].lower())
    makes = [{"id": i, "label": CAR_MAKES[i]} for i in ids[:limit]]
    return {"count": len(ids), "returned": len(makes), "makes": makes}


@mcp.tool()
async def list_car_models(make: Union[int, str]) -> dict:
    """List the models of a car make, for the ``model`` filter of ``search_autos``.

    ``make`` is a make id or name (see ``list_car_makes``).
    """
    make_id = _resolve_car_make(make)
    models = await _fetch_car_models(make_id)
    items = [{"id": v, "label": l} for v, l in models.items()]
    return {"make": CAR_MAKES.get(int(make_id)), "count": len(items), "models": items}


# ---------------------------------------------------------------------------
# Immobilien (real estate)
#
# The real-estate vertical lives under ``atz/2/<propertyTypeId>``: each property
# type (rent flat, buy house, plot, ...) is its own category id in the path, and
# the available filters differ per type. Filter options, ranges and the property
# types ship in data/immobilien/filters.json.
# ---------------------------------------------------------------------------

IMMO_SEARCH_PREFIX = "atz/2"
IMMO_RESULTS_BASE = "https://ad-search.willhaben.at/restapi/v2/search"

_ROOM_BUCKETS = {
    "1": "1X1", "2": "2X2", "3": "3X3", "4": "4X4", "5": "5X5",
    "6-9": "6X9", "10+": "10X",
}


def _load_immo_data() -> dict:
    path = Path(__file__).parent / "data" / "immobilien" / "filters.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        logging.getLogger(__name__).warning("immobilien filters.json not found -- property search disabled")
        return {"categories": {}, "selects": {}, "ranges": {}}


_IMMO = _load_immo_data()
IMMO_TYPES: dict[int, str] = {int(k): v for k, v in _IMMO.get("categories", {}).items()}
_IMMO_TYPE_BY_NAME: dict[str, list[int]] = {}
for _tid, _tlabel in IMMO_TYPES.items():
    _IMMO_TYPE_BY_NAME.setdefault(_norm(_tlabel), []).append(_tid)


def _resolve_immo_type(property_type: Union[int, str]) -> str:
    """Resolve a property type given as id or name to its atz/2/<id> category id."""
    if isinstance(property_type, int) or (isinstance(property_type, str) and property_type.strip().isdigit()):
        tid = int(str(property_type).strip())
        if tid not in IMMO_TYPES:
            raise ValueError(f"Unknown property-type id {tid}. Use list_immobilien_types.")
        return str(tid)
    matches = _IMMO_TYPE_BY_NAME.get(_norm(property_type), [])
    if not matches:
        raise ValueError(f"Unknown property type '{property_type}'. Use list_immobilien_types.")
    if len(matches) > 1:
        opts = ", ".join(f"{i} ({IMMO_TYPES[i]})" for i in matches[:10])
        raise ValueError(f"Property type '{property_type}' is ambiguous: {opts}. Please pass an id.")
    return str(matches[0])


def _resolve_options(value, options: dict, human: str) -> list[str]:
    """Map one or more labels/ids to ids for an enumerated filter ({id: label})."""
    by_norm = {_norm(l): i for i, l in options.items()}
    if value is None:
        return []
    values = value if isinstance(value, list) else [value]
    out = []
    for v in values:
        s = str(v).strip()
        if s in options:
            out.append(s)
        elif _norm(s) in by_norm:
            out.append(by_norm[_norm(s)])
        else:
            raise ValueError(f"Unknown {human} '{v}'. Options: {', '.join(options.values())}")
    return out


def _immo_options(qp: str) -> dict:
    return _IMMO.get("selects", {}).get(qp, {}).get("options", {})


def _resolve_rooms(rooms) -> list[str]:
    if rooms is None:
        return []
    values = rooms if isinstance(rooms, list) else [rooms]
    buckets = set(_ROOM_BUCKETS.values())
    out = []
    for v in values:
        s = str(v).strip()
        if s in buckets:
            out.append(s)
        elif s in _ROOM_BUCKETS:
            out.append(_ROOM_BUCKETS[s])
        else:
            raise ValueError(f"Unknown rooms value '{v}'. Options: {', '.join(_ROOM_BUCKETS)}")
    return out


def _summarize_immo(ad: dict) -> dict:
    """Like :func:`_summarize_ad`, plus the property-specific fields."""
    result = _summarize_ad(ad)
    attr = _attributes_to_dict(ad)
    living = _to_float(attr.get("ESTATE_SIZE/LIVING_AREA") or attr.get("ESTATE_SIZE"))
    price_per_m2 = _to_float(attr.get("PRICE/SQUARE_METER"))
    if price_per_m2 is None and living and result["price_amount"]:
        price_per_m2 = round(result["price_amount"] / living, 2)
    result.update({
        "property_type": attr.get("PROPERTY_TYPE"),
        "living_area_m2": living,
        "plot_area_m2": _to_float(attr.get("PLOT/AREA")),
        "rooms": attr.get("NUMBER_OF_ROOMS") or attr.get("NO_OF_ROOMS"),
        "price_per_m2": round(price_per_m2, 2) if price_per_m2 is not None else None,
        "floor": attr.get("FLOOR"),
        "district": attr.get("DISTRICT"),
        "address": attr.get("ADDRESS"),
    })
    return result


@mcp.tool()
@_tool_errors("property search")
async def search_immobilien(
    property_type: Union[int, str] = "Alle Immobilien",
    *,
    keyword: Optional[str] = None,
    object_type: Optional[Union[str, list[str]]] = None,
    rooms: Optional[Union[str, int, list]] = None,
    features: Optional[Union[str, list[str]]] = None,
    outdoor: Optional[Union[str, list[str]]] = None,
    price_from: Optional[float] = None,
    price_to: Optional[float] = None,
    area_from: Optional[int] = None,
    area_to: Optional[int] = None,
    plot_from: Optional[int] = None,
    plot_to: Optional[int] = None,
    sort_by: SortBy = "actuality",
    area: Optional[Union[Bundesland, list[Bundesland]]] = None,
    last_48h: bool = False,
    rows: int = 4,
    offset: int = 0,
) -> dict:
    """Search willhaben real estate (Immobilien).

    Pick a ``property_type`` first (it selects buy vs rent and the kind of
    property); everything else is optional. Use ``list_immobilien_types`` for the
    available types, e.g. "Wohnung mieten", "Haus kaufen", "Grundstücke",
    "Gewerbeimmobilie mieten", or "Alle Immobilien" (the default).

    - ``object_type``: a specific sub-type (PROPERTY_TYPE), e.g. "Einfamilienhaus",
      "Dachgeschosswohnung", "Villa".
    - ``rooms``: "1"–"5", "6-9" or "10+" (number of rooms).
    - ``features``: fittings like "Garage", "Keller", "Einbauküche", "Fahrstuhl".
    - ``outdoor``: "Balkon", "Terrasse", "Garten", "Loggia", "Dachterrasse", ...
    - ranges: ``price_from/to`` (EUR, rent or purchase depending on the type),
      ``area_from/to`` (living area m²), ``plot_from/to`` (plot area m²).
    - ``area``: federal state (region).

    Filters that don't apply to the chosen type are ignored by willhaben, but an
    inverted range (``price_from`` above ``price_to``) is rejected here rather
    than silently dropped.

    Results carry the numeric ``price_amount`` and ``price_per_m2`` next to the
    formatted ``price``, so listings can be sorted and compared without parsing
    the string, plus ``living_area_m2`` / ``plot_area_m2`` / ``rooms`` /
    ``floor`` / ``district`` / ``address``. Paging: ``next_offset`` and
    ``has_more`` tell you when the list is exhausted.
    """
    catid = _resolve_immo_type(property_type)
    _check_ranges(("price", price_from, price_to), ("area", area_from, area_to),
                  ("plot", plot_from, plot_to))
    rows = _rows(rows)
    params: dict = {
        "sfId": str(uuid.uuid4()),
        "isLog": "true",
        "rows": rows,
        "offset": offset,
        "sort": sort[sort_by],
    }
    if keyword:
        params["keyword"] = keyword

    object_ids = _resolve_options(object_type, _immo_options("PROPERTY_TYPE"), "object type")
    if object_ids:
        params["PROPERTY_TYPE"] = object_ids
    feature_ids = _resolve_options(features, _immo_options("ESTATE_PREFERENCE"), "feature")
    if feature_ids:
        params["ESTATE_PREFERENCE"] = feature_ids
    outdoor_ids = _resolve_options(outdoor, _immo_options("FREE_AREA/FREE_AREA_TYPE"), "outdoor area")
    if outdoor_ids:
        params["FREE_AREA/FREE_AREA_TYPE"] = outdoor_ids
    room_ids = _resolve_rooms(rooms)
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
    if area is not None:
        params["areaId"] = (
            [area_id[a] for a in area] if isinstance(area, list) else area_id[area]
        )

    headers = {
        "x-wh-client": WH_CLIENT,
        "x-wh-visitor-id": str(uuid.uuid4()),
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    url = f"{IMMO_RESULTS_BASE}/{IMMO_SEARCH_PREFIX}/{catid}"
    logger.debug("Requesting immobilien: %s params=%s", url, params)
    response = await _get(url, params=params, headers=headers)
    data = response.json()
    ads = data.get("advertSummaryList", {}).get("advertSummary", [])
    return {
        "property_type": IMMO_TYPES.get(int(catid)),
        **_pagination(data.get("rowsFound"), offset + len(ads), len(ads)),
        "results": [_summarize_immo(ad) for ad in ads],
    }


@mcp.tool()
async def list_immobilien_types() -> dict:
    """List the willhaben real-estate property types for ``search_immobilien``."""
    types = [{"id": i, "label": IMMO_TYPES[i]} for i in sorted(IMMO_TYPES)]
    return {"count": len(types), "types": types}


if __name__ == "__main__":
    url = f"http://{HOST}:{PORT}{MCP_PATH}"
    logger.info("Starting willhaben-mcp server on %s", url)
    mcp.run("streamable-http", host=HOST, port=PORT, streamable_http_path=MCP_PATH)