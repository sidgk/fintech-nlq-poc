"""
Trust badge — shows on every answer so an exec can trust the number at a glance:
how fresh the data is, and whether the dbt quality + reconciliation tests pass.

Reads dbt's `target/run_results.json` (written on every `dbt build`/`test`).
"""

import os
import json
from datetime import datetime, timezone

_RR = os.path.join(os.path.dirname(__file__), "..", "dbt", "target", "run_results.json")


def _human_age(seconds: float) -> str:
    if seconds < 90:
        return "just now"
    if seconds < 5400:
        return f"{int(seconds / 60)}m ago"
    if seconds < 129600:
        return f"{int(seconds / 3600)}h ago"
    return f"{int(seconds / 86400)}d ago"


def badge():
    """e.g. '✅ data refreshed 4h ago · 15/15 tests passing'  (or None if unknown)."""
    try:
        d = json.load(open(_RR))
        gen = d["metadata"]["generated_at"]
        tests = [r for r in d.get("results", []) if r.get("unique_id", "").startswith("test.")]
        passed = sum(1 for r in tests if r.get("status") == "pass")
        total = len(tests)
        age = (datetime.now(timezone.utc)
               - datetime.fromisoformat(gen.replace("Z", "+00:00"))).total_seconds()
        ok = total and passed == total
        parts = [f"{'✅' if ok else '⚠️'} data refreshed {_human_age(age)}"]
        if total:
            parts.append(f"{passed}/{total} tests passing")
        return "_" + " · ".join(parts) + "_"
    except Exception:
        return None
