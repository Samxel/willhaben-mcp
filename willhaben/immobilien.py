"""Immobilien (real estate).

The real-estate vertical lives under ``atz/2/<propertyTypeId>``: each property
type (rent flat, buy house, plot, ...) is its own category id in the path, and
the available filters differ per type. Filter options, ranges and the property
types ship in data/immobilien/filters.json."""

import json
import logging
from pathlib import Path
from typing import Union

from mcp.server.mcpserver.exceptions import ToolError

from . import matching, summaries


IMMO_SEARCH_PREFIX = "atz/2"
IMMO_RESULTS_BASE = "https://ad-search.willhaben.at/restapi/v2/search"

_ROOM_BUCKETS = {
    "1": "1X1", "2": "2X2", "3": "3X3", "4": "4X4", "5": "5X5",
    "6-9": "6X9", "10+": "10X",
}


def _load_immo_data() -> dict:
    path = Path(__file__).parent.parent / "data" / "immobilien" / "filters.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        logging.getLogger(__name__).warning("immobilien filters.json not found -- property search disabled")
        return {"categories": {}, "selects": {}, "ranges": {}}


_IMMO = _load_immo_data()
IMMO_TYPES: dict[int, str] = {int(k): v for k, v in _IMMO.get("categories", {}).items()}
_IMMO_TYPE_BY_NAME: dict[str, list[int]] = {}
for _tid, _tlabel in IMMO_TYPES.items():
    _IMMO_TYPE_BY_NAME.setdefault(matching.norm(_tlabel), []).append(_tid)


def resolve_type(property_type: Union[int, str]) -> str:
    """Resolve a property type given as id or name to its atz/2/<id> category id."""
    if isinstance(property_type, int) or (isinstance(property_type, str) and property_type.strip().isdigit()):
        tid = int(str(property_type).strip())
        if tid not in IMMO_TYPES:
            raise ToolError(f"Unknown property-type id {tid}. Use list_immobilien_types.")
        return str(tid)
    matches = _IMMO_TYPE_BY_NAME.get(matching.norm(property_type), [])
    if not matches:
        raise ToolError(f"Unknown property type '{property_type}'. Use list_immobilien_types.")
    if len(matches) > 1:
        opts = ", ".join(f"{i} ({IMMO_TYPES[i]})" for i in matches[:10])
        raise ToolError(f"Property type '{property_type}' is ambiguous: {opts}. Please pass an id.")
    return str(matches[0])


def resolve_options(value, options: dict, human: str) -> list[str]:
    """Map one or more labels/ids to ids for an enumerated filter ({id: label})."""
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


def options(qp: str) -> dict:
    return _IMMO.get("selects", {}).get(qp, {}).get("options", {})


def resolve_rooms(rooms) -> list[str]:
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


def summarize_immo(ad: dict) -> dict:
    """Like :func:`summaries.summarize_ad`, plus the property-specific fields."""
    result = summaries.summarize_ad(ad)
    attr = summaries.ad_attributes(ad)
    living = summaries.to_float(attr.get("ESTATE_SIZE/LIVING_AREA") or attr.get("ESTATE_SIZE"))
    price_per_m2 = summaries.to_float(attr.get("PRICE/SQUARE_METER"))
    if price_per_m2 is None and living and result["price_amount"]:
        price_per_m2 = round(result["price_amount"] / living, 2)
    result.update({
        "property_type": attr.get("PROPERTY_TYPE"),
        "living_area_m2": living,
        "plot_area_m2": summaries.to_float(attr.get("PLOT/AREA")),
        "rooms": attr.get("NUMBER_OF_ROOMS") or attr.get("NO_OF_ROOMS"),
        "price_per_m2": round(price_per_m2, 2) if price_per_m2 is not None else None,
        "floor": attr.get("FLOOR"),
        "district": attr.get("DISTRICT"),
        "address": attr.get("ADDRESS"),
    })
    return result
