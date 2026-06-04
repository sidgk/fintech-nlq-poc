-- SILVER: typed, de-duplicated cards.
{{ config(materialized='view') }}

select distinct on (id)
    id,
    customer_id,
    lower(trim(brand))      as brand,         -- visa / mastercard / amex
    lower(trim(card_type))  as card_type,     -- credit / debit
    issued_at::timestamp    as issued_at
from {{ source('raw', 'cards') }}
order by id, issued_at desc
