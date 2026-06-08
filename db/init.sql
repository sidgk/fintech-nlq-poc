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


-- ============================================================
-- BRONZE: accounts (partners / customers) — one row per account.
-- Intentionally messy: mixed-case status, single-letter risk (L/M/H), text
-- dates, ~25 duplicate company_ids → SILVER dedups/typecasts/normalizes.
-- ============================================================
DROP TABLE IF EXISTS raw.accounts CASCADE;
CREATE TABLE raw.accounts (
    company_id             TEXT,
    name_of_customer       TEXT,
    industry               TEXT,
    services_offered       TEXT,
    client_type            TEXT,
    account_status         TEXT,
    account_opening_date   TEXT,
    account_closing_date   TEXT,
    blocked_date           TEXT,
    reason_for_blocking    TEXT,
    risk_scoring           TEXT,           -- L / M / H
    bi_referral_party_name TEXT,
    is_test_account        INT,
    business_entity        TEXT
);

INSERT INTO raw.accounts
WITH base AS (
    SELECT
        gs,
        random() AS rnd,
        (ARRAY['UK','DE','FR','ES','NL','IT','US'])[floor(random()*7)+1] AS entity,
        (ARRAY['Retail','Gambling','Physical Store','E-Commerce','Professional Services',
               'Financial Services','Technical Services','Hospitality','Travel','Healthcare'])[floor(random()*10)+1] AS industry,
        (ARRAY['POS','Cards','Banking','POS, Cards','POS, Cards, Banking','Acquiring',
               'Cards, Acquiring','Bank Account','POS, Bank Account'])[floor(random()*9)+1] AS services,
        (ARRAY['merchant','sub_merchant','hybrid_sub_merchant'])[floor(random()*3)+1] AS client_type,
        (ARRAY['L','M','H'])[floor(random()*3)+1] AS risk,
        (now()::date - (floor(random()*9000))::int) AS open_date,
        (random() < 0.40) AS has_ref,
        (random() < 0.03) AS is_test
    FROM generate_series(1, 2000) gs
),
typed AS (
    SELECT *,
        (CASE
            WHEN rnd < 0.50 THEN 'Active'
            WHEN rnd < 0.68 THEN 'Approved'
            WHEN rnd < 0.83 THEN 'Rejected'
            WHEN rnd < 0.93 THEN 'Terminated'
            ELSE 'Blocked'
         END) AS status
    FROM base
)
SELECT
    entity || lpad(gs::text, 6, '0'),
    'Customer ' || gs,
    industry,
    services,
    client_type,
    (CASE WHEN gs % 7 = 0 THEN upper(status) WHEN gs % 5 = 0 THEN lower(status) ELSE status END),
    open_date::text,
    CASE WHEN status = 'Terminated' THEN (open_date + (floor(random()*1500)+30)::int)::text END,
    CASE WHEN status = 'Blocked'    THEN (open_date + (floor(random()*1500)+30)::int)::text END,
    CASE WHEN status = 'Blocked'    THEN (ARRAY['Fraud suspicion','AML review','Excessive chargebacks',
               'KYC incomplete','Sanctions match'])[floor(random()*5)+1] END,
    risk,
    CASE WHEN has_ref THEN (ARRAY['Suchit','Maria','John','Priya','Acme Partners',
               'FinIntro','Channel Partner'])[floor(random()*7)+1] END,
    (CASE WHEN is_test THEN 1 ELSE 0 END),
    entity
FROM typed;

-- ~25 duplicate company_ids (older copies) → SILVER keeps the latest.
INSERT INTO raw.accounts
SELECT company_id, name_of_customer, industry, services_offered, client_type, account_status,
       ((account_opening_date::date) - (floor(random()*500)+30)::int)::text,
       account_closing_date, blocked_date, reason_for_blocking, risk_scoring,
       bi_referral_party_name, is_test_account, business_entity
FROM raw.accounts
WHERE (substring(company_id from '[0-9]+'))::int % 80 = 0;

CREATE INDEX idx_raw_accounts_id ON raw.accounts(company_id);
