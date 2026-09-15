{# Read a medallion layer from Parquet with Hive partition columns exposed. #}
{% macro lake(layer) %}
    read_parquet(
        '{{ var("lake_path") }}/{{ layer }}/**/*.parquet',
        hive_partitioning = true,
        union_by_name = true
    )
{% endmacro %}


{# Canonical company key. Mirrors transforms.normalize_company so the SQL and
   Python paths agree; a mismatch here silently splits a company into two. #}
{% macro company_key(col) %}
    trim(
        regexp_replace(
            lower(regexp_replace({{ col }}, '[,.''"()&/]', ' ', 'g')),
            '\s+(inc|llc|ltd|limited|corp|corporation|co|company|plc|gmbh|holdings|group|technologies|technology|labs)$',
            '', 'g'
        )
    )
{% endmacro %}


{# Valid rate, zero-safe.

   The denominator is parenthesised: callers pass expressions like
   "valid_postings + discarded_postings", and without the parens SQL reads
   cast(a as double) / a + b as (a/a) + b - which produced 8.0 for a company
   with 3 valid and 7 discarded, and NaN whenever valid_postings was 0. #}
{% macro valid_rate(valid_col, total_col) %}
    coalesce(
        round(cast({{ valid_col }} as double) / nullif(({{ total_col }}), 0), 4),
        0.0
    )
{% endmacro %}
