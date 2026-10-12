"""
Loads the nightly snapshot into BigQuery (dataset `jmi`), for analysis the Sheet cannot do.

The Sheet only holds postings in the tracker today: expired ones are moved out, so the history is lost.
BigQuery keeps it:
  postings_history  one row per posting per night, partitioned by snapshot_date and clustered by company.
                    Rerunning a night replaces only that night's rows, so loads are idempotent.
                    This answers questions like "how many days does a posting stay open?" and
                    "is sponsorship rising or falling over time?"
  postings, weekly_ingest, companies, sources
                    the latest snapshot, replaced each night, for quick queries.
Any failure is logged and returns False: BigQuery is an extra destination and never blocks the public site.
"""
from __future__ import annotations

import logging
import os
from datetime import date

import pandas as pd

log = logging.getLogger(__name__)

PROJECT = os.environ.get("JMI_GCP_PROJECT", "thyroid-panel-prasad0411")
DATASET = os.environ.get("JMI_BQ_DATASET", "jmi")


def prepare_tables(snapshot: dict, snapshot_date: date | None = None) -> dict[str, pd.DataFrame]:
    postings = pd.DataFrame(snapshot["jobs"])
    if not postings.empty:
        postings["entry_date"] = pd.to_datetime(postings["entry_date"], errors="coerce").dt.date
    history = postings.assign(snapshot_date=snapshot_date or date.today())
    return {
        "postings": postings,
        "postings_history": history,
        "weekly_ingest": pd.DataFrame(snapshot["weekly"]),
        "companies": pd.DataFrame(snapshot["companies"]),
        "sources": pd.DataFrame(snapshot["sources"]),
    }


def load_snapshot(snapshot: dict, project: str = PROJECT, dataset: str = DATASET) -> bool:
    try:
        from google.api_core.exceptions import NotFound
        from google.cloud import bigquery
        client = bigquery.Client(project=project)
        ds = bigquery.Dataset(f"{project}.{dataset}")
        ds.location = "US"
        client.create_dataset(ds, exists_ok=True)
        today = date.today()
        for name, df in prepare_tables(snapshot, today).items():
            table = f"{project}.{dataset}.{name}"
            if name == "postings_history":
                try:
                    client.get_table(table)
                    params = [bigquery.ScalarQueryParameter("d", "DATE", today)]
                    client.query(f"DELETE FROM `{table}` WHERE snapshot_date = @d",
                                 job_config=bigquery.QueryJobConfig(query_parameters=params)).result()
                except NotFound:
                    log.info("BigQuery: first load creates postings_history")
                config = bigquery.LoadJobConfig(write_disposition="WRITE_APPEND")
                config.time_partitioning = bigquery.TimePartitioning(field="snapshot_date")
                config.clustering_fields = ["company"]
            else:
                config = bigquery.LoadJobConfig(write_disposition="WRITE_TRUNCATE")
                if name == "postings":
                    config.time_partitioning = bigquery.TimePartitioning(field="entry_date")
                    config.clustering_fields = ["source"]
            client.load_table_from_dataframe(df, table, job_config=config).result()
            log.info(f"BigQuery: loaded {len(df)} rows into {dataset}.{name}")
        return True
    except Exception as exc:
        log.warning(f"BigQuery load skipped ({type(exc).__name__}: {exc})")
        return False
