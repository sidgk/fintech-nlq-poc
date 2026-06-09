# datalake-metrics — our own MCP server (mimics the company stack)

This replaces Cube with the **exact pattern your company uses**: a custom MCP
server that serves a **MetricFlow semantic layer** (defined in dbt, on the gold
models) to an AI client. **No Cube.** The AI never writes SQL.

```
Claude Desktop (AI)  ──MCP (stdio)──▶  server.py  ──▶  MetricFlow (dbt models/semantic/*.yml)
                                                          │  compiles metric → SQL
                                                          ▼
                                                       Postgres gold  (Athena/Iceberg in prod)
```

## What it exposes (the tools the AI can call)
- **`list_metrics()`** — what can I ask? (the menu)
- **`list_dimensions(metric)`** — how can I slice it?
- **`query_metric(metrics, group_by, order_by, limit)`** — the numbers

The AI only ever picks a **defined metric + dimensions**; MetricFlow turns that
into governed SQL. That's the governance guarantee — same as Cube, different engine.

## The semantic layer it serves
Lives in the dbt project (not here): `dbt/models/semantic/accounts.yml` and
`payments.yml` — `semantic_models` (gold table → entities/dimensions/measures) +
`metrics`. Metrics today: `account_count`, `active_account_count`,
`block_rate`, `termination_rate`, `payment_count`, `revenue`,
`payment_success_rate`, …

## Run it
Prereqs (already done in this repo): `pip install dbt-metricflow mcp`, the dbt
models built, and a **time spine** (`dbt/models/marts/metricflow_time_spine.sql`).

```bash
# sanity-check MetricFlow directly
cd dbt && DBT_PROFILES_DIR=$PWD DBT_HOST=127.0.0.1 DBT_PORT=55432 \
  ../venv/bin/mf query --metrics account_count --group-by account__industry

# the MCP server itself (speaks MCP over stdio — normally launched by the AI client)
venv/bin/python mcp_server/server.py
```

## Connect it to the AI (Claude Desktop)
1. Open **Claude Desktop → Settings → Developer → Edit Config** (or edit
   `~/Library/Application Support/Claude/claude_desktop_config.json`).
2. Paste the `mcpServers` block from `claude_desktop_config.example.json`
   (use **absolute paths**).
3. **Restart Claude Desktop.** You'll see a 🔌/tools icon for `datalake-metrics`.
4. Ask in plain English:
   > "How many accounts do we have by industry?"
   > "What's the block rate by risk level?"
   > "Payment success rate by card status."
   Claude will call `query_metric` and answer from MetricFlow.

## How this maps to your company
| Our POC | Your company |
|---|---|
| `mcp_server/server.py` | your **datalake-mcp** repo |
| `dbt/models/semantic/*.yml` (MetricFlow) | your **data-engineering** repo, on the gold models |
| Postgres gold | Athena/Iceberg gold |
| Claude Desktop | whatever AI client calls your MCP server |

To go from POC → company: move the `models/semantic/*.yml` into your
data-engineering repo, point MetricFlow at your warehouse (`dbt-athena`), and run
this same `query_metric` handler in your datalake-mcp server. (See
`docs/metricflow/SETUP.md`.)

## Accuracy carries over
Reconciliation + quality tests (dbt), certification (`meta` on metrics), golden
evals (now run via `mf query`), additivity (`type: ratio` stays ratio-of-sums).
