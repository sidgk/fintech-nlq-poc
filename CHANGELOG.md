# Changelog — what's been achieved

Reverse-chronological log of capabilities shipped. The forward plan lives in
**[ROADMAP.md](ROADMAP.md)**; how it all fits together in **[ARCHITECTURE.md](ARCHITECTURE.md)**.

## Accuracy & trust (exec-grade)
- **Deterministic fast-path** (`bot/fastpath.py`) — top exec questions resolve to a
  fixed, reviewed spec with **no LLM** → sub-second (0.1–0.2s) **and** 100%
  reproducible. The "certified questions" set.
- **Trust badge** (`bot/freshness.py`) — every answer shows data freshness + dbt
  test status (`✅ data refreshed 4h ago · 15/15 tests passing`); fast-path answers
  also show `⚡ instant (certified)`.
- **Metric certification tiers** — each measure carries `meta.certified`. Experimental
  metrics get a caveat; `CERTIFIED_ONLY=true` blocks them outright (the C-level mode).
- **Ask-don't-guess** — ambiguous/vague requests get a clarifying question instead of
  a guess; the user's reply is merged + re-resolved (no loop).
- **Reconciliation tests** (`dbt/tests/recon_*.sql`) — gold revenue/counts must equal
  an independent recompute from raw (catches bad joins / double-counts).
- **Golden-question evals** (`evals/`) — question → expected interpretation +
  invariants; fail loudly on regression. Run on every change.
- **Deterministic guardrails** — qualify member names, strip stray time-granularity,
  drop invalid dateRanges (e.g. model inventing "all time").

## Model & speed
- **qwen2.5:7b** local default (stronger structured output); llama3.1:8b fallback.
  Free, private, no rate limits, pinned in RAM.
- **Answer cache** + single-flight — identical questions computed once, served to all.

## Bot UX
- **Numbered follow-up menu** (no Slack interactivity needed): `1` numbers in Sheets ·
  `2` chart in Sheets · `3` lineage · `4` leave feedback.
- **On-demand Google Sheets** — built only on request, **one sheet reused per
  question** across users (shared link).
- **Free-text feedback** (option 4) stored with sentiment in a dedicated table;
  sentiment-aware acknowledgment.
- **Lineage** — "how was this calculated?" → formula + Medallion source + exact SQL.
- **Conversational** — greetings/small-talk handled; contextual follow-ups
  ("the same per quarter").

## Observability
- **Query log + analytics** (`bot/analytics.py`) — every Q&A, option adoption,
  cache hit rate, latency, feedback (👍/👎 + free-text with sentiment), unanswerable
  questions.

## Reliability
- websocket-client handler (ping + auto-reconnect) · socket watchdog · keepalive
  wrapper · DM catch-up on restart · `caffeinate` to prevent sleep.

## Foundation
- Governed **Cube** semantic layer · **dbt Core** Medallion (raw→silver→gold) with
  quality + reconciliation tests · **Airflow + Cosmos** orchestration · local LLM
  resolver · Slack Socket Mode.
