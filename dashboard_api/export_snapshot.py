"""
Writes a read only JSON snapshot of the dashboard data for the public static site.

Only public job postings and pipeline statistics are exported. Personal application
outcomes (company_outcomes, applied/interview counts) are never read.

Run from the repo root:  python -m dashboard_api.export_snapshot dashboard-web/public/data
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


def main(out_dir: str) -> None:
    import duckdb
    runs_con = sqlite3.connect(f"file:{LOCAL}/run_history.db?mode=ro", uri=True)
    analytics_con = sqlite3.connect(f"file:{LOCAL}/analytics.db?mode=ro", uri=True)
    warehouse_con = duckdb.connect(str(LOCAL / "warehouse.duckdb"), read_only=True)
    try:
        files = write(build(runs_con, analytics_con, warehouse_con), Path(out_dir))
    finally:
        for c in (runs_con, analytics_con, warehouse_con):
            c.close()
    total = sum(f.stat().st_size for f in files)
    print(f"wrote {len(files)} files, {total / 1e6:.1f} MB, to {out_dir}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "dashboard-web/public/data")
