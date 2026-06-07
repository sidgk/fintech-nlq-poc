"""
Golden-question eval suite — the AI's regression guarantee.

Runs each golden question through the LIVE resolver and checks:
  - interpretation: did it pick the right measures + dimensions?
  - shape: expected row count
  - invariants: e.g. a rate must be 0-100

Fails loudly (exit code 1) on any regression, so it can gate CI / a deploy.

  python evals/run_evals.py
"""

import os
import sys

import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bot"))
from resolver import answer_question                       # noqa: E402

GOLDEN = os.path.join(os.path.dirname(__file__), "golden.yaml")


def _check(case):
    out = answer_question(case["question"])
    if "rows" not in out:
        return [f"resolver did not return data: {out}"]
    spec = out.get("query", {})
    rows = out.get("rows", [])
    fails = []

    if "measures" in case and set(spec.get("measures") or []) != set(case["measures"]):
        fails.append(f"measures {sorted(spec.get('measures') or [])} != {sorted(case['measures'])}")
    if "dimensions" in case and set(spec.get("dimensions") or []) != set(case["dimensions"]):
        fails.append(f"dimensions {sorted(spec.get('dimensions') or [])} != {sorted(case['dimensions'])}")
    if "rows" in case and len(rows) != case["rows"]:
        fails.append(f"row count {len(rows)} != expected {case['rows']}")
    if "min_rows" in case and len(rows) < case["min_rows"]:
        fails.append(f"row count {len(rows)} < min {case['min_rows']}")
    if "value_between" in case and rows:
        lo, hi = case["value_between"]
        val = None
        for v in rows[0].values():
            try:
                val = float(v)
            except (TypeError, ValueError):
                pass
        if val is None or not (lo <= val <= hi):
            fails.append(f"value {val} not in [{lo}, {hi}]")
    return fails


def main():
    cases = yaml.safe_load(open(GOLDEN))
    print(f"\n=== Golden-question evals ({len(cases)} cases) ===\n")
    failed = []
    for case in cases:
        fails = _check(case)
        if fails:
            print(f"  ❌ {case['question']}")
            for f in fails:
                print(f"       - {f}")
            failed.append(case["question"])
        else:
            print(f"  ✅ {case['question']}")

    print(f"\n{len(cases) - len(failed)}/{len(cases)} passed.")
    if failed:
        print(f"❌ {len(failed)} REGRESSION(S) — the AI mis-answered. Do not ship.")
        sys.exit(1)
    print("✅ All golden questions pass.")


if __name__ == "__main__":
    main()
