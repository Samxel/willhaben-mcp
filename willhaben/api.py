"""Requests to willhaben's app API: the application token, retries, the
error messages the model sees, and the param sets every search shares."""

import asyncio
import functools
import html
import json
import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Literal, Optional, Union

import httpx
from mcp.server.mcpserver.exceptions import ToolError


logger = logging.getLogger(__name__)


BASE_URL = "https://ad-search.willhaben.at/restapi/v2/search/atz/seo/kaufen-und-verkaufen/marktplatz"
DETAIL_URL = "https://publicapi.willhaben.at/atdetail/v1"
ATTRIBUTE_OPTIONS_URL = "https://app-aggregator.willhaben.at/api/v1/search/attribute-options"
APPLICATION_DATA_URL = "https://app-aggregator.willhaben.at/api/v1/application-data"

WH_CLIENT = "api@tailored-apps.com;willhabenapp;android;8.57.0;responsive_app"

# The detail API (HTTP/2) needs an `x-wh-application-token`, which willhaben's
# server issues for 30 days in answer to the app's signed `applicationTokenRequest`.
# That request is stored below, and a live token is fetched from it whenever one
# is needed (see `_fetch_application_token`), so no token that expires is
# hardcoded. If willhaben stops accepting the stored request (401), it needs
# updating.
WH_SECURITY_VERSION = "20130527022532"
WH_TOKEN_REQUEST = {
    "organization": "api@tailored-apps.com",
    "salt": "_xzcrY0SjCsTS6Uo",
    "signature": "3oC9/ga9Z7pIxTdx/sccKjmycnw=",
    "timestamp": "2026-08-24T09:25:52+0200",
}


# HTTP/2 is required by the detail API; the search API negotiates it too.
client = httpx.AsyncClient(http2=True)


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
                f"could not be refreshed; see WH_TOKEN_REQUEST in willhaben/api.py")
    return f"willhaben failed on the {what} request (HTTP {status})"


def tool_errors(what: str):
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


async def get(url: str, *, params: Optional[dict] = None, headers: Optional[dict] = None) -> httpx.Response:
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
    """Obtain a fresh 30-day ``x-wh-application-token`` from willhaben with the
    stored signed ``applicationTokenRequest`` (see WH_TOKEN_REQUEST)."""
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
            "willhaben rejected the stored token request (401), most likely because its "
            "signing key changed. WH_TOKEN_REQUEST in willhaben/api.py needs updating."
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


async def authed_get(url: str, *, params: Optional[dict] = None) -> httpx.Response:
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
            return await get(url, params=params, headers=headers)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 401 and attempt == 0:
                logger.info("app token expired -- refreshing and retrying")
                continue
            raise
    raise RuntimeError("unreachable")  # pragma: no cover


async def fetch_detail(ad_id: Union[str, int]) -> dict:
    """GET the raw advert-detail JSON (shared by detail/image/seller tools)."""
    return (await authed_get(f"{DETAIL_URL}/{ad_id}")).json()


DESCRIPTION_CAP = 580  # the mobile detail API truncates the description near 600 chars


async def fetch_full_description(ad_id: Union[str, int]) -> Optional[str]:
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
    return html.unescape(TAG_RE.sub("", text)).strip()


TAG_RE = re.compile(r"<[^>]+>")
