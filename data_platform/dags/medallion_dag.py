"""
Medallion pipeline orchestration.

    extract -> bronze -> silver -> [quality_gate, quarantine_report] -> gold
                                                                         |
                                                             dbt_build -> dbt_test
                                                                         |
                                                                   publish_summary

Design decisions worth knowing before changing anything:

  * `max_active_runs=1`. The layers write to shared Parquet paths, so two
    concurrent runs would interleave partition overwrites. Serialising the DAG
    is cheaper than adding a distributed lock.

  * Backfill is per ISO week, passed through to the medallion CLI. Dynamic
    partition overwrite means a rerun replaces only that week's files, so a
    backfill cannot corrupt neighbouring weeks.

  * `quality_gate` fails the run when the Silver pass rate drops below
    threshold. A pipeline that silently accepts a collapse in data quality is
    worse than one that stops, because downstream consumers keep reading and
    nobody finds out until a report looks wrong.

  * Row counts travel by XCom rather than being recomputed, so each task
    asserts against what the previous task actually produced.

  * Retries use exponential backoff. The extract step touches SQLite and the
    dbt step touches DuckDB; both can lose a lock transiently, and an
    immediate retry would just hit the same contention.

Local verification without a scheduler:
    airflow dags test medallion_pipeline 2026-09-15
"""
from __future__ import annotations

import os
import subprocess
from datetime import datetime, timedelta

# Airflow 3 moved the task SDK out of airflow.decorators; fall back so the
# same DAG file runs on 2.x deployments without edits.
try:
    from airflow.sdk import dag, task
except ImportError:
    from airflow.decorators import dag, task
from airflow.exceptions import AirflowFailException, AirflowSkipException

REPO = os.environ.get(
    "JOB_TRACKER_HOME",
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
)
DBT_DIR = os.path.join(REPO, "data_platform", "dbt")

# A run that drops below this Silver pass rate is treated as a failure rather
# than published. Set from the observed baseline, not aspirationally: the
# pipeline sits near 62%, so 45% represents a real regression.
MIN_SILVER_PASS_RATE = 0.45

# Guards against an upstream going dark. Zero extracted rows is always a fault.
MIN_EXTRACTED_ROWS = 1

DEFAULT_ARGS = {
    "owner": "prasad",
    "retries": 3,
    "retry_delay": timedelta(minutes=2),
    "retry_exponential_backoff": True,
    "max_retry_delay": timedelta(minutes=20),
    "execution_timeout": timedelta(minutes=45),
}


def _week_from_context(logical_date):
    """Map the run's logical date onto the ISO week the layers partition by."""
    iso = logical_date.isocalendar()
    return "%d-W%02d" % (iso[0], iso[1])


