# The whole system, explained — raw ingestion → Slack answer

Everything in this project, what each piece does, how they talk, and the exact
configuration. Read top to bottom once and you'll own it.

---

## 0. The one-paragraph mental model

There are **two separate timelines**:

- **BUILD TIME** (the data pipeline): messy raw data is cleaned and shaped into a
  trustworthy star schema. Run by **Airflow → Cosmos → dbt**. Happens on a
  schedule (or when you click "trigger"). *Nothing to do with Slack or the LLM.*
- **QUERY TIME** (answering a question): someone asks in **Slack**, an **LLM
  (Gemini)** translates it into a safe query against the **semantic layer
  (Cube)**, which runs SQL on the **gold** tables and returns a number.

The LLM only ever runs at query time, and it only ever sees the semantic layer's
catalog — never the database, never raw SQL.

```
BUILD TIME  (Airflow/Cosmos/dbt)          QUERY TIME  (Slack/Gemini/Cube)
raw → silver → gold  ─────────────────▶   Slack → resolver → Gemini → Cube → gold → Slack
                         (gold is the handoff point between the two timelines)
```

---

## 1. The components — what each one is and why it exists

| # | Component | Tech / version | Runs where | One-line job |
|---|---|---|---|---|
| 1 | **Database** | PostgreSQL 16 | Docker container `db` | Stores all data in 3 schemas: `raw`, `silver`, `gold` |
| 2 | **Transformation** | dbt Core 1.9 | host venv **and** inside Airflow | SQL models that build silver + gold from raw, with tests |
| 3 | **dbt⇄Airflow bridge** | Astronomer **Cosmos** 1.7 | inside Airflow | Turns each dbt model/test into an Airflow task |
| 4 | **Orchestrator** | Apache **Airflow** 2.10.5 | Docker (3 services) | Schedules/runs the dbt DAG; UI to watch it |
| 5 | **Semantic layer** | **Cube** (latest) | Docker container `cube` | Defines metrics/dimensions; compiles query specs → SQL |
| 6 | **LLM / brain** | **Gemini 3.5 Flash** (Google AI Studio) | Google's API (called from host) | Question → JSON query spec (never SQL) |
| 7 | **Resolver** | Python (`bot/resolver.py`) | host venv | Glue: Slack question → Gemini → Cube → rows |
| 8 | **Front door** | **Slack** + `slack-bolt` (`bot/app.py`) | host venv, Socket Mode | Receives questions, posts answers |
| 9 | **Wiring/runtime** | **Docker Compose** | host | Defines & networks all containers |

Key idea about responsibilities:
- **dbt = transforms** (it reshapes data; it is NOT a server, NOT an API).
- **Cube = semantic layer + API** (it serves metrics; it does NOT transform data).
- They're complementary: dbt makes the data *correct*; Cube makes it *askable*.
- We use **dbt Core** (free, open source) + **Cube** for the API, instead of dbt's
  own "Semantic Layer" which needs **paid dbt Cloud** to serve an API.

---

## 2. BUILD TIME — step by step (raw ingestion → gold)

### Step 1 — Ingestion creates the BRONZE layer
File: `db/init.sql` (auto-runs the first time the Postgres container starts).
It creates a schema `raw` and loads **deliberately messy** data — this simulates
"data engineering dumped the raw feed here":
- `raw.payments` — 20,000 payments **plus 40 duplicate rows** (same id, older copy),
  `created_at` stored as **text**, `status` in **mixed case** (`Succeeded`, ` succeeded`, `FAILED`).
- `raw.customers` (500), `raw.merchants` (100), `raw.cards` (800).

This is the only data that "lands." Everything else is derived from it.

### Step 2 — Airflow discovers the pipeline
The **airflow-scheduler** container continuously parses `airflow/dags/dbt_medallion_dag.py`.
That file uses **Cosmos** to read the dbt project and build a DAG. Cosmos runs
`dbt ls` (it has dbt installed in an isolated venv at `/opt/dbt_venv`) to list every
model and test, and renders **one Airflow task per model and per test**:

