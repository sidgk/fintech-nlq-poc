# Demo Script — "Ask your data in Slack", grounded in a governed pipeline

A 10-minute walkthrough for your manager. The story: **a stakeholder asks a
plain-English question in Slack and gets a trustworthy number — because the data
flows through a governed Medallion pipeline (dbt) and a semantic layer (Cube),
and the LLM only ever picks from defined metrics. It never writes SQL.**

---

## The architecture (one picture)

```
  Slack (NLQ)
     │  "total revenue by merchant category last 30 days"
     ▼
  Resolver  ── Gemini 3.5 Flash turns the question into a Cube QUERY SPEC (JSON),
     │         using ONLY members that exist. Never SQL.
     ▼
  Cube  (semantic layer = model/*.yml)  ── compiles the spec to SQL
     │
     ▼
  Postgres ── GOLD layer only
     ▲
     │  built by dbt, orchestrated by Airflow + Cosmos:
     │
  ┌──────────────── dbt Medallion (Airflow DAG `dbt_medallion`) ───────────────┐
  │  🥉 BRONZE  raw.*        landing tables, "as ingested" (messy: dupes, text  │
  │                          timestamps, mixed-case status)                     │
  │      └─▶ 🥈 SILVER  silver.stg_*   views: typecast + dedup (DISTINCT ON)     │
  │            └─▶ 🥇 GOLD  gold.fct_payments + gold.dim_*  star schema + tests  │
  └─────────────────────────────────────────────────────────────────────────────┘
```

**Two governance gates your manager cares about:**
1. **dbt tests** run inside the pipeline — `unique(fct_payments.id)` proves dedup,
   `accepted_values(status)` proves normalization. Bad data can't reach Gold.
2. **The LLM is constrained** to the semantic layer — it can only choose from
   metrics/dimensions defined in `model/*.yml`, validated before execution.

---

## Endpoints & credentials

| Thing | URL / command | Login |
|---|---|---|
| **Airflow** (orchestration) | http://localhost:8088 | `admin` / `admin` |
| **Cube Playground** (semantic layer) | http://localhost:4000 | — |
| **Postgres** (data) | `127.0.0.1:55432` db `demo` | `postgres` / `postgres` |
| **Slack** | DM or `@Data Bot` in a channel | — |

---

## Pre-demo checklist (run once, ~1 min before)

```bash
cd ~/Documents/fintech-nlq-poc
docker compose ps          # db, cube, airflow-meta, airflow-scheduler, airflow-webserver all Up
curl -s localhost:4000/cubejs-api/v1/meta | head -c 60   # Cube serving
pgrep -fl "python app.py"  # Slack bot running (if not, see "Start the bot" below)
```

If the Slack bot isn't running:
```bash
cd ~/Documents/fintech-nlq-poc
set -a; source .env; set +a
source venv/bin/activate
cd bot && nohup python app.py > /tmp/slackbot.log 2>&1 &
```

---

## The walkthrough (what to click + what to say)

### 1. Show the messy raw data (BRONZE) — "this is what lands"
```bash
docker exec fintech-nlq-poc-db-1 psql -U postgres -d demo -c \
  "select count(*) total, count(distinct id) distinct_ids from raw.payments;"
docker exec fintech-nlq-poc-db-1 psql -U postgres -d demo -c \
  "select distinct status from raw.payments order by 1;"
```
> "Raw payments have **40 duplicate ingests** (20,040 rows / 20,000 ids) and
> **mixed-case statuses** (`Succeeded`, ` succeeded`, `FAILED`). This is realistic
> landed data. We never let analysts touch this directly."

### 2. Show the dbt models (in VS Code) — "this is how we derive everything"
Open `dbt/models/` and show the three layers:
- `models/bronze/_sources.yml` — declares the raw tables (lineage starts here)
- `models/silver/stg_payments.sql` — `DISTINCT ON (id) … ORDER BY id, created_at DESC`
  (dedup) + `lower(trim(status))` (normalize) + `created_at::timestamp` (typecast)
- `models/gold/fct_payments.sql` — the fact table (one row per payment)
- `models/gold/_gold.yml` — the **tests** (unique, not_null, accepted_values, relationships)

> "When you ask *how did we get `total_amount` or `success_rate`* — it's not
> hand-typed. It's `gold.fct_payments`, built by these models, tested on every run."

### 3. Run the pipeline in Airflow — "and it's orchestrated, not run by hand"
Open **http://localhost:8088** → DAG `dbt_medallion` → **Graph** view.
> "Cosmos renders **each dbt model and test as its own Airflow task**. In prod this
> runs on a schedule via Cosmos on Airflow; locally it's the same DAG."

Click **Trigger DAG** (▶). Watch Silver → Gold → tests go green. (~30s)

### 4. Show the clean star schema (GOLD)
```bash
docker exec fintech-nlq-poc-db-1 psql -U postgres -d demo -c \
  "select count(*) from gold.fct_payments;"   -- 20000: duplicates gone
```

### 5. Show the semantic layer (CUBE)
Open **http://localhost:4000** → pick measure **Payments overview → Total amount**,
dimension **Merchants category** → Run. Show the **generated SQL** tab.
> "This is the governed layer. `revenue` = `total_amount` is defined once, in
> `model/cubes/payments.yml`, code-reviewed in Git. The LLM reads these definitions."

### 6. Ask in Slack — the payoff
In Slack, DM the bot (or `@Data Bot` in a channel):
> **total revenue by merchant category last 30 days**

> **how many successful payments did we have last week?**

> **success rate by card brand**

> "The bot shows the **answer AND the resolved query spec** — proof the LLM picked
> from our metrics, never wrote SQL. That's what makes it safe for finance."

---

## The one-liner if he asks "why not just text-to-SQL?"

> "Text-to-SQL lets the model invent joins and filters on raw tables — ungovernable
> for finance. Here the model is a **translator** constrained to a reviewed semantic
> layer, sitting on a **tested** Medallion pipeline. Every number is reproducible
> and auditable from raw to answer."

---

## Reset / teardown

```bash
# Re-run the whole pipeline from the UI (Trigger DAG) or CLI:
cd ~/Documents/fintech-nlq-poc/dbt && DBT_PROFILES_DIR=$PWD ../venv/bin/dbt build --profiles-dir $PWD

# Stop everything (keeps images; data re-seeds on next up):
docker compose down

# Full fresh start:
docker compose up -d
```
