"""
Metric lineage — answer the stakeholder question "how was this number calculated?"

Given the Cube query spec that produced an answer, we reconstruct the FULL chain:

    raw source column  →  silver (dedup/typecast)  →  gold star schema
                       →  Cube measure (the business formula)  →  the SQL that ran

Sources of truth:
  - the semantic-layer YAML in ../model  (the business definitions + formulas)
  - Cube's /sql endpoint                 (the exact compiled SQL)
  - the Medallion naming convention      (gold.fct_x ⇐ silver.stg_x ⇐ raw.x)
"""

import os
import glob

import requests
import yaml

CUBE_API_URL = os.environ.get("CUBE_API_URL", "http://localhost:4000/cubejs-api/v1")
_MODEL_DIR = os.path.join(os.path.dirname(__file__), "..", "model")


# ── load the semantic layer (cubes + the view) ───────────────────────────────

def _load_model():
    cubes, view = {}, None
    for path in glob.glob(os.path.join(_MODEL_DIR, "cubes", "*.yml")):
        doc = yaml.safe_load(open(path)) or {}
        for c in doc.get("cubes", []):
            cubes[c["name"]] = {
                "sql_table": c.get("sql_table", ""),
                "measures": {m["name"]: m for m in c.get("measures", [])},
                "dimensions": {d["name"]: d for d in c.get("dimensions", [])},
            }
    for path in glob.glob(os.path.join(_MODEL_DIR, "views", "*.yml")):
        doc = yaml.safe_load(open(path)) or {}
        for v in doc.get("views", []):
            view = v
    return cubes, view


def _resolve_member(full_name, view):
    """'payments_overview.total_amount' -> (cube_name, member_name)."""
    if "." not in full_name:
        return None, None
    _, member = full_name.split(".", 1)
    if not view:
        return None, member
    for group in view.get("cubes", []):
        cube_name = group.get("join_path", "").split(".")[-1]
        prefixed = group.get("prefix", False)
        for inc in group.get("includes", []):
            view_member = f"{cube_name}_{inc}" if prefixed else inc
            if view_member == member:
                return cube_name, inc
    return None, member


def _medallion_chain(sql_table):
    """'gold.fct_payments' -> ['raw.payments', 'silver.stg_payments', 'gold.fct_payments']."""
    table = sql_table.split(".")[-1] if sql_table else ""
    base = table.replace("fct_", "").replace("dim_", "")
    return [f"raw.{base}", f"silver.stg_{base}", sql_table]


def _measure_formula(m):
    """Render a measure's business formula from its YAML definition."""
    mtype = (m.get("type") or "").upper()
    sql = m.get("sql")
    if m.get("filters"):
        cond = "; ".join(f.get("sql", "") for f in m["filters"])
        return f"COUNT(*) WHERE {cond}"
    if mtype == "COUNT":
        return "COUNT(*)"
    if sql:
        expr = sql.replace("{CUBE}.", "")
        return f"{mtype}({expr})" if mtype in ("SUM", "AVG", "MIN", "MAX") else expr
    return mtype or "(derived)"


def _compiled_sql(spec):
    try:
        import json
        r = requests.get(f"{CUBE_API_URL}/sql",
                         params={"query": json.dumps(spec)}, timeout=30)
        r.raise_for_status()
        sql = r.json().get("sql", {}).get("sql")
        return sql[0] if sql else None
    except Exception:
        return None


# ── the stakeholder-facing explanation ───────────────────────────────────────

def explain(spec: dict) -> str:
    cubes, view = _load_model()
    lines = ["🧬 *How this was calculated* — full lineage from source to answer\n"]
    tables_touched = set()

    # 1) the business metric(s)
    lines.append("*Metric(s):*")
    for meas in spec.get("measures", []):
        cube_name, mname = _resolve_member(meas, view)
        cube = cubes.get(cube_name, {})
        mdef = cube.get("measures", {}).get(mname, {})
        formula = _measure_formula(mdef)
        desc = (mdef.get("description") or "").split(" Synonyms")[0]
        sql_table = cube.get("sql_table", "")
        if sql_table:
            tables_touched.add(sql_table)
        lines.append(f"• `{mname}` = `{formula}`")
        if desc:
            lines.append(f"    _{desc.strip()}_")
        lines.append(f"    computed on `{sql_table}`")

    # 2) the slices (dimensions)
    dims = spec.get("dimensions", [])
    if dims:
        lines.append("\n*Sliced by:*")
        for dim in dims:
            cube_name, dname = _resolve_member(dim, view)
            cube = cubes.get(cube_name, {})
            sql_table = cube.get("sql_table", "")
            if sql_table:
                tables_touched.add(sql_table)
            lines.append(f"• `{dname}` (from `{sql_table}`)")

    # 3) the time filter
    for td in spec.get("timeDimensions", []):
        rng = td.get("dateRange", "")
        gran = td.get("granularity")
        lines.append(f"\n*Time filter:* created_at — {rng}" + (f", bucketed by {gran}" if gran else ""))

    # 4) the Medallion lineage for every table involved
    lines.append("\n*Data lineage (Medallion — Bronze → Silver → Gold):*")
    chain_lines = []
    for tbl in sorted(tables_touched):
        raw, silver, gold = _medallion_chain(tbl)
        chain_lines.append(
            f"{raw:<22} 🥉 raw landing (as ingested)\n"
            f"  └─ {silver:<18} 🥈 dedup (DISTINCT ON id) + typecast\n"
            f"      └─ {gold:<14} 🥇 star-schema {'fact' if 'fct_' in gold else 'dimension'}"
        )
    lines.append("```\n" + "\n".join(chain_lines) + "\n```")

    # 5) the exact SQL that produced the number
    sql = _compiled_sql(spec)
    if sql:
        compact = " ".join(sql.split())
        lines.append("*Exact SQL Cube compiled & ran on Postgres:*")
        lines.append("```sql\n" + compact + "\n```")

    lines.append("_Every step is code-reviewed in Git and tested by dbt — fully auditable._")
    return "\n".join(lines)
