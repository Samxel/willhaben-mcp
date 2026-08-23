#!/usr/bin/env python3
"""Fetch live willhaben listing counts per vertical and write ``coverage.json``.

The README's shields.io badges read that file, and a scheduled GitHub Action
runs this script to keep the numbers fresh. One request per vertical (the total
``rowsFound`` of an empty search), so it stays cheap.
"""

import datetime
import gzip
import json
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

WH_CLIENT = "api@tailored-apps.com;willhabenapp;android;8.57.0;responsive_app"

# vertical -> (search endpoint, extra query params)
VERTICALS = {
    "marktplatz": (
        "https://ad-search.willhaben.at/restapi/v2/search/atz/seo/kaufen-und-verkaufen/marktplatz",
        {},
    ),
    "autos": (
        "https://ad-search.willhaben.at/restapi/v2/search/atz/3/2",
        {"isLog": "true"},
    ),
}


def rows_found(url: str, params: dict) -> int:
    query = urllib.parse.urlencode({**params, "rows": "0"}, doseq=True)
    headers = {
        "x-wh-client": WH_CLIENT,
        "x-wh-visitor-id": str(uuid.uuid4()),
        "Accept": "application/json",
        "accept-encoding": "gzip",
    }
    request = urllib.request.Request(f"{url}?{query}", headers=headers)
    with urllib.request.urlopen(request, timeout=30) as response:
        body = response.read()
        if body[:2] == b"\x1f\x8b":
            body = gzip.decompress(body)
        return int(json.loads(body.decode())["rowsFound"])


def human(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.0f}k"
    return str(n)


def main() -> None:
    coverage = {"updated": datetime.date.today().isoformat()}
    for name, (url, params) in VERTICALS.items():
        count = rows_found(url, params)
        coverage[name] = human(count)
        coverage[f"{name}_count"] = count

    out = Path(__file__).resolve().parent.parent / "coverage.json"
    out.write_text(json.dumps(coverage, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(coverage, indent=2))


if __name__ == "__main__":
    main()
