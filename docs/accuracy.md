# How we guarantee accuracy (the exec-trust playbook)

"One bad number breaks trust." We make a confidently-wrong number **structurally
unable to leave the building.** Accuracy is enforced at three independent layers
— the same pattern Airbnb (Minerva), Uber (uMetric), LinkedIn (UMP) use.

## The two correctness problems (don't conflate them)
1. **Data correctness** — is the number *computed* right? Deterministic → we make
   this effectively 100% with tested transforms + reconciliation.
2. **Interpretation correctness** — did the AI pick the right metric for the
   question? Probabilistic → we can't make the LLM perfect, so we make it **never
   confidently wrong** (golden evals + ask-don't-guess + constrained to defined
   members).

## Layer 1 — Data correctness (dbt)
- **Star-schema discipline:** `fct_payments` has ONE grain (one row per payment);
  dimensions are conformed; measures' additivity is explicit (a ratio like
  `success_rate` is `SUM(succeeded)/SUM(total)` — *ratio of sums*, never an average
  of per-row rates → avoids Simpson's-paradox wrong numbers).
- **Quality tests:** unique, not_null, accepted_values, relationships (in `_gold.yml`).
- **Reconciliation tests** (`dbt/tests/recon_*.sql`) — the key gate. Gold revenue /
  counts must equal an INDEPENDENT recompute straight from `raw`, via a path that
  bypasses the silver/gold models. A bad join, double-count, or unit bug fails the
  build. *This is what proves the number, not just that it's internally consistent.*

## Layer 2 — Definition correctness (semantic layer)
- One reviewed definition per metric, version-controlled in Git (`model/*.yml`),
  with descriptions + synonyms the AI reads.
- **Certification tiers (built)** — each measure carries `meta.certified` in the
  semantic layer. Uncertified ("experimental") metrics are answered with a caveat,
  and **blocked entirely** in `CERTIFIED_ONLY` mode (the C-level setting). So an
  exec never gets an experimental number presented as fact.

## Layer 3 — Interpretation correctness (the AI)
- The LLM can only pick from **defined members** — it never writes SQL.
- Deterministic guardrails repair near-misses (qualify member names, strip stray
  time-granularity).
- **Golden-question evals** (`evals/run_evals.py`) — question → expected
  interpretation + invariants, run on every metric/model/prompt change. A failure
  means the AI mis-understood a question → do not ship.
- **ask-don't-guess (built)** — ambiguous/vague requests get a clarifying question
  instead of a guess.
- **deterministic fast-path (built)** — `bot/fastpath.py` resolves the top exec
  questions to a fixed reviewed spec with no LLM → sub-second + 100% reproducible.
- **trust badge (built)** — every answer shows data freshness + dbt test status.

## Run the gates
```bash
# data: reconciliation + quality tests
cd dbt && DBT_PROFILES_DIR=$PWD ../venv/bin/dbt build --profiles-dir $PWD

# AI: golden-question regression
python evals/run_evals.py        # exit 1 on any regression
```

## The operating principle
> No number reaches an exec unless it passed a **reconciliation test** (data) and
> a **golden eval** (interpretation). On ambiguity the bot **clarifies** rather
> than guesses. That's how "never confidently wrong" becomes a property of the
> system, not a hope.
