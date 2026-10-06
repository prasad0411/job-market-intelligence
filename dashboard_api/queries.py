"""
Read only queries behind the React dashboard.

Every connection is opened read only: the SQLite stores with mode=ro and the
DuckDB warehouse with read_only=True. Nothing here can modify pipeline data.

Charts read the dbt marts (tested models). The jobs list reads analytics.jobs,
whose job_type column has historically received shifted values, so job_type is
normalised and anything unexpected is shown as "Other" rather than dropped.
Personal application outcomes (company_outcomes) are never queried.
"""
from __future__ import annotations

import re
from typing import Any

JOB_TYPES = {"Internship", "Full Time", "Co-op"}
_MOJIBAKE = re.compile(r"[\u00c2\u00c3\u00e2][\u0080-\u00bf\u2010-\u203a]|\ufffd")


def _rows(con, sql: str, params: tuple = ()) -> list[tuple]:
    return con.execute(sql, params).fetchall()


def clean_name(name: Any) -> bool:
    """A displayable company name: has a letter and no encoding debris."""
    s = str(name or "")
    return bool(re.search(r"[A-Za-z]", s)) and not _MOJIBAKE.search(s)


def summary(runs_con, analytics_con, warehouse_con) -> dict:
    n, valid, discarded, dup_url, dup_job, old, nontech, intl, clearance, black, http, avg_s, last = _rows(
        runs_con,
        "select count(*), sum(valid), sum(discarded), sum(duplicate_url), sum(duplicate_job), sum(skipped_old), "
        "sum(skipped_non_tech), sum(skipped_international), sum(skipped_clearance), sum(skipped_blacklisted), "
        "sum(failed_http), avg(elapsed_seconds), max(ts) from runs",
    )[0]
    evaluated = sum(int(x or 0) for x in (valid, discarded, dup_url, dup_job, old, nontech, intl, clearance, black, http))
    jobs_valid, sponsored, remote = _rows(
        analytics_con,
        "select count(*), sum(case when is_sponsored = 1 then 1 else 0 end), "
        "sum(case when is_remote = 1 then 1 else 0 end) from jobs where outcome = 'valid'",
    )[0]
    n_companies = sum(1 for (name,) in _rows(warehouse_con, "select company_display from main_marts.dim_company") if clean_name(name))
    n_sources = _rows(warehouse_con, "select count(distinct source) from main_marts.fct_source_quality")[0][0]
    return {
        "runs": int(n or 0),
        "last_run": last,
        "jobs_evaluated": evaluated,
        "jobs_evaluated_per_run": round(evaluated / n) if n else 0,
        "avg_run_minutes": round(float(avg_s or 0) / 60, 1),
        "valid_jobs": int(jobs_valid or 0),
        "sponsored_share": round(int(sponsored or 0) / jobs_valid, 4) if jobs_valid else 0.0,
        "remote_jobs": int(remote or 0),
        "companies": n_companies,
        "sources": int(n_sources or 0),
    }


def weekly(warehouse_con) -> list[dict]:
    rows = _rows(
        warehouse_con,
        "select p_week, sum(postings), sum(valid_postings), sum(sponsored_postings), count(distinct p_source) "
        "from main_marts.fct_weekly_ingest group by p_week order by p_week",
    )
    return [{"week": w, "postings": int(p or 0), "valid": int(v or 0), "sponsored": int(s or 0), "sources": int(n or 0)}
            for w, p, v, s, n in rows]


def sources(warehouse_con) -> list[dict]:
    rows = _rows(
        warehouse_con,
        "select source, accepted_rows, rejected_rows, total_rows, yield_rate "
        "from main_marts.fct_source_quality order by total_rows desc",
    )
    return [{"source": s, "accepted": int(a or 0), "rejected": int(r or 0), "total": int(t or 0), "yield_rate": float(y or 0)}
            for s, a, r, t, y in rows]


def funnel(analytics_con, days: int = 30) -> list[dict]:
    rows = _rows(
        analytics_con,
        "select stage, reason_category, sum(count) from rejection_funnel "
        "where date >= date('now', ?) group by stage, reason_category order by 3 desc limit 12",
        (f"-{int(days)} days",),
    )
    return [{"stage": st, "reason": r, "count": int(c or 0)} for st, r, c in rows]