@dag(
    dag_id="medallion_pipeline",
    description="Bronze/Silver/Gold Parquet layers plus dbt marts",
    schedule="0 3 * * *",
    start_date=datetime(2026, 9, 1),
    catchup=False,               # flip to True with --reset-dagruns to backfill
    max_active_runs=1,
    default_args=DEFAULT_ARGS,
    tags=["data-platform", "medallion", "dbt", "pyspark"],
    doc_md=__doc__,
)
def medallion_pipeline():

    @task
    def extract(**context) -> dict:
        """Pull the jobs fact table and report its size for the week."""
        import sys
        sys.path.insert(0, REPO)
        from data_platform.medallion import read_jobs

        week = _week_from_context(context["logical_date"])
        rows = read_jobs(week=week)
        if len(rows) < MIN_EXTRACTED_ROWS:
            # Distinguish "nothing new this week" from "extract is broken":
            # a week with no runs is legitimate, an empty table is not.
            all_rows = read_jobs()
            if not all_rows:
                raise AirflowFailException(
                    "extract returned 0 rows for the whole table - "
                    "analytics.db is empty or unreachable")
            raise AirflowSkipException("no rows for week %s, nothing to build" % week)
        return {"week": week, "extracted": len(rows)}

    @task
    def build_bronze(meta: dict) -> dict:
        """Land the raw extract, partitioned by source and ISO week."""
        import sys
        sys.path.insert(0, REPO)
        from data_platform.medallion import run

        stats = run(week=meta["week"], layer="bronze")
        if stats["bronze"] != meta["extracted"]:
            raise AirflowFailException(
                "bronze row count %d does not match extract %d - rows lost in landing"
                % (stats["bronze"], meta["extracted"]))
        return {**meta, **stats}

    @task
    def build_silver(meta: dict) -> dict:
        """Validate and deduplicate; rejects go to the quarantine table."""
        import sys
        sys.path.insert(0, REPO)
        from data_platform.medallion import run

        stats = run(week=meta["week"], layer="silver")
        return {**meta, **stats}

    @task
    def quality_gate(meta: dict) -> dict:
        """Stop the run if data quality regressed, rather than publishing it."""
        total = meta["silver"] + meta["quarantine"]
        rate = (meta["silver"] / total) if total else 0.0
        if rate < MIN_SILVER_PASS_RATE:
            raise AirflowFailException(
                "silver pass rate %.1f%% below threshold %.0f%% "
                "(%d passed, %d quarantined) - upstream data quality regressed"
                % (rate * 100, MIN_SILVER_PASS_RATE * 100,
                   meta["silver"], meta["quarantine"]))
        return {**meta, "silver_pass_rate": round(rate, 4)}

    @task
    def quarantine_report(meta: dict) -> dict:
        """Break the quarantine volume down by rule so a spike is attributable."""
        import sys
        from collections import Counter
        sys.path.insert(0, REPO)
        from data_platform.medallion import read_jobs, build_bronze as bb, build_silver as bs

        _, q = bs(bb(read_jobs(week=meta["week"])))
        breakdown = dict(Counter(r["quarantine_reason"] for r in q))
        for reason, n in sorted(breakdown.items(), key=lambda kv: -kv[1]):
            print("  %-22s %6d" % (reason, n))
        return {"week": meta["week"], "breakdown": breakdown}

    @task
    def build_gold(meta: dict) -> dict:
        """Curate the company + resume-track aggregate."""
        import sys
        sys.path.insert(0, REPO)
        from data_platform.medallion import run

        stats = run(week=meta["week"], layer="gold")
        return {**meta, **stats}

    @task
    def dbt_build(meta: dict) -> dict:
        """Materialize staging views and mart tables over the Parquet lake."""
        return _dbt("build", "--exclude", "test_type:generic", meta=meta)

    @task
    def dbt_test(meta: dict) -> dict:
        """Run the data tests separately so a test failure is distinguishable
        from a model failure in the task log."""
        return _dbt("test", meta=meta)

    @task
    def publish_summary(gate: dict, quarantine: dict, tests: dict) -> str:
        """Single line the digest and the run log can both carry."""
        top = sorted(quarantine["breakdown"].items(), key=lambda kv: -kv[1])[:3]
        summary = (
            "week=%s extracted=%d silver=%d (%.1f%% pass) gold=%d "
            "dbt=%s top_rejections=%s" % (
                gate["week"], gate["extracted"], gate["silver"],
                gate["silver_pass_rate"] * 100, gate.get("gold", 0),
                tests["status"],
                ", ".join("%s:%d" % (k, v) for k, v in top),
            )
        )
        print(summary)
        return summary

    meta = extract()
    bronze = build_bronze(meta)
    silver = build_silver(bronze)
    gate = quality_gate(silver)
    quarantine = quarantine_report(silver)
    gold = build_gold(gate)
    built = dbt_build(gold)
    tested = dbt_test(built)
    publish_summary(gate, quarantine, tested)


def _dbt(*args, meta=None):
    """Invoke dbt in-process-adjacent via subprocess, surfacing its output.

    dbt is run as a subprocess rather than imported: the CLI is the supported
    entrypoint, and mixing dbt's dependency tree into the Airflow worker's is
    a known source of version conflicts.
    """
    cmd = ["dbt", *args, "--project-dir", DBT_DIR, "--profiles-dir", DBT_DIR]
    print("running: %s" % " ".join(cmd))
    proc = subprocess.run(cmd, capture_output=True, text=True, cwd=DBT_DIR)
    print(proc.stdout[-4000:])
    if proc.returncode != 0:
        print(proc.stderr[-2000:])
        raise AirflowFailException("dbt %s failed with code %d"
                                   % (args[0], proc.returncode))
    return {**(meta or {}), "status": "ok"}


medallion_pipeline()
