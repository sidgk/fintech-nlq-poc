-- GOLD: the payments FACT table. Grain = one row per payment.
-- This is the single object Cube's `payments` cube reads. Every measure
-- (revenue, success_rate, ...) is derived from these columns.
{{ config(materialized='table') }}

select
    p.id,
    p.customer_id,
    p.card_id,
    p.merchant_id,
    p.amount_cents,
    (p.amount_cents / 100.0)::numeric(12,2)  as amount,     -- convenience: major units
    p.currency,
    p.status,
    p.payment_method,
    p.created_at
from {{ ref('stg_payments') }} p
