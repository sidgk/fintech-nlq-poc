-- GOLD: card dimension.
{{ config(materialized='table') }}

select id, customer_id, brand, card_type, issued_at
from {{ ref('stg_cards') }}
