# /// script
# requires-python = ">=3.10"
# dependencies = ["mcp>=2.3.0,<3", "httpx[http2]>=0.28.1"]
# ///
from mcp.server import MCPServer
from mcp.server.caching import CacheHint
from mcp.server.mcpserver import Image
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
import httpx
import argparse
import asyncio
import uuid
import json
import re
import html
import functools
import inspect
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

Shared parameters: area is an Austrian state (wien, niederoesterreich, ...). \
sort_by is actuality (default), price_asc, price_desc, nearest or relevance. \
rows is at most 50; page on with the next_offset a result returns, has_more \
says whether anything is left.

status is "active", "reserved" or "sold". willhaben always reports an ad as \
active, so this is read from the seller's marker in the title.

Fields without a value are left out of results.
"""

# The tool list only changes with a new release, so clients on the 2026-07-28
# protocol may keep it for a day instead of asking again on every connect.
mcp = MCPServer("willhaben-mcp", instructions=INSTRUCTIONS,
                cache_hints={"tools/list": CacheHint(ttl_ms=24 * 60 * 60 * 1000, scope="public")})
# HTTP/2 is required by the detail API; the search API negotiates it too.
client = httpx.AsyncClient(http2=True)

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s: %(message)s')

MAX_ROWS = 50
RETRYABLE_STATUS = {429, 500, 502, 503, 504}
MAX_RETRIES = 3


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


def _tool(fn=None, *, annotations: ToolAnnotations = ONLINE):
    """Register `fn` as a tool whose dict result goes out as compact JSON with
    its null fields left out. Left to the SDK, a dict is pretty-printed with a
    two-space indent, which is about a third of a typical result's tokens.
    structured_output stays off: it would send every result a second time as
    structuredContent and add an output schema to the tool listing. The
    docstring is dedented here because only Python 3.13+ does that itself; on
    older ones every description line would carry four spaces of indent."""
    if fn is None:
        return functools.partial(_tool, annotations=annotations)

    @functools.wraps(fn)
    async def wrapper(*args, **kwargs):
        result = await fn(*args, **kwargs)
        return json.dumps(_prune(result), ensure_ascii=False, separators=(",", ":"))
    return mcp.tool(description=inspect.cleandoc(fn.__doc__ or ""), annotations=annotations,
                    structured_output=False)(wrapper)


def _content_tool(fn):
    """Register `fn` as is, for a tool that returns content blocks rather than a
    dict; its docstring is dedented like ``_tool``'s."""
    return mcp.tool(description=inspect.cleandoc(fn.__doc__ or ""), annotations=ONLINE,
                    structured_output=False)(fn)


