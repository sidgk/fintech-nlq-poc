-- SILVER: clean, typed, de-duplicated partner accounts.
--   • dedup: keep the latest record per company_id (DISTINCT ON)
--   • typecast: text dates -> date
--   • normalize: mixed-case status -> Title Case; risk L/M/H -> Low/Medium/High
--   • derive: has_referral, real/test flag, and per-service boolean flags
{{ config(materialized='view') }}

select distinct on (company_id)
    company_id,
    trim(name_of_customer)                               as name_of_customer,
    trim(industry)                                       as industry,
    trim(services_offered)                               as services_offered,
    lower(trim(client_type))                             as client_type,
    initcap(lower(trim(account_status)))                 as account_status,   -- Active/Approved/Rejected/Terminated/Blocked
    account_opening_date::date                           as account_opening_date,
    account_closing_date::date                           as account_closing_date,
    blocked_date::date                                   as blocked_date,
    nullif(trim(reason_for_blocking), '')                as reason_for_blocking,
    case upper(trim(risk_scoring))
        when 'L' then 'Low' when 'M' then 'Medium' when 'H' then 'High'
        else initcap(lower(trim(risk_scoring)))
    end                                                  as risk_scoring,
    nullif(trim(bi_referral_party_name), '')             as bi_referral_party_name,
    (nullif(trim(bi_referral_party_name), '') is not null) as has_referral,
    (coalesce(is_test_account, 0) = 1)                   as is_test_account,
    upper(trim(business_entity))                         as business_entity,
    (services_offered ilike '%POS%')                     as has_pos,
    (services_offered ilike '%Card%')                    as has_cards,
    (services_offered ilike '%Bank%')                    as has_banking,
    (services_offered ilike '%Acquir%')                  as has_acquiring
from {{ source('raw', 'accounts') }}
order by company_id, account_opening_date::date desc
