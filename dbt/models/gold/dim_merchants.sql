-- GOLD: merchant dimension.
{{ config(materialized='table') }}

select id, name, category, country
from {{ ref('stg_merchants') }}
