{{ config(materialized='view') }}

-- Rows rejected at the Silver gate, kept with the rule that rejected them.
-- Nothing is deleted, so a bad filter is recoverable and the rejection rate
-- is measurable rather than inferred.

select
    cast(id as bigint)           as job_id_pk,
    url,
    company                      as company_raw,
    title,
    source,
    run_id,
    quarantine_reason,
    case
        when quarantine_reason in ('malformed_url', 'search_fallback_url') then 'url'
        when quarantine_reason in ('missing_company', 'placeholder_company') then 'entity'
        when quarantine_reason = 'duplicate_grain' then 'dedup'
        else 'other'
    end                          as reason_family
from {{ lake('silver_quarantine') }}
