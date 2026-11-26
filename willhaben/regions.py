"""Districts (Bezirke) for the ``district`` filter, shipped in
data/regions/districts.json (``scripts/build_districts.py`` refreshes it).

willhaben takes a district's area id wherever it takes a state's, so the
filter works the same for the marketplace, cars and real estate."""

import json
import logging
from pathlib import Path
from typing import Optional, Union

from mcp.server.mcpserver.exceptions import ToolError

from . import matching


def _load() -> list[dict]:
    path = Path(__file__).parent.parent / "data" / "regions" / "districts.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logging.getLogger(__name__).warning("could not load %s: %s", path, exc)
        return []


DISTRICTS: list[dict] = _load()
BY_ID = {d["id"]: d for d in DISTRICTS}
BY_POSTCODE = {d["postcode"]: d for d in DISTRICTS if d.get("postcode")}


def _segments(label: str) -> list[str]:
    # "Wien, 07. Bezirk, Neubau" -> its parts, so "Neubau" is an exact match
    return [matching.norm(part) for part in label.split(",")]


def resolve(value: Union[int, str]) -> dict:
    """One district by area id, Vienna postcode ("1070") or name ("Neubau",
    "Graz", "Innsbruck-Land")."""
    text = str(value).strip()
    if text.isdigit():
        if int(text) in BY_ID:
            return BY_ID[int(text)]
        if text in BY_POSTCODE:
            return BY_POSTCODE[text]
    q = matching.norm(text)
    exact = [d for d in DISTRICTS if q == matching.norm(d["label"]) or q in _segments(d["label"])]
    matches = exact or [d for d in DISTRICTS if q and q in matching.norm(d["label"])]
    if len(matches) == 1:
        return matches[0]
    if matches:
        names = "; ".join(f"{d['id']} ({d['label']})" for d in matches[:10])
        raise ToolError(f"District {value!r} is ambiguous: {names}. Pass one of the ids.")
    raise ToolError(f"Unknown district {value!r}. Pass a district name such as 'Graz' or 'Neubau', "
                    f"or a Vienna postcode such as '1070'.")


def area_ids(states: Optional[list[str]], districts: Optional[list], state_ids: dict) -> Optional[list[int]]:
    """The ``areaId`` values for a state filter and a district filter. A
    district narrows its state, so with both, each district has to lie in one
    of the states and only the districts are sent."""
    if not districts:
        return [state_ids[s] for s in states] if states else None
    picked = [resolve(d) for d in districts]
    if states:
        outside = [d["label"] for d in picked if d["state"] not in states]
        if outside:
            raise ToolError(f"{', '.join(outside)} is not in {', '.join(states)}; drop 'area' or fix 'district'.")
    return [d["id"] for d in picked]