def companies(warehouse_con, q: str = "", sponsored_only: bool = False, limit: int = 50) -> list[dict]:
    rows = _rows(
        warehouse_con,
        "select company_display, total_postings, valid_postings, sponsored_postings, sponsorship_rate, valid_rate, "
        "source_count from main_marts.dim_company order by valid_postings desc, total_postings desc",
    )
    ql = q.strip().lower()
    out = []
    for name, total, valid, sponsored, srate, vrate, nsrc in rows:
        if not clean_name(name) or (ql and ql not in str(name).lower()):
            continue
        if sponsored_only and not (sponsored or 0):
            continue
        out.append({"company": name, "total": int(total or 0), "valid": int(valid or 0), "sponsored": int(sponsored or 0),
                    "sponsorship_rate": float(srate or 0), "valid_rate": float(vrate or 0), "sources": int(nsrc or 0)})
        if len(out) >= limit:
            break
    return out


def jobs(analytics_con, q: str = "", source: str = "", job_type: str = "", sponsored_only: bool = False,
         remote_only: bool = False, limit: int = 50, offset: int = 0) -> dict:
    where = ["outcome = 'valid'", "url like 'http%'"]
    params: list[Any] = []
    if q.strip():
        where.append("(lower(company) like ? or lower(title) like ?)")
        params += [f"%{q.strip().lower()}%"] * 2
    if source:
        where.append("source = ?")
        params.append(source)
    if job_type in JOB_TYPES:
        where.append("job_type = ?")
        params.append(job_type)
    if sponsored_only:
        where.append("is_sponsored = 1")
    if remote_only:
        where.append("is_remote = 1")
    clause = " and ".join(where)
    total = _rows(analytics_con, f"select count(*) from jobs where {clause}", tuple(params))[0][0]
    rows = _rows(
        analytics_con,
        f"select id, company, title, location, source, url, job_type, is_remote, is_sponsored, resume_type "
        f"from jobs where {clause} order by id desc limit ? offset ?",
        tuple(params) + (int(limit), int(offset)),
    )
    items = [{"id": i, "company": c, "title": t, "location": loc or "Unknown", "source": s, "url": u,
              "job_type": jt if jt in JOB_TYPES else "Other", "remote": bool(r), "sponsored": bool(sp), "track": rt}
             for i, c, t, loc, s, u, jt, r, sp, rt in rows]
    return {"total": int(total), "items": items}


def job_sources(analytics_con) -> list[dict]:
    rows = _rows(analytics_con, "select source, count(*) from jobs where outcome = 'valid' and url like 'http%' "
                                "group by source order by 2 desc")
    return [{"source": s, "count": int(n)} for s, n in rows]


FUNNEL_STAGES = [
    ("duplicate_url", "Duplicate URL"),
    ("duplicate_job", "Duplicate job"),
    ("skipped_non_tech", "Not a tech role"),
    ("skipped_old", "Too old"),
    ("skipped_international", "Outside the US"),
    ("skipped_clearance", "Needs clearance"),
    ("skipped_blacklisted", "Blacklisted company"),
    ("failed_http", "Page failed to load"),
    ("discarded", "Failed validation"),
    ("valid", "Valid, written to tracker"),
]


def pipeline_funnel(runs_con) -> list[dict]:
    """Where every evaluated posting ended up, summed over all runs."""
    cols = ", ".join(f"sum({c})" for c, _ in FUNNEL_STAGES)
    totals = _rows(runs_con, f"select {cols} from runs")[0]
    return [{"stage": label, "key": key, "count": int(n or 0)} for (key, label), n in zip(FUNNEL_STAGES, totals)]


def runs(runs_con) -> list[dict]:
    rows = _rows(runs_con, "select ts, elapsed_seconds, valid, discarded, failed_http from runs order by ts")
    return [{"ts": ts, "minutes": round(float(e or 0) / 60, 1), "valid": int(v or 0), "discarded": int(d or 0),
             "failed_http": int(f or 0)} for ts, e, v, d, f in rows]


def quarantine(warehouse_con) -> list[dict]:
    rows = _rows(warehouse_con, "select reason_family, quarantine_reason, rejected_rows, share_of_rejected "
                                "from main_marts.fct_rejection_funnel order by rejected_rows desc")
    return [{"family": f, "reason": r, "rows": int(n or 0), "share": float(s or 0)} for f, r, n, s in rows]
