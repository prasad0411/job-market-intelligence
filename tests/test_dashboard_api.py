"""Dashboard API queries against small fixture databases with the real schemas."""
import sqlite3

import pytest

from dashboard_api import queries


@pytest.fixture
def runs_con():
    c = sqlite3.connect(":memory:")
    c.execute("create table runs (id integer primary key, ts text, valid int, discarded int, duplicate_url int, "
              "duplicate_job int, skipped_old int, skipped_non_tech int, skipped_international int, "
              "skipped_clearance int, skipped_blacklisted int, failed_http int, elapsed_seconds real)")
    c.execute("insert into runs values (1,'2026-10-05T16:25:04',112,58,140,235,0,314,2,11,3,0,1800)")
    c.execute("insert into runs values (2,'2026-10-06T12:48:13',13,31,47,121,0,118,11,0,0,20,2400)")
    return c


@pytest.fixture
def analytics_con():
    c = sqlite3.connect(":memory:")
    c.execute("create table jobs (id integer primary key, url text, company text, title text, location text, source text, "
              "outcome text, job_type text, is_remote int, is_sponsored int, resume_type text)")
    rows = [
        ("https://a.com/1", "Acme", "Software Engineer Intern", "Boston, MA", "SimplifyJobs", "valid", "Internship", 0, 1, "SDE"),
        ("https://b.com/2", "Beta", "ML Engineer New Grad", None, "SWE List", "valid", "Full Time", 1, 0, "ML"),
        ("https://c.com/3", "Gamma", "Data Analyst Co-op", "NYC", "SimplifyJobs", "valid", "Plano, TX", 0, 0, "DA"),
        ("https://d.com/4", "Delta", "Senior Engineer", "SF", "SimplifyJobs", "discarded", "Full Time", 0, 0, "SDE"),
        ("not a url", "Eps", "Intern", "LA", "Unknown", "valid", "Internship", 0, 0, "SDE"),
    ]
    c.executemany("insert into jobs (url, company, title, location, source, outcome, job_type, is_remote, is_sponsored, "
                  "resume_type) values (?,?,?,?,?,?,?,?,?,?)", rows)
    c.execute("create table rejection_funnel (id integer primary key, date text, stage text, reason_category text, count int)")
    c.execute("insert into rejection_funnel (date, stage, reason_category, count) values (date('now'),'season','Summer 2027',295)")
    c.execute("insert into rejection_funnel (date, stage, reason_category, count) values (date('now'),'season','Summer 2027',5)")
    c.execute("insert into rejection_funnel (date, stage, reason_category, count) values ('2020-01-01','location','Non-US',99)")
    return c


@pytest.fixture
def warehouse_con():
    c = sqlite3.connect(":memory:")
    c.execute("attach ':memory:' as main_marts")
    c.execute("create table main_marts.dim_company (company_key text, company_display text, total_postings int, "
              "source_count int, track_count int, valid_postings int, discarded_postings int, sponsored_postings int, "
              "remote_postings int, valid_rate real, sponsorship_rate real, is_fetch_waste int)")
    c.executemany("insert into main_marts.dim_company values (?,?,?,?,?,?,?,?,?,?,?,?)", [
        ("acme", "Acme", 30, 3, 1, 20, 10, 5, 0, 0.67, 0.25, 0),
        ("wex", "Wex", 14, 4, 2, 6, 8, 0, 2, 0.43, 0.0, 0),
        ("junk", "\u00e2\u0086\u00b3", 12, 1, 1, 0, 12, 0, 1, 0.0, 0.0, 0),
    ])
    c.execute("create table main_marts.fct_source_quality (source text, accepted_rows int, rejected_rows int, total_rows int, yield_rate real)")
    c.executemany("insert into main_marts.fct_source_quality values (?,?,?,?,?)",
                  [("swe_list", 2228, 19, 2247, 0.99), ("simplifyjobs", 2092, 5, 2097, 0.998)])
    c.execute("create table main_marts.fct_weekly_ingest (p_week text, p_source text, postings int, companies int, "
              "valid_postings int, sponsored_postings int, valid_rate real)")
    c.executemany("insert into main_marts.fct_weekly_ingest values (?,?,?,?,?,?,?)", [
        ("2026-W33", "a", 11, 6, 10, 0, 0.9), ("2026-W33", "b", 5, 2, 5, 1, 1.0), ("2026-W21", "a", 1, 1, 1, 0, 1.0)])
    return c


def test_summary_totals(runs_con, analytics_con, warehouse_con):
    s = queries.summary(runs_con, analytics_con, warehouse_con)
    assert s["runs"] == 2
    assert s["jobs_evaluated"] == (112+58+140+235+0+314+2+11+3+0) + (13+31+47+121+0+118+11+0+0+20)
    assert s["avg_run_minutes"] == 35.0
    assert s["valid_jobs"] == 4 and s["remote_jobs"] == 1
    assert s["sponsored_share"] == 0.25
    assert s["companies"] == 2, "mojibake company names must not count"
    assert s["sources"] == 2


