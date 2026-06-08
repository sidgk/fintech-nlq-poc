# Architecture

A natural-language question in Slack → a trustworthy number, computed through a
governed pipeline. **The LLM never writes SQL.**

```
Slack ──▶ Resolver (local LLM) ──▶ Cube (semantic layer) ──▶ Postgres (GOLD)
   ▲                                                              ▲
   │  numbered menu · cache · logging · feedback                  │
   └──────────────────────────────────────────  dbt Medallion, orchestrated by
                                                 Airflow + Cosmos
                                                 raw (BRONZE) ─▶ silver ─▶ gold
```

## Components

| Layer | Tech | Where | Role |
|---|---|---|---|
| Orchestration | **Airflow + Cosmos** | Docker, UI `:8088` | Runs the dbt DAG; one Airflow task per dbt model/test |
| Transformation | **dbt Core** (`dbt/`) | host venv + in Airflow | Bronze→Silver→Gold build + quality **&** reconciliation tests |
| Data | **Postgres 16** | Docker, host `:55432` / net `db:5432` | `raw` / `silver` / `gold` schemas |
| Semantic layer | **Cube** (`model/`) | Docker, `:4000` | Defines measures/dimensions/joins; compiles query specs → SQL |
| Resolver / LLM | **Ollama · qwen2.5:7b** (`bot/resolver.py`) | **local** (host) | Question → validated Cube query spec (JSON). Free, no rate limits, private. Provider-pluggable (`LLM_PROVIDER` = ollama/gemini/anthropic); llama3.1:8b kept as fallback |
| Front door | **Slack** (`bot/app.py`) | host, Socket Mode | Receives questions, posts answers, runs the menu |
| State / logs | **SQLite** (`bot/bot_state.db`) | host | Thread memory, query log, answer cache, feedback |
| AI regression | **golden evals** (`evals/`) | host / CI | question → expected interpretation; fails on regression |

## The Slack bot's capabilities (`bot/app.py`)

- **Intent routing** — greetings/small-talk get a friendly reply; only real data
  questions hit the pipeline; lineage questions are detected by intent.
- **Async replies** — instant "Hang on…" placeholder, then the answer (the event
  is acked immediately; no 3-second-timeout retries).
- **Numbered follow-up menu** — after every answer: `1` numbers in Sheets · `2`
  chart in Sheets · `3` lineage · `4` leave feedback. (No Slack interactivity
  needed — it reads the next message.)
- **On-demand Google Sheets** — a sheet is built only if the user picks 1/2, and
  **one sheet is reused per question** across users (shared link).
- **Answer cache** — identical questions are computed once and served to everyone
  (no repeat LLM/DB hit); single-flight dedups simultaneous asks; 30-min TTL.
- **Query log + feedback** — every interaction logged; option picks logged;
  free-text feedback (option 4) stored with sentiment. See `bot/analytics.py`.
- **Lineage** (`bot/lineage.py`) — "how was this calculated?" → formula + Medallion
  source chain + the exact compiled SQL.

## Reliability (`bot/app.py`, `bot/run_bot.sh`)

- **websocket-client handler** (ping/pong + auto-reconnect) instead of the fragile
  builtin one that went silently deaf on `BrokenPipe`.
- **Socket watchdog** — exits if the connection stays dead, so it restarts clean.
- **Keepalive wrapper** (`run_bot.sh`) — auto-restarts the bot within seconds.
- **DM catch-up** — on startup, answers any DM missed while disconnected.

## Medallion layers (in Postgres)

| Schema | Built by | Materialization | Contents |
|---|---|---|---|
| `raw` (Bronze) | `db/init.sql` (ingestion) | tables | Landing data — duplicates, text timestamps, messy status |
| `silver` | dbt `models/silver/stg_*.sql` | views | Typecast + de-duplicated (`DISTINCT ON (id)`) |
| `gold` | dbt `models/gold/{fct,dim}_*.sql` | tables | Star schema: `fct_payments` + `dim_merchants/cards/customers` |

Cube reads **only** `gold.*`.

## Data flow for one question

1. Slack event → `bot/app.py` → `_route` (menu pick? feedback? new question?)
2. **Cache check** — if this exact question is cached & fresh, return instantly
   (no LLM, no DB). Else single-flight → compute.
3. Resolver pulls Cube's catalog (`/meta`), asks the **local LLM** to translate →
   a JSON **query spec** using only existing members.
4. **Guardrails** repair near-misses (qualify member names, strip stray granularity).
5. Spec → Cube `/load` → Cube compiles SQL against `gold.*` → Postgres runs it.
6. Rows → formatted → posted to Slack (answer + resolved spec + numbered menu).
7. Everything **logged** to `bot_state.db`.

## Accuracy gates (see `docs/accuracy.md`)

- **Data:** dbt quality tests + **reconciliation** (`dbt/tests/recon_*.sql`) — gold
  totals must equal an independent recompute from raw (catches bad joins / double
  counts). Additivity discipline (rates = ratio-of-sums, never avg-of-rates).
- **Interpretation:** **golden evals** (`evals/run_evals.py`) — question → expected
  measures/dimensions + invariants, run on every change; exits 1 on regression.

## Ports (remapped to avoid clashes on this Mac)

| Service | Host port | Why not default |
|---|---|---|
| Postgres | **55432** | host already runs a Postgres on 5432 |
| Cube | 4000 / SQL API 15432 | — |
| Airflow UI | **8088** | host already uses 8080 |
| Ollama | 11434 | local model server |

## dbt connection note

`dbt/profiles.yml` uses `DBT_HOST`/`DBT_PORT`: from the **host**, `127.0.0.1:55432`
(IPv4 forced — plain `localhost` can hit a different local Postgres on `::1`);
inside **Airflow**, compose sets `DBT_HOST=db`, `DBT_PORT=5432`.
