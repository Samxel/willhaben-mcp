"""Fetch willhaben's districts (Bezirke) of every Austrian state into
``willhaben/data/regions/districts.json``.

willhaben names them in the "Region" navigator of a search scoped to a state;
the area ids are the same for the marketplace, cars and real estate. Vienna's
districts also get their postcode (1010 for the 1st, 1070 for the 7th, ...).
Run it again when a district goes missing.
"""

import asyncio
import json
import re
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from willhaben import api  # noqa: E402

# any search answers with the navigator; rental flats are a small, quick one
URL = "https://ad-search.willhaben.at/restapi/v2/search/atz/2/131"
VIENNA = re.compile(r"^Wien, (\d\d)\. Bezirk")


async def districts_of(state: str, area: int) -> list[dict]:
    params = {"sfId": str(uuid.uuid4()), "rows": 1, "areaId": area}
    data = (await api.get(URL, params=params, headers={"x-wh-client": api.WH_CLIENT,
                                                       "Accept": "application/json"})).json()
    for group in data.get("navigatorGroups", []):
        for nav in group.get("navigatorList", []):
            if nav.get("id") != "district":
                continue
            out = []
            for grouped in nav.get("groupedPossibleValues", []):
                for value in grouped.get("possibleValues", []):
                    label = value["label"]
                    vienna = VIENNA.match(label)
                    out.append({
                        "id": int(value["urlParamRepresentationForValue"][0]["value"]),
                        "label": label,
                        "state": state,
                        "postcode": f"1{vienna.group(1)}0" if vienna else None,
                    })
            return out
    raise SystemExit(f"no district navigator for {state}")


async def main() -> None:
    states = [(s, a) for s, a in api.area_id.items() if s != "andere_laender"]
    found = []
    for state, area in states:
        found += await districts_of(state, area)
    found.sort(key=lambda d: (d["state"], d["label"]))
    out = Path(__file__).resolve().parent.parent / "willhaben" / "data" / "regions" / "districts.json"
    out.write_text(json.dumps(found, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"{len(found)} districts -> {out}")


if __name__ == "__main__":
    asyncio.run(main())
