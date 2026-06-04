-- GOLD: customer dimension.
{{ config(materialized='table') }}

select id, name, country, segment, created_at
from {{ ref('stg_customers') }}
