"""The scripts behind the README badges."""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
spec = importlib.util.spec_from_file_location("update_coverage", SCRIPTS / "update_coverage.py")
update_coverage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(update_coverage)


def test_human_shortens_the_counts():
    assert [update_coverage.human(n) for n in (1234567, 98765, 999)] == ["1.2M", "99k", "999"]


def test_clone_counts_add_up_across_runs(tmp_path):
    """GitHub reports the last 14 days; days seen by an earlier run are kept,
    a day reported again is replaced rather than counted twice."""
    def run(*days):
        raw = {"clones": [{"timestamp": f"{day}T00:00:00Z", "count": count, "uniques": 1} for day, count in days]}
        (tmp_path / "clones_raw.json").write_text(json.dumps(raw))
        subprocess.run([sys.executable, str(SCRIPTS / "update_clones.py")], cwd=tmp_path, check=True,
                       capture_output=True)
        return json.loads((tmp_path / "clones.json").read_text())

    run(("2026-10-01", 3), ("2026-10-02", 4))
    store = run(("2026-10-02", 5), ("2026-10-03", 1))
    assert store["daily"]["2026-10-01"]["count"] == 3
    assert (store["total_clones"], store["days_tracked"]) == (9, 3)
