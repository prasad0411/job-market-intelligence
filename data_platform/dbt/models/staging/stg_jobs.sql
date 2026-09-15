{{ config(materialized='view') }}

-- Validated postings from the Silver layer. One row per company + title +
-- source; deduplication already happened upstream in PySpark.

select
    cast(id as bigint)                       as job_id_pk,
    url,
    company                                  as company_raw,
    {{ company_key('company') }}             as company_key,
    title,
    location,
    source,
    outcome,
    coalesce(nullif(resume_type, ''), 'UNKNOWN') as resume_track,
    job_type,
    remote,
    sponsorship,
    run_id,
    p_source,
    p_week,
    case when lower(sponsorship) in ('yes', 'true', '1') then 1 else 0 end as is_sponsored,
    case when lower(remote) like '%remote%' then 1 else 0 end             as is_remote
from {{ lake('silver') }}
where url is not null
