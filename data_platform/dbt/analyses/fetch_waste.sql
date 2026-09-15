-- Ad-hoc: companies consuming fetch budget with almost no usable output.
-- Not materialized; run with `dbt compile` and execute against the warehouse.

select
    company_display,
    total_postings,
    valid_postings,
    valid_rate,
    source_count
from {{ ref('dim_company') }}
where is_fetch_waste
order by total_postings desc
limit 25