```
stg_customers_run  stg_merchants_run  stg_cards_run  stg_payments_run     (SILVER)
        └──────────────┬───────────────┘
        dim_customers.run/.test   dim_merchants.run/.test   dim_cards.run/.test
                              fct_payments.run / fct_payments.test          (GOLD)
```

### Step 3 — You trigger the DAG (UI or schedule)
Airflow UI at **http://localhost:8088** (login `admin`/`admin`) → DAG `dbt_medallion`
→ Trigger. Airflow's **LocalExecutor** runs the tasks in dependency order.

### Step 4 — dbt builds SILVER (clean views)
Each `dbt/models/silver/stg_*.sql` runs. Example `stg_payments.sql`:
- `SELECT DISTINCT ON (id) ... ORDER BY id, created_at::timestamp DESC` → keeps the
  **latest** copy of each payment, dropping the 40 duplicates (Postgres has no
  `QUALIFY`, so `DISTINCT ON` is the idiom).
- `created_at::timestamp` → **typecasts** text → real timestamp.
- `lower(trim(status))` → **normalizes** `Succeeded`/` succeeded`/`FAILED` → clean values.
Materialized as **views** in schema `silver` (cheap, always fresh).

### Step 5 — dbt builds GOLD (the star schema, tested)
`dbt/models/gold/*.sql` read from silver and materialize as **tables** in schema `gold`:
- `fct_payments` — the **fact** table, one row per payment (adds `amount` = cents/100).
- `dim_merchants`, `dim_cards`, `dim_customers` — the **dimensions**.
This is a classic **star schema**: a central fact surrounded by dimensions.

### Step 6 — dbt runs the TESTS (the `.test` tasks)
Defined in `dbt/models/gold/_gold.yml`. These are the **trust gates**:
- `unique(fct_payments.id)` → proves dedup worked (would fail if duplicates remained).
- `accepted_values(status in succeeded/failed/refunded/pending)` → proves normalization.
- `not_null`, and `relationships` (every payment's merchant_id exists in dim_merchants).
If any test fails, the DAG goes **red** and bad data never gets trusted.

**Result of build time:** `gold.fct_payments` (20,000 clean rows) + dims, ready to serve.

---

## 3. How CUBE turns gold tables into "metrics" (the semantic layer)

Cube reads the folder `model/` (mounted into its container). Each YAML file maps a
**table** to **business concepts**. `model/cubes/payments.yml`:

```yaml
cubes:
  - name: payments
    sql_table: gold.fct_payments        # ← points at the GOLD table dbt built
    measures:
      - name: total_amount              # "revenue"
        type: sum
        sql: "{CUBE}.amount_cents / 100.0"
        description: "...Synonyms: revenue, total value, payment volume..."
      - name: succeeded_count           # "approved"
        type: count
        filters: [{ sql: "{CUBE}.status = 'succeeded'" }]
      - name: success_rate
        sql: "100.0 * {succeeded_count} / NULLIF({count}, 0)"
    dimensions:
      - name: status   {type: string}
      - name: created_at {type: time}   # enables "last 30 days"
    joins:
      - name: merchants
        sql: "{CUBE}.merchant_id = {merchants}.id"
        relationship: many_to_one
```

- **measures** = the numbers (revenue, counts, rates).
- **dimensions** = the slices (status, time, and via joins: merchant category, card brand…).
- **joins** = defined once, so nobody re-derives them.
- **descriptions + synonyms** = plain-English meaning. **This is what the LLM reads.**
- `model/views/payments_overview.yml` flattens payments + its joined dims into one
  surface (`payments_overview`) — the clean thing the LLM prefers.

Cube serves this as a REST API at `http://localhost:4000/cubejs-api/v1`. Two endpoints
matter:
- `GET /meta` → the **catalog** (all cubes, measures, dimensions, descriptions).
- `POST /load` → give it a **query spec**, it compiles SQL and returns rows.

Config (in `docker-compose.yml`): `CUBEJS_DEV_MODE=true` (no auth, enables Playground),
`CUBEJS_DB_HOST=db`, `CUBEJS_DB_NAME=demo` — Cube talks to Postgres over the Docker
network as `db:5432`.

---

## 4. The LLM — what it is, what it does, what it does NOT do

- **Model:** `gemini-3.5-flash` via **Google AI Studio's free tier** (set in `.env` as
  `GEMINI_MODEL`; provider chosen by `LLM_PROVIDER=gemini`). We picked it because it's
  free (no Anthropic credits needed), fast, and supports a strict **JSON output mode**.
  (The code also supports Claude via `LLM_PROVIDER=anthropic` — same logic, different vendor.)
