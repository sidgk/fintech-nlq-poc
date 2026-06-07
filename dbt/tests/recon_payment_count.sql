-- RECONCILIATION: the gold fact row count MUST equal the number of DISTINCT
-- payment ids in raw. This is computed via a path that does NOT go through the
-- silver/gold models — so a bug in those models (a bad join that fans out rows,
-- a dropped row, a double-count) is caught here. dbt test fails if this returns
-- any rows.
with gold_n as (
    select count(*)::bigint as n from {{ ref('fct_payments') }}
),
raw_n as (
    select count(distinct id)::bigint as n from {{ source('raw', 'payments') }}
)
select g.n as gold_rows, r.n as raw_distinct_ids
from gold_n g cross join raw_n r
where g.n <> r.n
