"""Auto & Motor (cars / Gebrauchtwagen).

The car vertical lives under `atz/3/2`. Navigation/filter metadata comes from
app-aggregator, result lists from ad-search (same hosts as the marketplace).
Enumerated filters (car type, fuel, ...) and the make list ship in
data/auto-motor/filters.json; models are make-specific and fetched on demand."""

import json
import logging
import uuid
from pathlib import Path
from typing import Optional, Union

from mcp.server.mcpserver.exceptions import ToolError

from . import api, matching, summaries


AUTO_SEARCH_PATH = "atz/3/2"
AUTO_RESULTS_URL = f"https://ad-search.willhaben.at/restapi/v2/search/{AUTO_SEARCH_PATH}"
AUTO_NAV_URL = f"https://app-aggregator.willhaben.at/restapi/v2/search/{AUTO_SEARCH_PATH}"


def _load_auto_data() -> dict:
    path = Path(__file__).parent.parent / "data" / "auto-motor" / "filters.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        logging.getLogger(__name__).warning("auto-motor filters.json not found -- car search disabled")
        return {"selects": {}, "ranges": {}, "toggles": {}, "makes": {}}


_AUTO = _load_auto_data()
CAR_MAKES: dict[int, str] = {int(k): v for k, v in _AUTO.get("makes", {}).items()}
_CAR_MAKE_BY_NAME: dict[str, list[int]] = {}
for _mid, _mlabel in CAR_MAKES.items():
    _CAR_MAKE_BY_NAME.setdefault(matching.norm(_mlabel), []).append(_mid)


def resolve_make(make: Union[int, str]) -> str:
    """Resolve a car make given as id or (unique) name to its CAR_MODEL/MAKE id."""
    if isinstance(make, int) or (isinstance(make, str) and make.strip().isdigit()):
        mid = int(str(make).strip())
        if mid not in CAR_MAKES:
            raise ToolError(f"Unknown make id {mid}. Use list_car_makes.")
        return str(mid)
    matches = _CAR_MAKE_BY_NAME.get(matching.norm(make), [])
    if not matches:
        raise ToolError(f"Unknown make '{make}'. Use list_car_makes.")
    if len(matches) > 1:
        opts = ", ".join(f"{i} ({CAR_MAKES[i]})" for i in matches[:10])
        raise ToolError(f"Make '{make}' is ambiguous: {opts}. Please pass an id.")
    return str(matches[0])


def resolve_select(value, qp: str, human: str) -> list[str]:
    """Map one or more labels/ids to ids for an enumerated car filter."""
    options = _AUTO.get("selects", {}).get(qp, {}).get("options", {})  # {id: label}
    by_norm = {matching.norm(l): i for i, l in options.items()}
    if value is None:
        return []
    values = value if isinstance(value, list) else [value]
    out = []
    for v in values:
        s = str(v).strip()
        if s in options:
            out.append(s)
        elif matching.norm(s) in by_norm:
            out.append(by_norm[matching.norm(s)])
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
MODEL_NAMES: dict[str, str] = {}


async def fetch_models(make_id: str) -> dict:
    """Fetch a make's models ({id: label}) from the navigation endpoint."""
    params = {"isNavigation": "true", "CAR_MODEL/MAKE": make_id, "sfId": str(uuid.uuid4())}
    headers = {
        "x-wh-client": api.WH_CLIENT,
        "x-wh-visitor-id": str(uuid.uuid4()),
        "Accept": "application/json",
    }
    response = await api.get(AUTO_NAV_URL, params=params, headers=headers)
    models = _car_models_from_response(response.json())
    MODEL_NAMES.update(models)
    return models


async def resolve_model(model: Union[int, str], make_id: Optional[str]) -> str:
    """Resolve a model given as id or name (within a make) to its CAR_MODEL/MODEL id."""
    s = str(model).strip()
    if s.isdigit():
        return s
    if make_id is None:
        raise ToolError("To search by model name, also pass 'make' (or use a model id).")
    models = await fetch_models(make_id)  # {id: label}
    by_norm = {matching.norm(l): i for i, l in models.items()}
    mid = by_norm.get(matching.norm(s))
    if mid is None:  # fall back to a substring match ("3er" -> "3er-Reihe")
        cands = [(i, l) for i, l in models.items() if matching.norm(s) in matching.norm(l)]
        if len(cands) == 1:
            mid = cands[0][0]
        elif len(cands) > 1:
            opts = ", ".join(f"{l} ({i})" for i, l in cands[:10])
            raise ToolError(f"Model '{model}' is ambiguous: {opts}. Be more specific or pass an id.")
    if mid is None:
        raise ToolError(f"Unknown model '{model}' for this make. Use list_car_models.")
    return mid


KW_TO_PS = 1.35962


def summarize_car(ad: dict) -> dict:
    """Like :func:`summaries.summarize_ad`, plus the car-specific fields that matter."""
    result = summaries.summarize_ad(ad)
    attr = summaries.ad_attributes(ad)
    mileage = attr.get("MILEAGE")
    # ENGINE/EFFECT is kW, the same unit the power_from/power_to filters take --
    # ads and buyers talk in PS, so give both rather than one mislabelled number.
    power_kw = summaries.to_float(attr.get("ENGINE/EFFECT"))
    make, year = attr.get("CAR_MODEL/MAKE"), attr.get("YEAR_MODEL")
    result.update({
        # willhaben only sends ids; the make list ships with the server, model
        # names are known once that make's models have been fetched
        "make": CAR_MAKES.get(int(make)) if str(make or "").isdigit() else make,
        "model": MODEL_NAMES.get(str(attr.get("CAR_MODEL/MODEL"))),
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
