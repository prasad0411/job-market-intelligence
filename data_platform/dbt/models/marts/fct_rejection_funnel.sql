{{ config(materialized='table') }}

-- Rejection funnel at reason grain, with each reason's share of total
-- quarantined volume. This is what turns "some rows failed" into "24% of
-- ingest fails URL validation".

with q as (
    select quarantine_reason, reason_family, count(*) as rejected_rows
    from {{ ref('stg_quarantine') }}
    group by quarantine_reason, reason_family
), total as (
    select sum(rejected_rows) as all_rejected from q
)

select
    q.reason_family,
    q.quarantine_reason,
    q.rejected_rows,
    round(cast(q.rejected_rows as double) / nullif(t.all_rejected, 0), 4) as share_of_rejected
from q
cross join total t
order by q.rejected_rows desc
