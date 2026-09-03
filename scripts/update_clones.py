import json
from pathlib import Path

RAW = Path("clones_raw.json")
STORE = Path("clones.json")

raw = json.loads(RAW.read_text())
store = json.loads(STORE.read_text()) if STORE.exists() else {"daily": {}}
daily = store.setdefault("daily", {})

for entry in raw.get("clones", []):
    day = entry["timestamp"][:10]
    daily[day] = {"count": entry["count"], "uniques": entry["uniques"]}

store["daily"] = dict(sorted(daily.items()))
store["total_clones"] = sum(d["count"] for d in daily.values())
store["total_uniques"] = sum(d["uniques"] for d in daily.values())
store["days_tracked"] = len(daily)

STORE.write_text(json.dumps(store, indent=2) + "\n")
print(f"{store['total_clones']} clones / {store['total_uniques']} uniques over {store['days_tracked']} days")
