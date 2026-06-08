# Adding a new domain (worked example: partner accounts)

How a new table becomes ask-able in Slack — the exact, repeatable recipe, with
**accuracy enforced at every layer**. This is what was done for `accounts`.

## The pipeline (raw → silver → gold → semantic layer → bot)

**1. Bronze — land the raw table** (`db/init.sql`)
`raw.accounts`, ~2000 rows. Kept intentionally *messy* (mixed-case status,
single-letter risk `L/M/H`, text dates, ~25 duplicate `company_id`s) so Silver has
real cleaning to do — and so reconciliation has something to prove.

**2. Silver — clean & conform** (`dbt/models/silver/stg_accounts.sql`)
- **dedup**: `DISTINCT ON (company_id)` keep the latest record (2025 → 2000)
- **typecast**: text dates → `date`
- **normalize**: status → Title Case; risk `L/M/H` → `Low/Medium/High`
- **derive**: `has_referral`, `is_test_account`, and per-service booleans
  (`has_pos/has_cards/has_banking/has_acquiring`) so multi-value "services_offered"
  is exactly queryable
- declared as a source in `models/bronze/_sources.yml`

**3. Gold — the star table** (`dbt/models/gold/dim_accounts.sql`)
One row per `company_id`. Tested in `models/gold/_accounts.yml`:
`unique` + `not_null` on the id (proves dedup), `accepted_values` on status & risk
(proves normalization).

**4. Reconciliation** (`dbt/tests/recon_account_count.sql`)
Gold count **must equal** `COUNT(DISTINCT company_id)` from raw — computed via a
path that bypasses silver/gold. Catches a dedup bug / lost rows / double-count.
`dbt build` → PASS=31.

**5. Semantic layer** (`model/cubes/accounts.yml` + `model/views/accounts_overview.yml`)
- **measures** (all `meta.certified: true`): `count`, `active_count`,
  `approved_count`, `rejected_count`, `terminated_count`, `blocked_count`,
  `high_risk_count`, `referred_count`, `test_account_count`, `block_rate`,
  `termination_rate`
- **dimensions**: status, industry, services, client_type, risk, business_entity
  (country), reason_for_blocking, referral party, the booleans, and the dates
- Rich `description` + **synonyms** on each — this is what the LLM reads
- `accounts_overview` view = the flat surface the resolver prefers

**6. Resolver — multi-domain routing** (`bot/resolver.py`)
The prompt now picks the matching `*_overview` view by topic (payments_overview vs
accounts_overview) and never mixes domains. Guards (qualify names, strip stray
granularity, drop unasked dateRanges) apply across both.

**7. Fast-path + evals**
Top account questions added to `bot/fastpath.py` (instant, LLM-free) and to
`evals/golden.yaml` (regression). `python evals/run_evals.py` → 12/12.

## Accuracy — how it's guaranteed for this domain
| Risk | Guard |
|---|---|
| Wrong count / bad dedup | `recon_account_count` + `unique(company_id)` |
| Messy status mis-bucketed | `accepted_values(account_status)` |
| Risk mis-mapped | `accepted_values(risk_scoring)` |
| AI picks wrong metric | golden evals (per question) |
| AI confidently wrong on vague ask | ask-don't-guess |
| Unasked time filter | drop dateRange when no time words |
| Experimental metric to an exec | `meta.certified` + `CERTIFIED_ONLY` |
| Repeated heavy question | answer cache + single-flight |

## To add the NEXT domain, repeat
init.sql (bronze) → `stg_*` (silver) → `dim_*`/`fct_*` (gold) + tests + `recon_*` →
Cube cube + `*_overview` view (certify measures) → add to resolver routing,
fast-path, and golden evals. Run `dbt build` + `python evals/run_evals.py`.