def test_weekly_groups_and_orders(warehouse_con):
    w = queries.weekly(warehouse_con)
    assert [x["week"] for x in w] == ["2026-W21", "2026-W33"]
    assert w[1] == {"week": "2026-W33", "postings": 16, "valid": 15, "sponsored": 1, "sources": 2}


def test_funnel_window_and_sum(analytics_con):
    f = queries.funnel(analytics_con, days=30)
    assert f == [{"stage": "season", "reason": "Summer 2027", "count": 300}]


def test_companies_filters(warehouse_con):
    assert [c["company"] for c in queries.companies(warehouse_con)] == ["Acme", "Wex"]
    assert [c["company"] for c in queries.companies(warehouse_con, q="we")] == ["Wex"]
    assert [c["company"] for c in queries.companies(warehouse_con, sponsored_only=True)] == ["Acme"]


def test_jobs_only_valid_with_urls_and_normalised_type(analytics_con):
    r = queries.jobs(analytics_con)
    assert r["total"] == 3
    types = {j["company"]: j["job_type"] for j in r["items"]}
    assert types == {"Acme": "Internship", "Beta": "Full Time", "Gamma": "Other"}
    assert next(j for j in r["items"] if j["company"] == "Beta")["location"] == "Unknown"


def test_jobs_filters_and_paging(analytics_con):
    assert queries.jobs(analytics_con, q="ml")["total"] == 1
    assert queries.jobs(analytics_con, source="SimplifyJobs")["total"] == 2
    assert queries.jobs(analytics_con, job_type="Internship")["total"] == 1
    assert queries.jobs(analytics_con, sponsored_only=True)["items"][0]["company"] == "Acme"
    assert queries.jobs(analytics_con, remote_only=True)["items"][0]["company"] == "Beta"
    assert queries.jobs(analytics_con, job_type="Plano, TX")["total"] == 3, "unknown type is ignored, not matched"
    page = queries.jobs(analytics_con, limit=2, offset=2)
    assert page["total"] == 3 and len(page["items"]) == 1


def test_jobs_query_is_parameterised(analytics_con):
    assert queries.jobs(analytics_con, q="x' or '1'='1")["total"] == 0


def test_job_sources(analytics_con):
    assert queries.job_sources(analytics_con) == [{"source": "SimplifyJobs", "count": 2}, {"source": "SWE List", "count": 1}]


def test_read_only_connections_cannot_write(tmp_path):
    db = tmp_path / "x.db"
    sqlite3.connect(db).execute("create table t (a int)")
    ro = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    with pytest.raises(sqlite3.OperationalError):
        ro.execute("insert into t values (1)")


def test_pipeline_funnel_accounts_for_every_posting(runs_con):
    f = queries.pipeline_funnel(runs_con)
    assert f[-1] == {"stage": "Valid, written to tracker", "key": "valid", "count": 125}
    assert sum(s["count"] for s in f) == queries.summary(runs_con, _analytics_stub(), _warehouse_stub())["jobs_evaluated"]


def test_runs_timeline(runs_con):
    r = queries.runs(runs_con)
    assert [x["minutes"] for x in r] == [30.0, 40.0]
    assert r[1] == {"ts": "2026-10-06T12:48:13", "minutes": 40.0, "valid": 13, "discarded": 31, "failed_http": 20}


def test_quarantine_sorted(warehouse_con):
    warehouse_con.execute("create table main_marts.fct_rejection_funnel (reason_family text, quarantine_reason text, rejected_rows int, share_of_rejected real)")
    warehouse_con.executemany("insert into main_marts.fct_rejection_funnel values (?,?,?,?)",
                              [("entity", "missing_company", 120, 0.26), ("url", "malformed_url", 310, 0.66)])
    assert [q["reason"] for q in queries.quarantine(warehouse_con)] == ["malformed_url", "missing_company"]


def test_snapshot_has_no_personal_outcomes(runs_con, analytics_con, warehouse_con, tmp_path):
    from dashboard_api import export_snapshot
    warehouse_con.execute("create table main_marts.fct_rejection_funnel (reason_family text, quarantine_reason text, rejected_rows int, share_of_rejected real)")
    analytics_con.execute("create table company_outcomes (company text, total_applied int, total_interviews int)")
    analytics_con.execute("insert into company_outcomes values ('Acme', 3, 1)")
    files = export_snapshot.write(export_snapshot.build(runs_con, analytics_con, warehouse_con), tmp_path)
    blob = " ".join(f.read_text() for f in files)
    assert {f.stem for f in files} >= {"summary", "jobs", "companies", "pipeline", "runs", "quarantine", "meta"}
    for word in ("total_applied", "total_interviews", "applied", "interview", "outcome"):
        assert word not in blob


def _analytics_stub():
    import sqlite3 as s
    c = s.connect(":memory:")
    c.execute("create table jobs (outcome text, is_sponsored int, is_remote int)")
    return c


def _warehouse_stub():
    import sqlite3 as s
    c = s.connect(":memory:")
    c.execute("attach ':memory:' as main_marts")
    c.execute("create table main_marts.dim_company (company_display text)")
    c.execute("create table main_marts.fct_source_quality (source text)")
    return c
