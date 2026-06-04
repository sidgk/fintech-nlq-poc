# Architecture

End-to-end: a natural-language question in Slack → a trustworthy number, computed
through a governed pipeline. The LLM never writes SQL.

```
Slack ──▶ Resolver (Gemini) ──▶ Cube (semantic layer) ──▶ Postgres (GOLD)
                                                              ▲
                          dbt Medallion, orchestrated by Airflow + Cosmos
                          raw (BRONZE) ─▶ silver (stg_*) ─▶ gold (fct/dim)
```

## Components

| Layer | Tech | Where | Role |
|---|---|---|---|
| Orchestration | **Airflow + Cosmos** | Docker, UI `:8088` | Runs the dbt DAG; one Airflow task per dbt model/test |
| Transformation | **dbt Core** (`dbt/`) | host venv + in Airflow | Bronze→Silver→Gold Medallion build + tests |
| Data | **Postgres 16** | Docker, host `:55432` / net `db:5432` | Holds `raw` / `silver` / `gold` schemas |
| Semantic layer | **Cube** (`model/`) | Docker, `:4000` | Defines measures/dimensions/joins; compiles query specs to SQL |
| Resolver | **Gemini 3.5 Flash** (`bot/resolver.py`) | host | Question → validated Cube query spec (JSON) |
| Front door | **Slack** (`bot/app.py`) | host, Socket Mode | Receives questions, posts answers |

## Medallion layers (in Postgres)

| Schema | Built by | Materialization | Contents |
|---|---|---|---|
| `raw` (Bronze) | `db/init.sql` (ingestion) | tables | Landing data — duplicates, text timestamps, messy status |
| `silver` | dbt `models/silver/stg_*.sql` | views | Typecast + de-duplicated (`DISTINCT ON (id)`) |
| `gold` | dbt `models/gold/{fct,dim}_*.sql` | tables | Star schema: `fct_payments` + `dim_merchants/cards/customers` |

Cube reads **only** `gold.*`. Change one `sql_table:` line per cube to repoint.

## Data flow for one question

1. Slack event → `bot/app.py` → `answer_question()` in `bot/resolver.py`
2. Resolver pulls Cube's catalog (`/meta`), sends it + the question to Gemini
3. Gemini returns a JSON **query spec** using only existing members (validated)
4. Spec → Cube `/load` → Cube compiles SQL against `gold.*` → Postgres runs it
5. Rows → formatted → posted back to Slack (answer **+** the resolved spec)

## dbt lineage (how measures are derived)

`raw.payments` (Bronze, messy)
→ `silver.stg_payments` (typed, deduped)
→ `gold.fct_payments` (fact grain = one payment; adds `amount` in major units)
→ Cube `payments` cube measures (`total_amount`, `success_rate`, …) in `model/cubes/payments.yml`
→ `payments_overview` view (`model/views/`) — the flat surface the resolver prefers

## Ports (all remapped to avoid clashes on this Mac)

| Service | Host port | Why not default |
|---|---|---|
| Postgres | **55432** | host already runs a Postgres on 5432 |
| Cube | 4000 | — |
| Cube SQL API | 15432 | — |
| Airflow UI | **8088** | host already uses 8080 |

## dbt connection note

`dbt/profiles.yml` uses `DBT_HOST` / `DBT_PORT` env vars:
- from the **host**: defaults to `127.0.0.1:55432` (IPv4 forced — `localhost` could
  resolve to a different local Postgres on `::1`)
- inside **Airflow**: compose sets `DBT_HOST=db`, `DBT_PORT=5432` (Docker network)
