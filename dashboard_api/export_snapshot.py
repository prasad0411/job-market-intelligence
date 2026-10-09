"""
Writes a read only JSON snapshot of the dashboard data for the public static site.

Only public job postings and pipeline statistics are exported. Personal application
outcomes (company_outcomes, applied/interview counts) are never read.

Run from the repo root:  python -m dashboard_api.export_snapshot dashboard-web/public/data [--local]
"""
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

from dashboard_api import queries

LOCAL = Path(".local")


def build(runs_con, analytics_con, warehouse_con) -> dict:
    return {
        "summary": queries.summary(runs_con, analytics_con, warehouse_con),
        "weekly": queries.weekly(warehouse_con),
        "sources": queries.sources(warehouse_con),
        "funnel": queries.funnel(analytics_con, 30),
        "companies": queries.companies(warehouse_con, limit=5000),
        "jobs": queries.jobs(analytics_con, limit=100000)["items"],
        "job_sources": queries.job_sources(analytics_con),
        "pipeline": queries.pipeline_funnel(runs_con),
        "runs": queries.runs(runs_con),
        "quarantine": queries.quarantine(warehouse_con),
        "insights": queries.insights(analytics_con, warehouse_con),
        "meta": {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds")},
    }


def write(snapshot: dict, out: Path) -> list[Path]:
    out.mkdir(parents=True, exist_ok=True)
    written = []
    for name, data in snapshot.items():
        path = out / f"{name}.json"
        path.write_text(json.dumps(data, separators=(",", ":"), ensure_ascii=False))
        written.append(path)
    return written


def main(out_dir: str, source: str = "sheet", bigquery: bool = False) -> None:
    """source "sheet" (default for the public site) reads the live Google Sheet; "local" reads analytics.db."""
    runs_con = sqlite3.connect(f"file:{LOCAL}/run_history.db?mode=ro", uri=True)
    try:
        if source == "sheet":
            # module path import, so preflight's import graph sees sheet_snapshot as reachable
            from dashboard_api.sheet_snapshot import build as build_from_sheet, read_sheet
            valid_values, discard_values = read_sheet()
            snap = build_from_sheet(valid_values, discard_values, runs_con)
        else:
            import duckdb
            analytics_con = sqlite3.connect(f"file:{LOCAL}/analytics.db?mode=ro", uri=True)
            warehouse_con = duckdb.connect(str(LOCAL / "warehouse.duckdb"), read_only=True)
            try:
                snap = build(runs_con, analytics_con, warehouse_con)
            finally:
                analytics_con.close()
                warehouse_con.close()
    finally:
        runs_con.close()
    files = write(snap, Path(out_dir))
    if bigquery:
        from dashboard_api.bq_load import load_snapshot
        load_snapshot(snap)
    total = sum(f.stat().st_size for f in files)
    print(f"wrote {len(files)} files, {total / 1e6:.1f} MB, to {out_dir} (source: {source})")


if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.INFO, format="  %(message)s")
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    src = "local" if "--local" in sys.argv else "sheet"
    main(args[0] if args else "dashboard-web/public/data", src, bigquery="--bigquery" in sys.argv)