- **Its only job:** read the Cube catalog + the user's question, and emit a **JSON query
  spec** that uses *only members that exist in the catalog*. That's it.
- **What it does NOT do:** it never writes SQL, never connects to Postgres, never sees
  raw data. It's a **constrained translator**, not a query engine. That constraint is
  the entire trust story for finance.

How the call is made (`bot/resolver.py`, function `_resolve_with_gemini`): a `POST` to
`https://generativelanguage.googleapis.com/v1beta/models/gemini-3.5-flash:generateContent?key=...`
with:
- `systemInstruction` = the rules + the full catalog (so the model knows the allowed members),
- `contents` = the user's question,
- `generationConfig.responseMimeType = "application/json"` (forces valid JSON, no markdown),
- `thinkingConfig.thinkingBudget = 0` and `temperature = 0` (fast, deterministic — this is
  mechanical translation, not creative reasoning).

---

## 5. QUERY TIME — step by step (Slack question → number on screen)

### Step 1 — You ask in Slack
`@Data Bot total revenue by merchant category last 30 days`

### Step 2 — Slack delivers the event (no public URL)
The bot (`bot/app.py`) runs with **slack-bolt** in **Socket Mode** — it opens an
**outbound** WebSocket to Slack using `SLACK_APP_TOKEN` (`xapp-…`). Slack pushes the
`app_mention` event down that socket. (That's why you never needed ngrok or a public
server.) `SLACK_BOT_TOKEN` (`xoxb-…`) is used to *post* the reply back.

### Step 3 — app.py handles the event
`@app.event("app_mention")` fires. It strips the `@Data Bot` prefix and calls
`answer_question(question)` from the resolver.

### Step 4 — Resolver fetches the catalog
`fetch_catalog()` does `GET {CUBE_API_URL}/meta` and renders a compact text list of every
measure & dimension **with their descriptions/synonyms**. This is the menu the LLM may order from.

### Step 5 — Resolver asks Gemini to translate
`question_to_query()` builds the system prompt (catalog + rules) and calls Gemini. Gemini
returns JSON like:
```json
{ "measures": ["payments_overview.total_amount"],
  "dimensions": ["payments_overview.merchants_category"],
  "timeDimensions": [{"dimension":"payments_overview.created_at","dateRange":"last 30 days"}] }
```
Every name in there exists in the catalog — that's enforced by giving the model only the catalog.

### Step 6 — Resolver runs the spec through Cube
`run_query()` does `POST {CUBE_API_URL}/load` with that spec. **Cube** (not the LLM) now:
- looks up the members in `model/*.yml`,
- compiles them into real SQL (`SUM(amount_cents/100.0)`, `GROUP BY merchant.category`,
  `WHERE created_at >= now() - 30 days`, joining `gold.fct_payments` to `gold.dim_merchants`),
- runs that SQL on **Postgres gold tables**,
- returns rows as JSON.

### Step 7 — Resolver returns, app.py formats, Slack displays
`answer_question` returns `{query: <spec>, rows: [...]}`. `format_reply()` in `app.py`
renders the rows as a table and appends the line `resolved via semantic layer: <spec>`
(the proof the LLM stayed inside the guardrails). `say(...)` posts it to Slack.

---

## 6. Trace ONE number end-to-end: "total revenue"

```
raw.payments.amount_cents (text-ish int, may be a dup row)
  ─[dbt silver stg_payments]→  amount_cents (deduped, typed)
  ─[dbt gold fct_payments]→    gold.fct_payments.amount_cents (+ amount column)
  ─[Cube measure total_amount = SUM(amount_cents/100.0)]→  defined in model/cubes/payments.yml
  ─[LLM maps "revenue" → payments_overview.total_amount]→  via the description's synonyms
  ─[Cube compiles spec → SQL → runs on gold]→  142518.90 for "travel"
  ─[app.py formats]→  shown in Slack
```

