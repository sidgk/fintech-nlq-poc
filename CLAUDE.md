# CLAUDE.md — Fintech NLQ project

Context for Claude Code. Read this first. We are EXTENDING an existing working
POC, not starting over.

## Goal
A stakeholder asks a plain-English question in Slack and gets the answer back,
computed from our data via a governed **semantic layer** (never raw text-to-SQL).

## What a semantic layer is (so we stay aligned)
It's the `model/` folder — plain YAML files, one per table, each defining:
- measures: the numbers (e.g. total_amount, succeeded_count)
- dimensions: the slices (e.g. status, cards.brand, created_at)
- joins: how tables connect, defined once
Cube reads this folder and serves it as an API. We do NOT use dbt MetricFlow
(that's dbt's own semantic layer; on dbt Core it can't serve an API without paid
dbt Cloud, and Cube already serves + caches). dbt = transforms; Cube = semantic layer.

## Architecture
raw tables (Postgres)  ->  dbt Core (staging: typecast+dedup -> marts: fct/dim)
  ->  Cube (measures+dimensions+joins on the marts, served via API)
  ->  Resolver (Claude: question -> validated query spec, never SQL)
  ->  Slack bot

## PHASE 0 — get the existing POC running first (tonight's demo)
The repo already has: db/init.sql (sample data), model/ (Cube semantic layer),
bot/ (resolver + Slack bot), docker-compose.yml (Postgres + Cube).
1. Make sure Docker Desktop is running (open -a Docker on macOS; wait for it).
2. `docker compose up -d` — starts Postgres (auto-seeded ~20k payments) + Cube.
3. Open http://localhost:4000 (Cube Playground). Confirm you can pick a measure
   like "Payments overview Succeeded count" and see data. THIS is the demo.
4. Test the AI brain from the terminal (needs ANTHROPIC_API_KEY in .env):
   `cd bot && python resolver.py "total revenue by merchant category last 30 days"`
   It prints the resolved query spec + the real numbers.
5. (Optional) Wire Slack: see README Step 2. Socket Mode, no public URL needed.

## PHASE 1 — dbt Medallion + Airflow/Cosmos  ✅ BUILT (2026-06-04)
The full governed pipeline is implemented and verified. Layout:
1. **Bronze**: `db/init.sql` seeds a `raw` schema (intentionally messy — 40 dup
   payments, text timestamps, mixed-case status) so Silver has real work.
2. **dbt Core** under `dbt/` (dbt-postgres 1.9, profile `fintech`):
   - `models/bronze/_sources.yml` — declares `raw.*` sources
   - `models/silver/stg_*.sql` — typecast + dedup (`DISTINCT ON (id) … ORDER BY
     id, created_at DESC`), materialized as views in schema `silver`
   - `models/gold/{fct_payments,dim_*}.sql` — star schema, tables in schema `gold`
   - `models/gold/_gold.yml` — tests (unique/not_null/accepted_values/relationships)
   - `macros/generate_schema_name.sql` — writes to `silver`/`gold` verbatim
3. **Cube repointed** to `gold.fct_payments` / `gold.dim_*`. Resolver + Slack unchanged.
4. **Airflow + Cosmos** (`airflow/`, services in `docker-compose.yml`): DAG
   `dbt_medallion` renders one Airflow task per dbt model+test. UI at :8088.

### Run / verify
```bash
# dbt from host (uses 127.0.0.1:55432):
cd dbt && DBT_PROFILES_DIR=$PWD ../venv/bin/dbt build --profiles-dir $PWD
# Airflow DAG:
open http://localhost:8088   # admin/admin → trigger dbt_medallion
```

### Ports (remapped to avoid clashes on this Mac)
- Postgres host **55432** (host 5432 taken) — net `db:5432` for Cube/Airflow
- Cube **4000**, Airflow UI **8088** (host 8080 taken)

### Full demo walkthrough: `DEMO_SCRIPT.md`. Reference: `ARCHITECTURE.md`.

## Resolver / LLM
The model is a constrained translator, not a SQL writer: it gets Cube's catalog
(/meta) and must emit a JSON query spec using only existing members; we validate
before running. It never touches the database.
Model: default claude-sonnet-4-6 (fast/cheap, fine for this). Use claude-opus-4-8
if ambiguous questions need more reasoning. Pin the id; force JSON output.

## Conventions
- Never commit secrets. .env is gitignored; .env.example is the template.
- Always test resolver from CLI before touching Slack.
- Keep an evals/ folder of question->expected-answer pairs; run on metric changes.
