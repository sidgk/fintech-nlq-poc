-- RECONCILIATION: the gold account count MUST equal the number of DISTINCT
-- company_ids in raw (computed independently of the silver/gold models). Catches
-- a dedup bug, a bad transform, or rows lost/duplicated. Fails if it returns rows.
with gold_n as (
    select count(*)::bigint as n from {{ ref('dim_accounts') }}
),
raw_n as (
    select count(distinct company_id)::bigint as n from {{ source('raw', 'accounts') }}
)
select g.n as gold_rows, r.n as raw_distinct_ids
from gold_n g cross join raw_n r
where g.n <> r.n
