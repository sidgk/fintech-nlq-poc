-- ============================================================
-- Fintech payments demo — Medallion architecture, BRONZE layer
-- Auto-runs the first time the Postgres container starts.
--
--   raw    (BRONZE) = these landing tables, "as ingested by data engineering"
--                     — intentionally messy: duplicate rows, text timestamps
--   silver (SILVER) = dbt staging views   (stg_*: typecast + dedup)
--   gold   (GOLD)   = dbt marts tables     (fct_payments + dim_*  star schema)
--
-- dbt (run locally or via Airflow/Cosmos) transforms raw -> silver -> gold.
-- Cube reads ONLY the gold layer. The resolver/Slack never see raw.
-- ============================================================

CREATE SCHEMA IF NOT EXISTS raw;
CREATE SCHEMA IF NOT EXISTS silver;
CREATE SCHEMA IF NOT EXISTS gold;

DROP TABLE IF EXISTS raw.payments, raw.cards, raw.merchants, raw.customers CASCADE;

-- ---------- BRONZE dimension: customers ----------
CREATE TABLE raw.customers (
    id          INT,
    name        TEXT,
    country     TEXT,
    segment     TEXT,                     -- consumer / sme / enterprise
    created_at  TIMESTAMP
);

INSERT INTO raw.customers (id, name, country, segment, created_at)
SELECT
    gs,
    'Customer ' || gs,
    (ARRAY['DE','FR','ES','NL','IT','GB'])[floor(random()*6)+1],
    (ARRAY['consumer','sme','enterprise'])[floor(random()*3)+1],
    now() - (random()*540 || ' days')::interval
FROM generate_series(1, 500) gs;

-- ---------- BRONZE dimension: merchants ----------
CREATE TABLE raw.merchants (
    id        INT,
    name      TEXT,
    category  TEXT,                       -- grocery / travel / electronics / ...
    country   TEXT
);

INSERT INTO raw.merchants (id, name, category, country)
SELECT
    gs,
    'Merchant ' || gs,
    (ARRAY['grocery','travel','electronics','dining','subscription','fuel','healthcare'])[floor(random()*7)+1],
    (ARRAY['DE','FR','ES','NL','IT','GB'])[floor(random()*6)+1]
FROM generate_series(1, 100) gs;

-- ---------- BRONZE dimension: cards ----------
CREATE TABLE raw.cards (
    id           INT,
    customer_id  INT,
    brand        TEXT,                     -- visa / mastercard / amex
    card_type    TEXT,                     -- credit / debit
    issued_at    TIMESTAMP
);

INSERT INTO raw.cards (id, customer_id, brand, card_type, issued_at)
SELECT
    gs,
    floor(random()*500)+1,
    (ARRAY['visa','mastercard','amex'])[floor(random()*3)+1],
    (ARRAY['credit','debit'])[floor(random()*2)+1],
    now() - (random()*400 || ' days')::interval
FROM generate_series(1, 800) gs;

-- ---------- BRONZE fact: payments ----------
-- NOTE: no primary key, no FKs, created_at is TEXT. This is deliberate —
-- bronze/landing data is permissive and stringly-typed. The SILVER layer
-- is what enforces a clean, typed, de-duplicated contract.
CREATE TABLE raw.payments (
    id              INT,
    customer_id     INT,
    card_id         INT,
    merchant_id     INT,
    amount_cents    INT,
    currency        TEXT,                  -- EUR / USD / GBP
    status          TEXT,                  -- succeeded / failed / refunded / pending (sometimes messy case/space)
    payment_method  TEXT,                  -- card / sepa / paypal / apple_pay
    created_at      TEXT                   -- ISO string, as landed (silver casts to timestamp)
);

INSERT INTO raw.payments (id, customer_id, card_id, merchant_id, amount_cents, currency, status, payment_method, created_at)
SELECT
    gs,
    floor(random()*500)+1,
    floor(random()*800)+1,
    floor(random()*100)+1,
    floor(random()*49900 + 100)::int,
    (ARRAY['EUR','EUR','EUR','USD','GBP'])[floor(random()*5)+1],
    -- inject a little messiness so silver's trim/lower normalization is real
    CASE
        WHEN random() < 0.80 THEN (ARRAY['succeeded','Succeeded',' succeeded'])[floor(random()*3)+1]
        WHEN random() < 0.90 THEN (ARRAY['failed','FAILED'])[floor(random()*2)+1]
        WHEN random() < 0.96 THEN 'refunded'
        ELSE 'pending'
    END,
    (ARRAY['card','card','card','sepa','paypal','apple_pay'])[floor(random()*6)+1],
    (now() - (random()*180 || ' days')::interval)::text
FROM generate_series(1, 20000) gs;

-- Duplicate ingestion: ~40 payments land TWICE (same id, older copy).
-- Silver's `SELECT DISTINCT ON (id) ... ORDER BY id, created_at DESC` keeps
-- the latest and drops these. raw.payments = 20040 rows; silver = 20000.
INSERT INTO raw.payments (id, customer_id, card_id, merchant_id, amount_cents, currency, status, payment_method, created_at)
SELECT
    id, customer_id, card_id, merchant_id, amount_cents, currency, status, payment_method,
    ((created_at::timestamp) - (random()*30 || ' days')::interval)::text   -- older duplicate
FROM raw.payments
WHERE id % 500 = 0;   -- 40 rows (ids 500,1000,...,20000)

-- Bronze indexes (light — heavy lifting happens in gold)
CREATE INDEX idx_raw_payments_id ON raw.payments(id);
