"""
Loads the nightly snapshot into BigQuery (dataset `jmi`).

postings is partitioned by entry date and clustered by source, so date filtered queries only
scan the days they need. Loads replace each table (idempotent). Any failure is logged and
returns False: BigQuery is an extra destination and must never block the public site.
"""
from __future__ import annotations

import logging
import os

import pandas as pd

log = logging.getLogger(__name__)

PROJECT = os.environ.get("JMI_GCP_PROJECT", "thyroid-panel-prasad0411")
DATASET = os.environ.get("JMI_BQ_DATASET", "jmi")


def prepare_tables(snapshot: dict) -> dict[str, pd.DataFrame]:
    postings = pd.DataFrame(snapshot["jobs"])
    if not postings.empty:
        postings["entry_date"] = pd.to_datetime(postings["entry_date"], errors="coerce").dt.date
    return {
        "postings": postings,
        "weekly_ingest": pd.DataFrame(snapshot["weekly"]),
        "companies": pd.DataFrame(snapshot["companies"]),
        "sources": pd.DataFrame(snapshot["sources"]),
    }


def load_snapshot(snapshot: dict, project: str = PROJECT, dataset: str = DATASET) -> bool:
    try:
        from google.cloud import bigquery
        client = bigquery.Client(project=project)
        ds = bigquery.Dataset(f"{project}.{dataset}")
        ds.location = "US"
        client.create_dataset(ds, exists_ok=True)
        for name, df in prepare_tables(snapshot).items():
            config = bigquery.LoadJobConfig(write_disposition="WRITE_TRUNCATE")
            if name == "postings":
                config.time_partitioning = bigquery.TimePartitioning(field="entry_date")
                config.clustering_fields = ["source"]
            client.load_table_from_dataframe(df, f"{project}.{dataset}.{name}", job_config=config).result()
            log.info(f"BigQuery: loaded {len(df)} rows into {dataset}.{name}")
        return True
    except Exception as exc:
        log.warning(f"BigQuery load skipped ({type(exc).__name__}: {exc})")
        return False
