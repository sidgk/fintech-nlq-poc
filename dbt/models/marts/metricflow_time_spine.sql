-- Date spine required by MetricFlow (one row per day). Materialized as a table.
{{ config(materialized='table') }}

select cast(d as date) as date_day
from generate_series(timestamp '2015-01-01', timestamp '2031-12-31', interval '1 day') as d
