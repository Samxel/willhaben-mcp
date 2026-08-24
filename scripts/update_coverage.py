#!/usr/bin/env python3
"""Fetch live willhaben listing counts per vertical and write ``coverage.json``.

The README's shields.io badges read that file, and a scheduled GitHub Action
runs this script to keep the numbers fresh. A single request to the vertical
overview endpoint returns ``nrOfAdverts`` for every vertical at once.
"""

import datetime
import gzip
import json
import urllib.request
import uuid
from pathlib import Path

WH_CLIENT = "api@tailored-apps.com;willhabenapp;android;8.57.0;responsive_app"
VERTICAL_URL = "https://www.willhaben.at/webapi/ad-search/vertical"

# willhaben vertical id -> coverage.json key
VERTICALS = {
    5: "marktplatz",
    3: "autos",
    2: "immobilien",
}


def fetch_verticals() -> dict[int, int]:
    headers = {
        "x-wh-client": WH_CLIENT,
        "x-wh-visitor-id": str(uuid.uuid4()),
        "Accept": "application/json",
        "accept-encoding": "gzip",
    }
    request = urllib.request.Request(VERTICAL_URL, headers=headers)
    with urllib.request.urlopen(request, timeout=30) as response:
        body = response.read()
        if body[:2] == b"\x1f\x8b":
            body = gzip.decompress(body)
        data = json.loads(body.decode())
        return {v["id"]: int(v["nrOfAdverts"]) for v in data["vertical"]}


def human(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.0f}k"
    return str(n)


def main() -> None:
    counts = fetch_verticals()

    coverage = {"updated": datetime.date.today().isoformat()}
    for vertical_id, name in VERTICALS.items():
        count = counts[vertical_id]
        coverage[name] = human(count)
        coverage[f"{name}_count"] = count

    out = Path(__file__).resolve().parent.parent / "coverage.json"
    out.write_text(json.dumps(coverage, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(coverage, indent=2))


if __name__ == "__main__":
    main()
