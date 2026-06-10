# Getting started — MetricFlow semantic layer + an MCP server

A complete, from-scratch guide: what MetricFlow is, how it talks to dbt and the
warehouse, how to install and configure it, how the MCP server serves it to an AI,
and how the project is structured as more data domains are added.

---

## 1. Is MetricFlow part of dbt? (and the pieces)

**MetricFlow is dbt's semantic-layer engine, built by dbt Labs.** It is *not*
bundled in `dbt-core` — on dbt Core you add it with one package:

```bash
pip install dbt-metricflow      # installs MetricFlow + the `mf` CLI
```

The pieces and how they relate:

| Piece | What it is | Where it lives |
|---|---|---|
| **dbt** | builds your tables (raw → gold) | the dbt project |
| **Semantic models + metrics** | YAML: measures, dimensions, metrics on the gold tables | **inside the dbt project** (`models/semantic/`) |
| **MetricFlow (`mf`)** | reads those YAML definitions and **compiles a metric → SQL** | the `dbt-metricflow` package |
| **MCP server** | exposes the metrics to an AI as callable tools | a small service (here `mcp_server/`) |
| **Warehouse adapter** | how MetricFlow connects to the DB | `dbt-postgres` / `dbt-athena` / `dbt-trino` … |

So the semantic layer **is part of the dbt project** (the YAML); MetricFlow is the
engine that runs it; the MCP server is the connector to the AI.

---

## 2. How it communicates (the flow)

```
YAML (models/semantic/*.yml)
   │  dbt parse
   ▼
target/semantic_manifest.json          ← MetricFlow's compiled definition
   │  mf query --metrics X --group-by Y
   ▼
MetricFlow compiles → SQL  ──▶  warehouse (Postgres / Athena over Iceberg)  ──▶ rows
   ▲
   │  the MCP server calls `mf query` (or MetricFlow's Python API)
AI client ──MCP──▶ MCP server
```

1. You define metrics in YAML.
2. `dbt parse` turns the project into a **semantic manifest**.
3. MetricFlow reads the manifest; given a metric + grouping, it **writes the SQL**
   and runs it through the dbt connection.
4. The MCP server wraps that as tools (`list_metrics`, `query_metric`) so an AI can
   call it. The AI never writes SQL — it picks a defined metric.

---

## 3. Install & configure (step by step)

**Prerequisites:** a dbt project with gold models, and a warehouse connection in
`profiles.yml`.

```bash
# 1. Install MetricFlow (+ the MCP SDK for the server)
pip install dbt-metricflow mcp

# 2. Add a TIME SPINE model (MetricFlow requires one date dimension).
#    models/marts/metricflow_time_spine.sql  (one row per day) + a YAML marking it:
#      models:
#        - name: metricflow_time_spine
#          time_spine: { standard_granularity_column: date_day }
#          columns: [{ name: date_day, granularity: day }]
dbt run --select metricflow_time_spine

# 3. Add semantic models + metrics (models/semantic/*.yml) — see section 5.

# 4. Build the semantic manifest, then sanity-check:
dbt parse
mf list metrics
mf query --metrics account_count --group-by account__industry

# 5. Run the MCP server (it shells out to `mf query`):
python mcp_server/server.py        # speaks MCP over stdio
```

**Gotcha we hit:** MetricFlow needs the **time spine** or every query errors with
*"At least one time spine must be configured."* Build it once; it serves all domains.

---

## 4. Wiring the MCP server to an AI client

The MCP server speaks the MCP protocol over **stdio**; the AI client launches it.
For a desktop AI client, add a server entry to its MCP config (absolute paths):

```json
{
  "mcpServers": {
    "datalake-metrics": {
      "command": "/abs/path/venv/bin/python",
      "args": ["/abs/path/mcp_server/server.py"]
    }
  }
}
```

Restart the client → it discovers the tools → ask in plain English
("how many accounts by industry?") → the client calls `query_metric` →
MetricFlow answers. (See `mcp_server/README.md`.)

For a **production / service** deployment, the same `query_metric` handler runs
inside your long-running MCP service, pointed at the production warehouse adapter
(e.g. `dbt-athena`) instead of local Postgres.

---

## 5. Project structure — one domain, then many

**One domain (what's in this repo):**
```
dbt/models/
├── silver/   stg_*.sql
├── gold/     fct_payments.sql · dim_accounts.sql
├── marts/    metricflow_time_spine.sql        ← shared by ALL domains
└── semantic/
    ├── payments.yml      ← semantic_models + metrics for payments
    └── accounts.yml      ← semantic_models + metrics for accounts
```

**Many domains** (e.g. a payments source, a CRM like HubSpot, a tracker like Jira)
— each domain owns its gold models and **its own semantic file(s)**. Two layouts,
both valid; pick one and be consistent:

**(a) by layer (simple, fewer folders):**
```
dbt/models/
├── gold/      fct_payments · dim_accounts · fct_hubspot_deals · dim_jira_issues …
├── marts/     metricflow_time_spine.sql
└── semantic/
    ├── payments.yml
    ├── accounts.yml
    ├── hubspot.yml      ← HubSpot semantic models + metrics
    └── jira.yml         ← Jira semantic models + metrics
```

**(b) by domain (scales better for many sources / teams):**
```
dbt/models/
├── shared/
│   └── metricflow_time_spine.sql
├── payments/
│   ├── gold/ …
│   └── semantic/payments.yml
├── hubspot/
│   ├── gold/ …
│   └── semantic/hubspot.yml
└── jira/
    ├── gold/ …
    └── semantic/jira.yml
```

Key facts about multiple domains:
- **One dbt project, one MetricFlow graph, one time spine.** All metrics live in a
  single namespace, so `list_metrics` shows every domain's metrics and
  `query_metric` works across any of them — through the **same MCP server**.
- **Each domain is independent** unless its semantic models share an `entity`
  (a join key) — then MetricFlow can join across them. Usually each source is its
  own island (HubSpot deals don't join Jira issues).
- **Adding a new domain = add its gold models + one `semantic/<domain>.yml`**, then
  `dbt parse`. No MCP-server change needed — it already serves whatever metrics
  exist. That's the whole point: the metric catalog is data, not code.
- Prefix metric/measure names per domain to avoid clashes (`hubspot_deal_count`,
  `jira_open_issues`, `payment_success_rate`).

---

## 6. Accuracy carries across every domain
Each domain reuses the same gates: dbt quality + **reconciliation** tests on its
gold models, `meta` certification on its metrics, golden-question evals (run via
`mf query`), and ratio metrics stay ratio-of-sums. See `docs/accuracy.md`.

---

## 7. Quick reference
```bash
dbt parse                                   # rebuild the semantic manifest after YAML edits
mf list metrics                             # the menu
mf list dimensions --metrics account_count  # how to slice one
mf query --metrics revenue --group-by payment__status --order -revenue --limit 10
python mcp_server/server.py                  # run the MCP server
```
