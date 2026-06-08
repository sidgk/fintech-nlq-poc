# Roadmap — from POC to a system the CFO bets the board meeting on

**North star:** ask any business question in plain language and get an answer you
can act on without verifying. **Accuracy is non-negotiable and ranked above every
other feature.** A confidently-wrong number must be *structurally* unable to ship.

## The three horizons (each needs a different core thing)

| Horizon | Audience | The ONE thing it must nail |
|---|---|---|
| **H1 — C-level** | a handful, highest stakes | **Trust** (accuracy + never confidently wrong) + speed + proactivity |
| **H2 — All employees** | hundreds | **Governance + discoverability** (roles, "what can I ask?", caching) |
| **H3 — Partners** | external tenants | **Multi-tenancy + row-level isolation** (partner A can never see B's data) |

> Architectural rule: design **tenant isolation** (H3) into the spine *now*, even
> while shipping H1. The partner layer is a platform, not a bigger chatbot.

---

## P0 — Data accuracy (highest priority, always)

The bar: every certified number is provably correct, and the bot never guesses.

1. **Metric certification tiers** — tag each metric `certified` / `experimental` in
   the semantic layer. The C-level path answers **only** from certified metrics; an
   uncertified ask gets a clear caveat. *(Biggest single trust lever.)*
2. **Reconciliation against an external source of truth** — extend the dbt
   `recon_*` tests to reconcile against the **finance GL / payment processor
   settlement**, not just an internal recompute. This is how "trust the number"
   becomes literal. *(We have internal reconciliation today — done.)*
3. **Ask-don't-guess + confidence** — when the resolved query is ambiguous or
   low-confidence, **clarify** ("net or gross? including pending?") instead of
   answering. Never surface a guess to an exec.
4. **Deterministic fast-path for the top ~20 exec questions** — pre-defined,
   reviewed, LLM-free queries for the metrics that matter most. 100% reproducible.
5. **Anomaly detection + freshness SLAs** — flag a number that moved suspiciously
   or is stale *before* an exec sees it; show a freshness/test badge on every answer.
6. **Additivity enforcement** — encode each measure's additivity in the semantic
   layer so the AI cannot mis-aggregate (rates stay ratio-of-sums). *(Defined
   correctly today; make it explicit + tested.)*
7. **Grow the golden-eval suite** — every 👍/👎 and every bug becomes a new eval.
   Gate every deploy on it. *(Suite exists — done; expand continuously.)*

## P0 — Speed of response

Target: **< 5s** for the answer, every time. Today ~7s (the local 8B LLM dominates;
Cube + DB are < 1s; cache hits are instant).

1. **Model tier** — `qwen2.5:7b` (more accurate, similar speed) or `llama3.2:3b`
   (~2-3s) for the long tail; keep the model **pinned in RAM** (done).
2. **Deterministic fast-path** (see accuracy #4) — top questions skip the LLM
   entirely → sub-second *and* 100% correct. Speed and accuracy, same lever.
3. **Cube pre-aggregations** — pre-compute common rollups so heavy/repeated queries
   never re-scan the warehouse (critical on pay-per-query Athena).
4. **Answer cache** — repeats are instant (done); add semantic/spec-level caching
   so differently-phrased identical questions also hit cache.
5. **Prompt slimming** — trim the catalog sent to the LLM (smaller prompt = faster).
6. **Hardware** — a bigger GPU (or a small cloud GPU) runs the same 8B in 1-2s when
   we move off a laptop.

## P1 — Trust UX (makes accuracy *visible*)

- **Freshness + test-status badge** on every answer ("✅ fresh 2h ago, tests green").
- **Definition on demand** — "what does *revenue* mean here?" → the exact formula.
- **Confidence signal** — show when the bot is sure vs. clarifying.

## P1 — Proactivity (tool → assistant, the Siri leap)

- **Scheduled digests** — "your Monday numbers" pushed before they ask.
- **Anomaly alerts** — "Revenue down 8% WoW, driven by Fuel — want the breakdown?"
- **Next-question anticipation** after each answer.

## P1 — Governance & multi-tenancy (unlocks H2 → H3)

- **Identity / role layer** — who's asking → what they're allowed to see.
- **Row-level security / tenant isolation** — the hard requirement for partners.
- **Audit log** (we log every Q&A — extend to access + retention).
- **Rate limiting + usage metering** (per partner) for the H3 platform.

## P2 — Learning loop

- **Few-shot memory** — confirmed-good question→spec pairs retrieved into the
  prompt so the bot improves on your phrasings without retraining.
- **Feedback triage** — the `feedback` table (with sentiment) drives the backlog.

## P2 — Production infrastructure

- **Always-on deploy** — move the bot off the laptop (so it works while you sleep).
  Pairs with a hosted model OR a GPU box if we keep local inference.
- **Warehouse swap** — point Cube at **Athena/Trino over Iceberg on S3** (same
  `model/` files). Cube caching matters here for cost.
- **MCP integration** — expose the metrics via the team's **MCP server** (their
  preferred AI-to-tools transport) in addition to / instead of Cube's REST.
- **Observability** — dashboards on latency, cache hit rate, error/clarify rate,
  feedback sentiment.

---

## Suggested sequence

**Phase 1 — Harden H1 for C-level (now → launch)**
P0 accuracy #1, #3, #5 (certification, ask-don't-guess, freshness badges) ·
P0 speed #1, #2 (model tier + fast-path) · P1 trust UX (badges, definitions).

**Phase 2 — Open to the company (H2)**
Identity/role layer · discoverability ("what can I ask?") · proactivity (digests) ·
expand certified-metric coverage · observability.

**Phase 3 — Partner platform (H3)**
Tenant isolation / row-level security · rate limiting + usage metering ·
warehouse swap (Athena/Iceberg) · always-on deploy · MCP.

---

## Already in place (the baseline this builds on)

✅ Governed semantic layer (Cube) · ✅ dbt Medallion + quality **&** reconciliation
tests · ✅ Airflow/Cosmos orchestration · ✅ local LLM **qwen2.5:7b** (free, private,
no limits) · ✅ guardrails (qualify names, strip granularity, drop invalid dateRanges,
intent routing) · ✅ lineage · ✅ answer cache + single-flight · ✅ query log +
feedback (👍/👎 + free-text w/ sentiment) · ✅ golden-eval suite · ✅ on-demand +
reused Google Sheets · ✅ self-healing bot.

**Exec-grade accuracy shipped:** ✅ **metric certification tiers** (+ `CERTIFIED_ONLY`
block) · ✅ **ask-don't-guess** · ✅ **deterministic fast-path** (sub-second, 100%
reproducible) · ✅ **freshness + test-status badge** on every answer.

**Remaining top P0s:** external-source reconciliation (finance GL) · anomaly detection ·
definition-on-demand · then H2 (roles/discoverability) and H3 (tenant isolation).
