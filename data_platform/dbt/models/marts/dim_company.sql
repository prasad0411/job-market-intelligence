{{ config(materialized='table') }}

-- Company dimension at company_key grain. Aggregates posting volume, track
-- mix, and source coverage so downstream consumers read one stable row per
-- company rather than scanning the fact table.

with postings as (

    select
        company_key,
        company_raw,
        resume_track,
        source,
        outcome,
        is_sponsored,
        is_remote
    from {{ ref('stg_jobs') }}

), agg as (

    select
        company_key,
        -- keep the longest raw spelling as the display name; short forms are
        -- usually the truncated ones
        max(company_raw)                                       as company_display,
        count(*)                                               as total_postings,
        count(distinct source)                                 as source_count,
        count(distinct resume_track)                            as track_count,
        sum(case when outcome = 'valid' then 1 else 0 end)     as valid_postings,
        sum(case when outcome = 'discarded' then 1 else 0 end) as discarded_postings,
        sum(is_sponsored)                                      as sponsored_postings,
        sum(is_remote)                                         as remote_postings
    from postings
    group by company_key

)

select
    company_key,
    company_display,
    total_postings,
    source_count,
    track_count,
    valid_postings,
    discarded_postings,
    sponsored_postings,
    remote_postings,
    {{ valid_rate('valid_postings', 'valid_postings + discarded_postings') }} as valid_rate,
    {{ valid_rate('sponsored_postings', 'total_postings') }}                  as sponsorship_rate,
    -- a company seen across many sources with a near-zero valid rate is
    -- wasting fetch budget and belongs on a pre-fetch blocklist
    case
        when total_postings >= 20
         and {{ valid_rate('valid_postings', 'valid_postings + discarded_postings') }} < 0.05
        then true else false
    end as is_fetch_waste
from agg
