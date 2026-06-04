# Process & workflow

How to run, operate, and extend this project. Pairs with `ARCHITECTURE.md` (what it
is) and `WALKTHROUGH.md` (how it works internally).

---

## Daily run

```bash
cd ~/Documents/fintech-nlq-poc

# 1. Bring up the whole stack (Postgres, Cube, Airflow x3)
docker compose up -d

# 2. Start the Slack bot (host process, Socket Mode)
set -a; source .env; set +a
source venv/bin/activate
cd bot && nohup python app.py > /tmp/slackbot.log 2>&1 &

# 3. (Optional) Run the data pipeline — also runnable from Airflow UI
cd ../dbt && DBT_PROFILES_DIR=$PWD ../venv/bin/dbt build --profiles-dir $PWD
```

Endpoints: Airflow http://localhost:8088 (admin/admin) · Cube http://localhost:4000 ·
Postgres 127.0.0.1:55432 (postgres/postgres, db `demo`).

Stop: `docker compose down` (data re-seeds on next `up`; the metadata DB persists in a volume).

---

## The two workflows

### A. Data pipeline workflow (build time)
1. Raw data lands in `raw.*` (here via `db/init.sql`; in prod, your ingestion).
2. Airflow DAG `dbt_medallion` runs dbt via Cosmos: silver (views) → gold (tables) → tests.
3. If tests pass, `gold.*` is the trusted serving layer Cube reads.

Trigger: Airflow UI → `dbt_medallion` → ▶, or `dbt build` from the CLI.

### B. Question-answering workflow (query time)
1. User asks in Slack → `bot/app.py`.
2. `bot/resolver.py` fetches Cube's catalog, asks Gemini to translate → query spec.
3. Spec → Cube `/load` → SQL on `gold.*` → rows → formatted reply in Slack.

---

## How to extend

### Add a new metric (most common task)
1. Edit `model/cubes/payments.yml` — add a `measure:` with a clear `description:` and
   **synonyms** (the LLM matches user words via these).
2. Add it to the `payments_overview` view in `model/views/payments_overview.yml`.
3. `docker compose restart cube` (or it hot-reloads in dev mode).
4. Ask it in Slack. No code change, no LLM change.

### Add a new source column / transform
1. Make sure it exists in `raw.*` (ingestion / `db/init.sql`).
2. Surface it in the silver model (`dbt/models/silver/stg_*.sql`).
3. Carry it into gold (`dbt/models/gold/*.sql`); add a test in `_gold.yml`.
4. `dbt build` (or run the Airflow DAG).
5. Expose it in Cube (`model/cubes/*.yml`) as a measure or dimension.

### Add a new dimension table
1. New silver `stg_*` + gold `dim_*` models.
2. New `model/cubes/<dim>.yml` + a `join:` from `payments.yml`.
3. Include its fields in the view.

### Change the LLM
- Edit `.env`: `LLM_PROVIDER=gemini|anthropic`, `GEMINI_MODEL=...` / `CLAUDE_MODEL=...`.
- Logic lives in `bot/resolver.py` (`_resolve_with_gemini` / `_resolve_with_anthropic`).

---

## Golden-question evals (recommended before any metric change)
Keep an `evals/` folder of `question → expected answer` pairs and re-run them whenever you
change a model or metric. This is how you earn trust to expand domain by domain. (Not yet
built — a good next step.)

---

## Conventions
- **Never commit secrets.** `.env` is gitignored; `.env.example` is the template.
- **Always test the resolver from the CLI before touching Slack:**
  `cd bot && python resolver.py "your question"`.
- **dbt tests are the contract.** A red test = a broken pipeline; fix before serving.
- **The LLM never writes SQL.** If you're tempted to let it, you're breaking the model.

---

## Production notes (how this maps to real infra)
- **Ingestion:** replace `db/init.sql` with your real landing process into `raw`.
- **Warehouse:** swap Cube's connection from local Postgres to Athena/Trino over Iceberg —
  same `model/` files, just a different data source.
- **Orchestration:** the same Cosmos DAG runs on managed Airflow (MWAA/Astronomer/Composer).
- **Caching:** Cube pre-aggregations matter once you're on a pay-per-query warehouse.
- **Visualization:** point Superset/Metabase at Cube's SQL API (port 15432) for dashboards.
