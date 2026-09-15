{{ config(materialized='table') }}

-- Per-source quality fact combining accepted and quarantined volume, so a
-- source's true yield is visible rather than only its accepted count.

with accepted as (
    select p_source as source, count(*) as accepted_rows
    from {{ ref('stg_jobs') }}
    group by p_source
), rejected as (
    select lower(replace(source, ' ', '_')) as source, count(*) as rejected_rows
    from {{ ref('stg_quarantine') }}
    group by lower(replace(source, ' ', '_'))
)

select
    coalesce(a.source, r.source)                as source,
    coalesce(a.accepted_rows, 0)                as accepted_rows,
    coalesce(r.rejected_rows, 0)                as rejected_rows,
    coalesce(a.accepted_rows, 0) + coalesce(r.rejected_rows, 0) as total_rows,
    {{ valid_rate('coalesce(a.accepted_rows, 0)',
                  'coalesce(a.accepted_rows, 0) + coalesce(r.rejected_rows, 0)') }} as yield_rate
from accepted a
full outer join rejected r on a.source = r.source
order by total_rows desc
