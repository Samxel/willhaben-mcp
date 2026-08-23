from mcp.server import MCPServer
from mcp.server.mcpserver import Image
import httpx
import asyncio
import uuid
import json
import re
import unicodedata
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional, Union, Literal
import logging

BASE_URL = "https://ad-search.willhaben.at/restapi/v2/search/atz/seo/kaufen-und-verkaufen/marktplatz"
DETAIL_URL = "https://publicapi.willhaben.at/atdetail/v1"
ATTRIBUTE_OPTIONS_URL = "https://app-aggregator.willhaben.at/api/v1/search/attribute-options"

WH_CLIENT = "api@tailored-apps.com;willhabenapp;android;8.57.0;responsive_app"
# The detail API is gated by a static, app-level signing token (over HTTP/2).
# It is not bound to a user, visitor id or date -- only its presence is checked.
WH_SECURITY_VERSION = "20130527022532"
WH_APPLICATION_TOKEN = (
    "vUbhu3l/YLBUYicWNFRac/DbBJHKWh9rcP4UnOILHSe2tsCoQxZvldz2X7DlMRHwoR1Ol1dd1FMhm3YnWc60LUNy5x/E5d+24jJbqBFFAhA="
)

HOST = "127.0.0.1"
PORT = 8000
MCP_PATH = "/mcp"

mcp = MCPServer("willhaben-mcp")
# HTTP/2 is required by the detail API; the search API negotiates it too.
client = httpx.AsyncClient(http2=True)

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s %(levelname)s: %(message)s')

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


def _norm(s: str) -> str:
    """Normalise a label for lookup: lowercase, strip accents, drop all spaces."""
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode()
    return "".join(s.lower().split())


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