def _tool_errors(what: str):
    """Turn upstream failures into a ``ToolError`` the model can read, rather
    than a raw httpx error; any exception that is not a ``ToolError`` would only
    reach it as "Error executing tool". ``what`` names the thing being fetched,
    for the message."""
    def decorator(fn):
        @functools.wraps(fn)
        async def wrapper(*args, **kwargs):
            try:
                return await fn(*args, **kwargs)
            except httpx.HTTPStatusError as exc:
                raise ToolError(_upstream_message(exc.response.status_code, what)) from exc
            except httpx.HTTPError as exc:
                raise ToolError(f"could not reach willhaben for the {what} request "
                                f"({type(exc).__name__})") from exc
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
        raise ToolError(f"Could not reach willhaben to obtain an application token: {exc}") from exc
    if response.status_code == 401:
        raise ToolError(
            "willhaben rejected the stored token-refresh credential (401). Its signing "
            "key was most likely rotated -- capture a fresh applicationTokenRequest from "
            "the app and update WH_TOKEN_REQUEST in main.py (see the comment there)."
        )
    response.raise_for_status()
    token = (response.json().get("applicationToken") or {}).get("value")
    if not token:
        raise ToolError(
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


# A number and its unit: sellers write the same storage size as "128GB",
# "128 GB", "128gb" or "256Gb", so a term given in one of those has to match all.
_NUM_UNIT_RE = re.compile(r"^(\d+)\s*([a-z]+)$")


def _term_forms(term: str) -> list[set]:
    """The token sets a filter term may appear as. Everything is one form,
    except a number with a unit, which is also accepted split in two -- that way
    a term matches whichever way the seller spelled it, without changing how the
    rest of a title is read."""
    folded = _fold(term).strip()
    match = _NUM_UNIT_RE.match(folded)
    if not match:
        return [_tokens(folded)]
    number, unit = match.groups()
    return [{f"{number}{unit}"}, {number, unit}]


def _has_term(tokens: set, forms: list[set]) -> bool:
    """Whether a title (or ad text) carries a filter term. A word matches in
    full, or as the tail of a German compound ("kabel" catches "Ladekabel"), but
    never one that merely starts with it -- otherwise "pro" would throw away
    every "Prozessor" and "provisionsfrei" and be useless for separating an
    iPhone 13 from an iPhone 13 Pro."""
    return any(all(any(token == word or token.endswith(word) for token in tokens)
                   for word in form)
               for form in forms)


def _check_ranges(*bounds) -> None:
    """Reject an inverted range instead of letting willhaben drop the filter and
    quietly answer with everything."""
    for name, low, high in bounds:
        if low is not None and high is not None and low > high:
            raise ToolError(
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
            raise ToolError(f"Unknown category id {cid}. Use the list_categories tool.")
        return str(cid)
    matches = _CATEGORY_BY_NAME.get(_norm(category), [])
    if not matches:
        # names come in any order and wording ("Handys / Smartphones" for
        # "Smartphones / Handys"): a category carrying every word is meant
        wanted = _tokens(category)
        matches = [cid for cid, label in CATEGORY_LABEL.items() if wanted and wanted <= _tokens(label)]
    if not matches:
        raise ToolError(f"Unknown category '{category}'. {_closest_categories(category)}")
    if len(matches) > 1:
        opts = ", ".join(f"{i} ({CATEGORY_PATH[i]})" for i in matches[:10])
        raise ToolError(f"Category '{category}' is ambiguous ({len(matches)} matches): {opts}. Please pass an id.")
    return str(matches[0])


def _word_overlap(wanted: set, words: set) -> int:
    """How many of `words` relate to one of `wanted`, counting a part of a
    German compound: "damenschuhe" finds both "schuhe" and "damenmode"."""
    def related(a, b):
        if a == b:
            return True
        # parts of a compound only count from four letters on, or "r" from
        # "R&B" would be found in every other word
        return len(a) >= 4 and len(b) >= 4 and (a in b or b in a or a[:5] == b[:5])
    return sum(any(related(w, word) for w in wanted) for word in words)


def _closest_categories(name: str, limit: int = 5) -> str:
    """The categories whose path shares the most words with `name`, as a hint
    for the model, so a near miss does not end in a bare "unknown"."""
    wanted = {w for w in _tokens(name) if len(w) >= 3}
    scored = sorted(((_word_overlap(wanted, _tokens(CATEGORY_PATH[cid])), cid) for cid in CATEGORY_LABEL),
                    key=lambda pair: (-pair[0], len(CATEGORY_PATH[pair[1]])))
    best = [f"{cid} ({CATEGORY_PATH[cid]})" for score, cid in scored[:limit] if score]
    if not best:
        return "Use list_categories to find the id."
    return "Closest: " + ", ".join(best) + ". Pass one of these ids."


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
            raise ToolError(
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


def _ad_url(ad_id) -> Optional[str]:
    """The ad's short link; willhaben redirects it to the long SEO address, which
    is three times the length for the same page."""
    return f"https://www.willhaben.at/iad/object?adId={ad_id}" if ad_id else None


def _to_float(value) -> Optional[float]:
    try:
        return float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return None


def _price_amount(attr: dict) -> Optional[float]:
    """The numeric price. Marketplace and car ads carry ``PRICE/AMOUNT``;
    property ads only have ``PRICE`` (or, for lettings, the monthly rent) --
    without the fallback, sorting and price-per-m2 are impossible on a value
    that is plainly there. An ad with none of these has no price at all
    ("auf Anfrage"), and neither does its display string."""
    for key in ("PRICE/AMOUNT", "PRICE", "RENT/PER_MONTH_LETTINGS"):
        amount = _to_float(attr.get(key))
        if amount is not None:
            return amount
    return None


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
    title = attr.get("HEADING") or ad.get("description")
    amount = _price_amount(attr)
    return {
        "id": ad.get("id"),
        "title": title,
        "description": attr.get("BODY_DYN"),
        "price_amount": amount,
        # the display string only says something the number does not when
        # there is no number ("auf Anfrage")
        "price": attr.get("PRICE_FOR_DISPLAY") if amount is None else None,
        "status": _ad_status(title, ad)[0],
        "seller": "privat" if attr.get("ISPRIVATE") == "1" else "haendler",
        "seller_name": attr.get("ORGNAME") or attr.get("CONTACT/NAME"),
        "location": attr.get("LOCATION"),
        "postcode": attr.get("POSTCODE"),
        "published": (attr.get("PUBLISHED_String") or "")[:16] or None,
        "paylivery": attr.get("p2penabled") == "true" or None,
        "url": _ad_url(ad.get("id")),
    }


def _widget(widgets: list[dict], widget_type: str) -> dict:
    """Return the first detail widget of the given type, or an empty dict."""
    return next((w for w in widgets if w.get("type") == widget_type), {})


def _detail_attributes(detail: dict) -> dict:
    """The detail's itemised ``name -> value`` attributes ("Übergabe",
    "Zustand", ...), which live in the KEY_VALUE_PAIRS_LIST widget."""
    pairs = _widget(detail.get("widgets", []), "KEY_VALUE_PAIRS_LIST").get("keyValuePairsList", [])
    return {kv.get("name"): kv.get("value") for kv in pairs}


def _handover_options(attributes: dict) -> list[str]:
    """What the seller offers ("Selbstabholung", "Versand"). The only place
    willhaben states delivery -- it is not in the search response at all."""
    return [h.strip() for h in str(attributes.get("Übergabe") or "").split(",") if h.strip()]


def _summarize_ad_detail(detail: dict) -> dict:
    """Reduce willhaben's widget-based advert-detail response to the fields that
    matter for an AI: the complete (untruncated) description, every image, the
    itemised attributes and precise location. Complements :func:`_summarize_ad`,
    whose description is truncated."""
    widgets = detail.get("widgets", [])
    tms = detail.get("taggingData", {}).get("tmsDataValues", {}).get("tmsData", {})

    images = _widget(widgets, "PICTURE_SLIDER").get("advertImageList", [])
    title_price = _widget(widgets, "TITLE_WITH_PRICE")
    # Several PARAGRAPHED_TEXT widgets exist (e.g. the "Privatperson" label);
    # the real description is the one with the longest body.
    descriptions = [w.get("teaser", "") for w in widgets if w.get("type") == "PARAGRAPHED_TEXT"]
    description = max(descriptions, key=len) if descriptions else None

    attributes = _detail_attributes(detail)
    title = detail.get("description")
    handover = _handover_options(attributes)
    amount = _to_float(tms.get("exact_price"))
    return {
        "id": detail.get("adId"),
        "title": title,
        "description": description or None,
        "price_amount": amount,
        "price": title_price.get("formattedPrice") if amount is None else None,
        "status": _ad_status(title, detail)[0],
        "condition": attributes.get("Zustand"),
        "handover": handover,
        "ships": any("versand" in h.lower() for h in handover),
        "pickup_only": bool(handover) and not any("versand" in h.lower() for h in handover),
        "seller": "privat" if tms.get("is_private") == "true" else "haendler",
        "seller_name": tms.get("seller_name"),
        "paylivery": detail.get("payliveryEnabled") or None,
        "location": tms.get("region_level_3"),
        "postcode": tms.get("post_code"),
        "state": tms.get("region_level_2"),
        "category": [kv.get("categoryName") for kv in
                     _widget(widgets, "CATEGORIES").get("categoryPath", {}).get("categoryEntryList", [])],
        # condition and handover are pulled out above already
        "attributes": {k: v for k, v in attributes.items() if k not in ("Zustand", "Übergabe")} or None,
        "published": tms.get("publish_date"),
        "delivery": title_price.get("deliveryCosts"),
        # the pictures themselves come from get_ad_images; their urls would
        # only cost tokens here
        "image_count": len(images),
        "url": _ad_url(detail.get("adId")),
    }


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
        detail = await _fetch_detail(ad_id)
    except (httpx.HTTPError, ToolError):
        return None
    return _handover_options(_detail_attributes(detail))


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


@_tool
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
    wanted = _tokens(keyword) if (title_only and keyword) else set()
    excluded = [_term_forms(t) for t in (exclude or []) if str(t).strip()]
    required = [_term_forms(t) for t in (require or []) if str(t).strip()]
    post_filtered = bool(wanted or excluded or required or hide_reserved or handover)

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
        tokens = _tokens(ad["title"] or "")
        if wanted and not wanted <= tokens:
            return False
        if any(_has_term(tokens, forms) for forms in excluded):
            return False
        if required:
            # a spec is written wherever the seller felt like it, so look at the
            # whole ad rather than demanding it in the title
            text = tokens | _tokens(ad["description"] or "")
            if not all(_has_term(text, forms) for forms in required):
                return False
        return not (hide_reserved and ad["status"] != "active")

    results: list = []
    cursor, lookups, rows_found = offset, 0, None
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
@_tool_errors("ad")
async def get_ad_detail(ad_id: Union[str, int]) -> dict:
    """One ad in full: the complete description (also when the app
    API cuts it), condition, attributes, category, location and ``image_count``.

    It is the only place that says how an item changes hands: ``handover``
    ("Selbstabholung", "Versand") with ``ships`` and ``pickup_only``.

    Args:
        ad_id: The ad id from a search.
    """
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


@_content_tool
@_tool_errors("ad")
async def get_ad_images(ad_id: Union[str, int], max_images: int = 4) -> list:
    """The ad's photos as images you can look at, up to
    ``max_images`` (at most 10). Use them whenever the answer depends on what
    the item looks like: condition, damage, completeness, or details only a
    picture shows, such as a label or a screen.

    Args:
        ad_id: The ad id from a search.
        max_images: How many photos to fetch. Default 4.
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


SELLER_TRUST_URL = "https://publicapi.willhaben.at/userprofile/trust-signals"
SELLER_PROFILE_URL = "https://ad-search.willhaben.at/restapi/v2/sellerprofile"
DEALER_PROFILE_URL = "https://api.willhaben.at/restapi/v2/dealerprofile"


@_tool
@_tool_errors("seller")
async def get_ad_seller(ad_id: Union[str, int]) -> dict:
    """Who sells an ad: name, private or dealer, rating and reply
    time for private sellers, member since, and location. For a trust check the
    ad itself does not answer.

    Args:
        ad_id: The ad id from a search.
    """
    detail = await _fetch_detail(ad_id)
    tms = detail.get("taggingData", {}).get("tmsDataValues", {}).get("tmsData", {})
    seller_id = tms.get("seller_id")
    name = tms.get("seller_name")
    if not seller_id:
        raise ToolError(f"Ad {ad_id} names no seller id (seller name: {name!r}).")

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
        q = _norm(query)
        ids = [cid for cid, lab in CATEGORY_LABEL.items() if q in _norm(lab)]
    else:
        ids = CATEGORY_CHILDREN.get(parent_id, [])

    ids.sort(key=lambda i: CATEGORY_PATH[i])
    results = [
        {
            "id": cid,
            # the path ends in the label, and the parent is the step before it
            "path": CATEGORY_PATH[cid],
            "has_children": bool(CATEGORY_CHILDREN.get(cid)),
        }
        for cid in ids[:limit]
    ]
    return {"count": len(ids), "returned": len(results), "categories": results}


_TAG_RE = re.compile(r"<[^>]+>")


@_tool
@_tool_errors("brand")
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
            raise ToolError(f"Unknown make id {mid}. Use list_car_makes.")
        return str(mid)
    matches = _CAR_MAKE_BY_NAME.get(_norm(make), [])
    if not matches:
        raise ToolError(f"Unknown make '{make}'. Use list_car_makes.")
    if len(matches) > 1:
        opts = ", ".join(f"{i} ({CAR_MAKES[i]})" for i in matches[:10])
        raise ToolError(f"Make '{make}' is ambiguous: {opts}. Please pass an id.")
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
            raise ToolError(f"Unknown {human} '{v}'. Options: {', '.join(options.values())}")
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


# model id -> name, learned from every model list fetched, so a car hit can
# name its model instead of carrying willhaben's bare id
_CAR_MODEL_NAMES: dict[str, str] = {}


async def _fetch_car_models(make_id: str) -> dict:
    """Fetch a make's models ({id: label}) from the navigation endpoint."""
    params = {"isNavigation": "true", "CAR_MODEL/MAKE": make_id, "sfId": str(uuid.uuid4())}
    headers = {
        "x-wh-client": WH_CLIENT,
        "x-wh-visitor-id": str(uuid.uuid4()),
        "Accept": "application/json",
    }
    response = await _get(AUTO_NAV_URL, params=params, headers=headers)
    models = _car_models_from_response(response.json())
    _CAR_MODEL_NAMES.update(models)
    return models


async def _resolve_car_model(model: Union[int, str], make_id: Optional[str]) -> str:
    """Resolve a model given as id or name (within a make) to its CAR_MODEL/MODEL id."""
    s = str(model).strip()
    if s.isdigit():
        return s
    if make_id is None:
        raise ToolError("To search by model name, also pass 'make' (or use a model id).")
    models = await _fetch_car_models(make_id)  # {id: label}
    by_norm = {_norm(l): i for i, l in models.items()}
    mid = by_norm.get(_norm(s))
    if mid is None:  # fall back to a substring match ("3er" -> "3er-Reihe")
        cands = [(i, l) for i, l in models.items() if _norm(s) in _norm(l)]
        if len(cands) == 1:
            mid = cands[0][0]
        elif len(cands) > 1:
            opts = ", ".join(f"{l} ({i})" for i, l in cands[:10])
            raise ToolError(f"Model '{model}' is ambiguous: {opts}. Be more specific or pass an id.")
    if mid is None:
        raise ToolError(f"Unknown model '{model}' for this make. Use list_car_models.")
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
    make, year = attr.get("CAR_MODEL/MAKE"), attr.get("YEAR_MODEL")
    result.update({
        # willhaben only sends ids; the make list ships with the server, model
        # names are known once that make's models have been fetched
        "make": CAR_MAKES.get(int(make)) if str(make or "").isdigit() else make,
        "model": _CAR_MODEL_NAMES.get(str(attr.get("CAR_MODEL/MODEL"))),
        "variant": attr.get("CAR_MODEL/MODEL_SPECIFICATION"),
        "year": int(year) if str(year or "").isdigit() else None,
        "mileage_km": int(mileage) if mileage not in (None, "") else None,
        "fuel": attr.get("ENGINE/FUEL_RESOLVED"),
        "transmission": attr.get("TRANSMISSION_RESOLVED"),
        "power_kw": int(power_kw) if power_kw is not None else None,
        "power_ps": round(power_kw * KW_TO_PS) if power_kw is not None else None,
        "condition": attr.get("CONDITION_RESOLVED"),
        "owners": attr.get("NO_OF_OWNERS"),
    })
    return result


@_tool
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
    """Search used cars. Everything is optional.

    Prefer the structured filters to ``keyword``: they match exact fields, a
    keyword searches the free text. Use a keyword for what has no filter, such
    as a chassis code ("E39") on top of make and model.

    Hits name ``make`` and ``model`` and give power as ``power_kw`` and
    ``power_ps``. willhaben tops a make search with a paid listing whatever the
    filters say; hits outside the requested ranges are dropped and counted in
    ``dropped_off_filter``.

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
    make_id = params.get("CAR_MODEL/MAKE")
    model_ids = {str(_attributes_to_dict(ad).get("CAR_MODEL/MODEL")) for ad in ads}
    if make_id and not model_ids <= _CAR_MODEL_NAMES.keys():
        try:  # one request per make, then the names are known for good
            await _fetch_car_models(make_id)
        except httpx.HTTPError:
            pass
    cars = [_summarize_car(ad) for ad in ads]
    # willhaben puts a paid dealer listing on top of a make search whatever the
    # filters say (a 48k car for "up to 15k"); drop anything outside them
    bounds = [("price_amount", price_from, price_to), ("year", year_from, year_to),
              ("mileage_km", mileage_from, mileage_to), ("power_kw", power_from, power_to)]
    kept = [c for c in cars if all(c.get(field) is None or ((low is None or c[field] >= low) and
                                                             (high is None or c[field] <= high))
                                   for field, low, high in bounds)]
    result = {**_pagination(data.get("rowsFound"), offset + len(ads), len(kept)), "results": kept}
    if len(kept) < len(cars):
        result["dropped_off_filter"] = len(cars) - len(kept)
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
        q = _norm(query)
        ids = [i for i, name in CAR_MAKES.items() if q in _norm(name)]
    else:
        ids = list(CAR_MAKES)
    ids.sort(key=lambda i: CAR_MAKES[i].lower())
    makes = [{"id": i, "label": CAR_MAKES[i]} for i in ids[:limit]]
    return {"count": len(ids), "returned": len(makes), "makes": makes}


@_tool
@_tool_errors("car model")
async def list_car_models(make: Union[int, str]) -> dict:
    """List the models of a car make, for the ``model`` filter of
    ``search_autos``.

    Args:
        make: Make id or name.
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
            raise ToolError(f"Unknown property-type id {tid}. Use list_immobilien_types.")
        return str(tid)
    matches = _IMMO_TYPE_BY_NAME.get(_norm(property_type), [])
    if not matches:
        raise ToolError(f"Unknown property type '{property_type}'. Use list_immobilien_types.")
    if len(matches) > 1:
        opts = ", ".join(f"{i} ({IMMO_TYPES[i]})" for i in matches[:10])
        raise ToolError(f"Property type '{property_type}' is ambiguous: {opts}. Please pass an id.")
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
            raise ToolError(f"Unknown {human} '{v}'. Options: {', '.join(options.values())}")
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
            raise ToolError(f"Unknown rooms value '{v}'. Options: {', '.join(_ROOM_BUCKETS)}")
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


@_tool
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
    """Search real estate. ``property_type`` picks buy or rent
    and the kind of property; everything else is optional, and filters that do
    not fit the type are ignored by willhaben.

    Hits carry ``price_amount`` and ``price_per_m2`` next to
    ``living_area_m2``, ``plot_area_m2``, ``rooms``, ``floor``, ``district`` and
    ``address``.

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
        last_48h: Only ads from the last two days.
    """
    catid = _resolve_immo_type(property_type)
    _check_ranges(("price", price_from, price_to), ("area", area_from, area_to),
                  ("plot", plot_from, plot_to))
    rows = _rows(rows, offset)
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


@_tool(annotations=LOCAL)
async def list_immobilien_types() -> dict:
    """List the property types for ``search_immobilien``."""
    types = [{"id": i, "label": IMMO_TYPES[i]} for i in sorted(IMMO_TYPES)]
    return {"count": len(types), "types": types}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="willhaben MCP server")
    parser.add_argument("--stdio", action="store_true",
                        help="speak MCP over stdin/stdout instead of serving HTTP")
    if parser.parse_args().stdio:
        # stdout carries the protocol; the log already goes to stderr
        mcp.run("stdio")
    else:
        url = f"http://{HOST}:{PORT}{MCP_PATH}"
        logger.info("Starting willhaben-mcp server on %s", url)
        mcp.run("streamable-http", host=HOST, port=PORT, streamable_http_path=MCP_PATH)