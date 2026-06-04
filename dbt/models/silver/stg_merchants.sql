-- SILVER: typed, de-duplicated merchants.
{{ config(materialized='view') }}

select distinct on (id)
    id,
    trim(name)              as name,
    lower(trim(category))   as category,      -- grocery / travel / electronics / ...
    upper(trim(country))    as country
from {{ source('raw', 'merchants') }}
order by id