Every hop is inspectable and reproducible. That's what "governed" means.

---

## 7. Every backend configuration we set (the inventory)

**`docker-compose.yml`** — 6 services on one Docker network:
- `db` (Postgres 16): user/pass `postgres`, db `demo`, host port **55432**→container 5432,
  runs `db/init.sql` on first boot.
- `cube`: dev mode on, reads `./model`, connects to `db:5432`, ports 4000 + 15432.
- `airflow-meta` (Postgres 16): Airflow's own metadata DB (separate from analytics).
- `airflow-init`: runs `airflow db migrate` + creates `admin/admin` (uses the image's
  default entrypoint so it works under the host UID).
- `airflow-scheduler`: LocalExecutor, parses DAGs, runs tasks.
- `airflow-webserver`: UI on host **8088**→container 8080.
- Airflow containers run as `${AIRFLOW_UID}` (= your host uid 501) so they can write the
  mounted `dbt/` + logs; they set `DBT_HOST=db`, `DBT_PORT=5432` so dbt reaches Postgres
  over the Docker network.

**`airflow/Dockerfile`** — `apache/airflow:2.10.5` + Cosmos in the airflow env + dbt in an
**isolated venv** `/opt/dbt_venv` (keeps dbt's deps from clashing with Airflow's).

**`airflow/dags/dbt_medallion_dag.py`** — Cosmos `DbtDag` with:
- `ProjectConfig("/opt/airflow/dbt")`, `ProfileConfig(profiles.yml)`,
- `ExecutionConfig(dbt_executable_path="/opt/dbt_venv/bin/dbt")`,
- `RenderConfig(load_method=DBT_LS)`, `schedule=None` (manual trigger).

**`dbt/`**:
- `dbt_project.yml` — silver=views, gold=tables.
- `profiles.yml` — Postgres target; host/port from `DBT_HOST`/`DBT_PORT`
  (default `127.0.0.1:55432` on the host; `db:5432` inside Airflow).
- `macros/generate_schema_name.sql` — write to schemas named exactly `silver`/`gold`.
- `models/bronze/_sources.yml`, `models/silver/stg_*.sql`, `models/gold/{fct,dim}_*.sql`, `_gold.yml`.

**`model/`** (Cube) — `cubes/{payments,cards,merchants,customers}.yml` (each `sql_table:
gold.*`) + `views/payments_overview.yml`.

**`bot/`** — `resolver.py` (Gemini + Cube glue), `app.py` (Slack Socket Mode).

**`.env`** (gitignored secrets) — `GEMINI_API_KEY`, `LLM_PROVIDER=gemini`,
`GEMINI_MODEL=gemini-3.5-flash`, `SLACK_BOT_TOKEN`, `SLACK_APP_TOKEN`, `CUBE_API_URL`,
`AIRFLOW_UID`. (`ANTHROPIC_API_KEY` is present but unused while provider=gemini.)

**Ports we remapped** (your Mac already used the defaults): Postgres **55432** (5432 taken),
Airflow **8088** (8080 taken).

---

## 8. Who talks to whom (the wire map)

```
Slack  ⇄(WebSocket, Socket Mode)⇄  app.py ──calls──▶ resolver.py
resolver.py ──HTTP GET /meta, POST /load──▶ Cube (:4000)
resolver.py ──HTTPS──▶ Gemini API (generativelanguage.googleapis.com)
Cube ──SQL over Docker net──▶ Postgres db:5432  (reads gold.*)
Airflow scheduler ──Cosmos──▶ dbt (/opt/dbt_venv) ──SQL──▶ Postgres db:5432 (writes silver/gold)
dbt-from-host ──▶ Postgres 127.0.0.1:55432
Airflow services ──▶ airflow-meta (its own Postgres)
```

That's the entire system. Build time fills `gold`; query time reads `gold`; the LLM only
ever picks names from Cube's catalog and never touches the database.
