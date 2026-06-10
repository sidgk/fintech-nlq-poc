# datalake-metrics — a custom MCP server over a MetricFlow semantic layer

This replaces Cube with a **custom-MCP + dbt-semantic-layer** pattern: a small MCP
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

## How this maps to a two-repo production stack
A common production layout splits this across two repos. This POC keeps both sides
in one place so the whole loop is runnable:

| This POC | Two-repo production layout |
|---|---|
| `mcp_server/server.py` | the **MCP-server** repo |
| `dbt/models/semantic/*.yml` (MetricFlow) | the **data-engineering** (dbt) repo, on the gold models |
| Postgres gold | a cloud warehouse (Athena/Iceberg, Snowflake, BigQuery …) |
| Claude Desktop | whatever AI client calls the MCP server |

To go from POC → production: move the `models/semantic/*.yml` into the
data-engineering repo, point MetricFlow at the production warehouse (e.g.
`dbt-athena`), and run this same `query_metric` handler in the MCP-server repo.
(See `docs/metricflow/SETUP.md`.)

## Serving the Slack bot via MetricFlow (not Cube)
The Slack bot can answer **through this MCP server's MetricFlow layer instead of
Cube** — one env switch, no Cube involved:

```bash
SEMANTIC_BACKEND=metricflow      # default is "cube"
```

With it set, `bot/resolver.answer_question()` delegates to `bot/mf_backend.py`,
which: reads the metric catalog from this server (`list_metrics`), asks the LLM
for a MetricFlow spec `{metrics, group_by, order_by, limit}` (never SQL), and
executes it through this server's `query_rows()` — the **same handler** the AI
client calls. So accounts, payments, and any future domain are served via the MCP
server / MetricFlow.

End-to-end test (real MCP protocol over stdio, both domains):
```bash
DBT_HOST=127.0.0.1 DBT_PORT=55432 venv/bin/python mcp_server/test_e2e.py
```

**Reconciliation:** the MetricFlow path matches Cube exactly — `block_rate` by
risk agrees to 4 decimals and the account total (2000) is identical. Ratio metrics
return a 0–1 fraction in MetricFlow; the bot scales them to the app's 0–100 display
convention (`block_rate 0.0751 → 7.5%`) to match Cube's `100.0 * num/den`.

## Accuracy carries over
Reconciliation + quality tests (dbt), certification (`meta` on metrics), golden
evals (now run via `mf query`), additivity (`type: ratio` stays ratio-of-sums).