def _summarize_ad(ad: dict) -> dict:
    """Reduce a full willhaben advert to the handful of fields that matter
    for an AI response instead of returning the whole raw object."""
    attr = _attributes_to_dict(ad)
    seo_url = attr.get("SEO_URL")
    price_amount = attr.get("PRICE/AMOUNT")
    return {
        "id": ad.get("id"),
        "title": attr.get("HEADING") or ad.get("description"),
        "description": attr.get("BODY_DYN"),
        "price": attr.get("PRICE_FOR_DISPLAY"),
        "price_amount": float(price_amount) if price_amount is not None else None,
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

    exact_price = tms.get("exact_price")
    return {
        "id": detail.get("adId"),
        "title": detail.get("description"),
        "description": description or None,
        "price": title_price.get("formattedPrice"),
        "price_amount": float(exact_price) if exact_price is not None else None,
        "seller": "privat" if tms.get("is_private") == "true" else "haendler",
        "seller_name": tms.get("seller_name"),
        "paylivery": detail.get("payliveryEnabled"),
        "location": tms.get("region_level_3"),
        "postcode": tms.get("post_code"),
        "state": tms.get("region_level_2"),
        "category": [kv.get("categoryName") for kv in
                     _widget(widgets, "CATEGORIES").get("categoryPath", {}).get("categoryEntryList", [])],
        "attributes": {kv.get("name"): kv.get("value") for kv in key_values},
        "published": tms.get("publish_date"),
        "delivery": title_price.get("deliveryCosts"),
        "image_urls": [img.get("referenceImageUrl") for img in images],
    }


@mcp.tool()
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
    """
    if not keyword and category is None:
        raise ValueError("Provide 'keyword' and/or 'category'.")

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
    logger.debug("Requesting: '%s'\nWith parameters: %s", BASE_URL, params)
    response = await client.get(BASE_URL, params=params, headers=headers)
    logger.debug("Response: %d", response.status_code)
    response.raise_for_status()
    data = response.json()
    ads = data.get("advertSummaryList", {}).get("advertSummary", [])
    return {
        "rows_found": data.get("rowsFound"),
        "rows_returned": data.get("rowsReturned"),
        "results": [_summarize_ad(ad) for ad in ads],
    }


@mcp.tool()
async def get_ad_detail(ad_id: Union[str, int]) -> dict:
    """Fetch the full detail of a single willhaben advert by its id, including
    the complete (untruncated) description, all images, itemised attributes and
    precise location. Use it after a search to inspect a listing.

    Search results only carry a truncated description, so come here whenever you
    need the full text, the complete attributes, or every detail of one ad (and
    ``get_ad_images`` when you need to see it)."""
    url = f"{DETAIL_URL}/{ad_id}"
    headers = {
        "Accept": "application/json",
        "x-wh-client": WH_CLIENT,
        "x-wh-date": datetime.now(timezone.utc).isoformat(),
        "x-wh-security-version": WH_SECURITY_VERSION,
        "x-wh-application-token": WH_APPLICATION_TOKEN,
    }
    logger.debug("Requesting detail: '%s'", url)
    response = await client.get(url, headers=headers)
    logger.debug("Response: %d", response.status_code)
    response.raise_for_status()
    return _summarize_ad_detail(response.json())


_CONTENT_TYPE_FORMAT = {
    "image/webp": "webp",
    "image/jpeg": "jpeg",
    "image/jpg": "jpeg",
    "image/png": "png",
    "image/gif": "gif",
}


async def _fetch_detail(ad_id: Union[str, int]) -> dict:
    """GET the raw advert-detail JSON (shared by detail/image tools)."""
    headers = {
        "Accept": "application/json",
        "x-wh-client": WH_CLIENT,
        "x-wh-date": datetime.now(timezone.utc).isoformat(),
        "x-wh-security-version": WH_SECURITY_VERSION,
        "x-wh-application-token": WH_APPLICATION_TOKEN,
    }
    response = await client.get(f"{DETAIL_URL}/{ad_id}", headers=headers)
    response.raise_for_status()
    return response.json()


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
    detail = await _fetch_detail(ad_id)
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
    ``brand`` to search_willhaben.
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
    response = await client.get(AUTO_NAV_URL, params=params, headers=headers)
    response.raise_for_status()
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


def _summarize_car(ad: dict) -> dict:
    """Like :func:`_summarize_ad`, plus the car-specific fields that matter."""
    result = _summarize_ad(ad)
    attr = _attributes_to_dict(ad)
    mileage = attr.get("MILEAGE")
    result.update({
        "make": attr.get("CAR_MODEL/MAKE"),
        "model": attr.get("CAR_MODEL/MODEL"),
        "variant": attr.get("CAR_MODEL/MODEL_SPECIFICATION"),
        "year": attr.get("YEAR_MODEL"),
        "mileage_km": int(mileage) if mileage not in (None, "") else None,
        "fuel": attr.get("ENGINE/FUEL_RESOLVED"),
        "transmission": attr.get("TRANSMISSION_RESOLVED"),
        "power_ps": attr.get("ENGINE/EFFECT"),
        "condition": attr.get("CONDITION_RESOLVED"),
        "owners": attr.get("NO_OF_OWNERS"),
    })
    return result


@mcp.tool()
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
      ``mileage_from/to`` (km), ``power_from/to`` (kW).
    - ``warranty`` / ``condition_report`` (Pickerl §57a) toggles.

    Names and ids for the enumerated filters are accepted interchangeably.
    """
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
    response = await client.get(AUTO_RESULTS_URL, params=params, headers=headers)
    response.raise_for_status()
    data = response.json()
    ads = data.get("advertSummaryList", {}).get("advertSummary", [])
    return {
        "rows_found": data.get("rowsFound"),
        "rows_returned": data.get("rowsReturned"),
        "results": [_summarize_car(ad) for ad in ads],
    }


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


if __name__ == "__main__":
    url = f"http://{HOST}:{PORT}{MCP_PATH}"
    logger.info("Starting willhaben-mcp server on %s", url)
    mcp.run("streamable-http", host=HOST, port=PORT, streamable_http_path=MCP_PATH)