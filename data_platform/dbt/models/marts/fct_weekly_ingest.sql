{{ config(
    materialized = 'incremental',
    unique_key = ['p_week', 'p_source'],
    incremental_strategy = 'delete+insert'
) }}

-- Weekly ingest volume per source, built incrementally.
--
-- On a normal run only the trailing lookback window is rebuilt, so a daily
-- refresh does not recompute a year of history. Late-arriving rows inside the
-- window are still picked up because the strategy deletes and reinserts the
-- affected weeks rather than appending.
--
-- Full history rebuilds on `dbt run --full-refresh`.

with base as (

    select
        p_week,
        p_source,
        count(*)                                           as postings,
        count(distinct company_key)                        as companies,
        sum(case when outcome = 'valid' then 1 else 0 end) as valid_postings,
        sum(is_sponsored)                                  as sponsored_postings
    from {{ ref('stg_jobs') }}
    where p_week != 'unknown'

    {% if is_incremental() %}
      -- ISO week strings sort lexically, so a string comparison bounds the
      -- window without parsing dates
      and p_week >= (
          select coalesce(min(p_week), '0000-W00')
          from (
              select distinct p_week
              from {{ this }}
              order by p_week desc
              limit {{ (var('lookback_days') / 7) | round(0, 'ceil') | int }}
          )
      )
    {% endif %}

    group by p_week, p_source

)

select
    p_week,
    p_source,
    postings,
    companies,
    valid_postings,
    sponsored_postings,
    {{ valid_rate('valid_postings', 'postings') }} as valid_rate
from base
