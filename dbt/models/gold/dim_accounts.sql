-- GOLD: partner accounts. Grain = one row per company_id. Cube reads this.
{{ config(materialized='table') }}

select * from {{ ref('stg_accounts') }}
