"""
FastAPI service for the React dashboard. Read only.

Run from the repo root:  uvicorn dashboard_api.app:app --port 8001
"""
import os
import sqlite3
from contextlib import contextmanager

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware

from dashboard_api import queries

LOCAL = os.environ.get("JMI_LOCAL_DIR", ".local")
app = FastAPI(title="Job Market Intelligence API", version="1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.environ.get("ALLOWED_ORIGINS", "http://localhost:5174").split(","),
    allow_methods=["GET"],
    allow_headers=["Content-Type"],
)


def _sqlite(name: str):
    return sqlite3.connect(f"file:{LOCAL}/{name}?mode=ro", uri=True)


def _warehouse():
    import duckdb
    return duckdb.connect(f"{LOCAL}/warehouse.duckdb", read_only=True)


@contextmanager
def _open(*openers):
    cons = [o() for o in openers]
    try:
        yield cons
    finally:
        for c in cons:
            c.close()


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/summary")
def summary():
    with _open(lambda: _sqlite("run_history.db"), lambda: _sqlite("analytics.db"), _warehouse) as (r, a, w):
        return queries.summary(r, a, w)


@app.get("/weekly")
def weekly():
    with _open(_warehouse) as (w,):
        return queries.weekly(w)


@app.get("/sources")
def sources():
    with _open(_warehouse) as (w,):
        return queries.sources(w)


@app.get("/funnel")
def funnel(days: int = Query(30, ge=1, le=365)):
    with _open(lambda: _sqlite("analytics.db")) as (a,):
        return queries.funnel(a, days)


@app.get("/companies")
def companies(q: str = "", sponsored: bool = False, limit: int = Query(50, ge=1, le=500)):
    with _open(_warehouse) as (w,):
        return queries.companies(w, q, sponsored, limit)


@app.get("/jobs")
def jobs(q: str = "", source: str = "", job_type: str = "", sponsored: bool = False, remote: bool = False,
         limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)):
    with _open(lambda: _sqlite("analytics.db")) as (a,):
        return queries.jobs(a, q, source, job_type, sponsored, remote, limit, offset)


@app.get("/job-sources")
def job_sources():
    with _open(lambda: _sqlite("analytics.db")) as (a,):
        return queries.job_sources(a)


@app.get("/pipeline")
def pipeline():
    with _open(lambda: _sqlite("run_history.db")) as (r,):
        return queries.pipeline_funnel(r)


@app.get("/runs")
def runs():
    with _open(lambda: _sqlite("run_history.db")) as (r,):
        return queries.runs(r)


@app.get("/quarantine")
def quarantine():
    with _open(_warehouse) as (w,):
        return queries.quarantine(w)
