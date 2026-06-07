-- RECONCILIATION: the number of 'succeeded' payments in gold MUST equal an
-- independent recompute from raw (dedup latest per id, normalize the messy
-- status the same way, count 'succeeded'). Catches a normalization or filter
-- bug that would silently mis-state the success metric the CFO relies on.
with gold_s as (
    select count(*)::bigint as n from {{ ref('fct_payments') }} where status = 'succeeded'
),
raw_s as (
    select count(*)::bigint as n
    from (
        select distinct on (id) lower(trim(status)) as status
        from {{ source('raw', 'payments') }}
        order by id, created_at::timestamp desc
    ) deduped
    where status = 'succeeded'
)
select g.n as gold_succeeded, r.n as raw_succeeded
from gold_s g cross join raw_s r
where g.n <> r.n
