-- RECONCILIATION: total revenue in gold MUST equal an INDEPENDENT recompute
-- straight from raw (dedup the latest copy per id, sum the cents). If gold's
-- revenue drifts from this — a wrong join, a double-count, a unit bug — this
-- returns a row and the test fails. We compare integer cents (exact, no float
-- rounding ambiguity).
with gold_cents as (
    select sum(amount_cents)::bigint as cents from {{ ref('fct_payments') }}
),
raw_cents as (
    select sum(amount_cents)::bigint as cents
    from (
        select distinct on (id) amount_cents
        from {{ source('raw', 'payments') }}
        order by id, created_at::timestamp desc
    ) deduped
)
select g.cents as gold_cents, r.cents as raw_cents, (g.cents - r.cents) as diff_cents
from gold_cents g cross join raw_cents r
where g.cents <> r.cents
