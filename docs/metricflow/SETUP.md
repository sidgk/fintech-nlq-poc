# Proper semantic layer for a dbt + custom-MCP stack (two repos)

Target: replace a hand-maintained per-metric registry (e.g. a `prompts.json` of
metric→SQL) with a real, declarative **semantic layer in YAML** on top of the dbt
gold models — governed, reconciled, certified — served by an MCP server.

## Who owns what

A two-repo split is the common production layout: a dbt repo for the data and its
meaning, and an MCP-server repo for the AI ↔ data connection.

| Repo | Owns | Add this |
|---|---|---|
| **data-engineering** (dbt) | the data + its meaning | `models/semantic/*.yml` — MetricFlow semantic_models + metrics, on the **gold** fact/dim models. THIS is the semantic layer. |
| **MCP server** | the AI ↔ data connection | change the server to resolve metrics via **MetricFlow** instead of hard-coded SQL + a per-metric JSON registry. |

## Why MetricFlow (not a custom YAML)
You *could* invent your own YAML + a YAML→SQL compiler — but that's reinventing
MetricFlow. MetricFlow IS the dbt-native YAML semantic layer; it compiles
`metric + group_by + time_range + filters → SQL` for you. You write YAML; it does
the SQL. Single source of truth, reuses the gold star schema, governed in Git.

## The semantic layer (in the dbt repo)
One `semantic_model` per gold table + `metrics` on top. Concepts map 1:1 from the
Cube model we built:
- cube → `semantic_model` (`model: ref('fct_x')`)
- measure (count / sum) → `measure` (agg: count_distinct / sum, optional CASE expr)
- ratio (success_rate, block_rate) → `metric` of `type: ratio` (numerator/denominator)
- dimensions → dimensions (categorical / time)
- `meta.certified` → `meta: {certified: true}`

(See `accounts_semantic_model.yml` in this folder for a full worked example.)

Validate locally:
```bash
pip install dbt-metricflow
dbt parse
mf list metrics
mf query --metrics payment_success_rate --group-by metric_time__day
```

## The MCP server change (in the MCP-server repo)
Replace "one hand-written SQL per metric" with one of these:

**Option A — generic tool (recommended).** One MCP tool:
```
query_metric(metric: str, group_by: list[str] = [], time_range: str = None,
             filters: dict = {})
```
Its handler calls MetricFlow (the `mf` CLI or the MetricFlow Python API) →
compiled SQL → Athena → rows. The list of valid `metric`/`group_by` values comes
from MetricFlow's catalog (`mf list metrics` / the semantic manifest), so the AI
is constrained to defined metrics — the governance guarantee.

**Option B — keep per-metric tools, but GENERATE them.** Auto-build the per-metric
tool definitions from MetricFlow's metric catalog at startup, so they're never
hand-edited again. Same effect; less change to the AI's interface.

## Migration (incremental, low-risk — don't rip out the old registry on day one)
1. Pick ONE metric already in the legacy registry (e.g. `payment_success_rate`).
2. Define it in MetricFlow in the dbt repo (semantic_model + metric).
3. Add a `query_metric` path in the MCP server that resolves it via MetricFlow.
4. Compare: MetricFlow result vs the old hand-written SQL result → must match
   (this is the reconciliation gate for the migration).
5. Repeat metric by metric; delete each hand-written SQL + JSON entry as it moves.
6. When all metrics are in MetricFlow, the legacy registry is fully generated (or gone).

## Accuracy carries over (all of it — none was Cube-specific)
- **Reconciliation tests** — dbt tests in the dbt repo (gold == independent
  recompute from raw). Plus the migration check in step 4 above.
- **Certification** — `meta.certified` per metric; the MCP server can refuse
  uncertified metrics for exec users.
- **Golden evals** — question → expected metric, run against MetricFlow.
- **Additivity** — `type: ratio` metrics stay ratio-of-sums automatically.

## Questions to settle before migrating
1. Does the MCP server call the warehouse directly today, or through a query engine?
   (MetricFlow needs a dbt adapter for the warehouse — `dbt-athena`/`dbt-trino`/etc.)
2. Where does the legacy registry map a metric → its SQL today? (That's what
   MetricFlow replaces — confirm the seam so the swap is clean.)
3. Generic `query_metric` tool, or auto-generated per-metric tools? (A vs B above.)
