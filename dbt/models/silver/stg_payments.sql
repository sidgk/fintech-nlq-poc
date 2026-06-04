-- SILVER: clean, typed, de-duplicated payments.
-- Postgres has no QUALIFY, so we use DISTINCT ON (id) ... ORDER BY id, created_at DESC
-- to keep the LATEST landed copy of each payment and drop duplicate ingests.
{{ config(materialized='view') }}

select distinct on (id)
    id,
    customer_id,
    card_id,
    merchant_id,
    amount_cents::int                          as amount_cents,
    upper(trim(currency))                      as currency,
    lower(trim(status))                        as status,          -- 'Succeeded',' succeeded' -> 'succeeded'
    lower(trim(payment_method))                as payment_method,
    created_at::timestamp                      as created_at       -- text -> timestamp
from {{ source('raw', 'payments') }}
order by id, created_at::timestamp desc
