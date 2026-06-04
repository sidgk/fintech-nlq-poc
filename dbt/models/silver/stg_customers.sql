-- SILVER: typed, de-duplicated customers.
{{ config(materialized='view') }}

select distinct on (id)
    id,
    trim(name)             as name,
    upper(trim(country))   as country,
    lower(trim(segment))   as segment,        -- consumer / sme / enterprise
    created_at::timestamp  as created_at
from {{ source('raw', 'customers') }}
order by id, created_at desc
