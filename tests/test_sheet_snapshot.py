"""Sheet snapshot: privacy, dates without a year, normalisation, and consistency of every number."""
import json
import sqlite3
from datetime import date

from dashboard_api import sheet_snapshot as ss

TODAY = date(2026, 10, 6)
VALID = [
    ["Sr. No.", "Status", "Company", "Title", "Date Applied", "Job URL", "Job ID", "Job Type", "Location", "Resume",
     "Remote?", "Entry Date", "Source", "Sponsorship", "Notes"],
    ["1", "Applied", "Stripe", "Software Engineer Intern", "05 October", "https://s/1", "J1", "Internship",
     "San Francisco, CA", "SDE", "Remote", "05 October, 09:37 AM", "SWE List", "Yes", "Rescued 05 Oct, 09:37"],
    ["2", "Rejected", "Ramp", "Data Engineer Co-op", "01 October", "https://r/2", "J2", "Co-op",
     "New York, NY", "DA", "On Site", "29 September, 06:28 AM", "SimplifyJobs", "No", "secret note"],
    ["3", "OA Round 1", "Stripe", "ML Engineer New Grad", "", "https://s/3", "J3", "Full Time",
     "Boston, MA", "ML", "Unknown", "20 December, 01:21 PM", "SWE List", "Yes", ""],
    ["4", "Not Applied", "", "No company row", "", "https://x", "", "Internship", "", "SDE", "", "", "", "", ""],
]
DISCARD = [
    ["Sr. No.", "Discard Reason", "Company", "Title", "Date Applied", "Job URL", "Job ID", "Job Type", "Location",
     "Remote?", "Entry Date", "Source", "Sponsorship"],
    ["1", "Expired: 2+ days", "A", "t", "N/A", "https://a", "", "Internship", "", "Unknown", "05 October, 08:00 AM", "SWE List", "Unknown"],
    ["2", "Expired: 9 days", "B", "t", "N/A", "https://b", "", "Internship", "", "Unknown", "28 September, 08:00 AM", "Indeed", "Yes"],
    ["3", "Senior role (Staff)", "C", "t", "N/A", "https://c", "", "Full Time", "", "Unknown", "garbage", "Indeed", "Unknown"],
]


def runs_con():
    c = sqlite3.connect(":memory:")
    c.execute("create table runs (id integer primary key, ts text, valid int, discarded int, duplicate_url int, "
              "duplicate_job int, skipped_old int, skipped_non_tech int, skipped_international int, "
              "skipped_clearance int, skipped_blacklisted int, failed_http int, elapsed_seconds real)")
    c.execute("insert into runs values (1,'2026-10-05T16:25:04',112,58,140,235,0,314,2,11,3,0,360)")
    return c


def snap():
    return ss.build(VALID, DISCARD, runs_con(), today=TODAY)


def test_dates_without_year_resolve_to_the_most_recent_past_date():
    assert ss.parse_entry_date("05 October, 09:37 AM", TODAY) == date(2026, 10, 5)
    assert ss.parse_entry_date("20 December, 01:21 PM", TODAY) == date(2025, 12, 20)
    assert ss.parse_entry_date("Feb 24, 2026", TODAY) == date(2026, 2, 24)
    assert ss.parse_entry_date("N/A", TODAY) is None
    assert ss.parse_entry_date("garbage", TODAY) is None


def test_personal_columns_never_reach_the_snapshot():
    blob = json.dumps(snap())
    for private in ("Applied", "Rejected", "OA Round 1", "Not Applied", "secret note", "Rescued", "Date Applied", "Status"):
        assert private not in blob
    assert set(snap()["jobs"][0]) == {"id", *ss.PUBLIC_FIELDS}


def test_jobs_newest_first_and_incomplete_rows_dropped():
    jobs = snap()["jobs"]
    assert [j["title"] for j in jobs] == ["Software Engineer Intern", "Data Engineer Co-op", "ML Engineer New Grad"]
    assert jobs[0] == {"id": 1, "company": "Stripe", "title": "Software Engineer Intern", "location": "San Francisco, CA",
                       "source": "SWE List", "url": "https://s/1", "job_type": "Internship", "remote": True,
                       "sponsored": True, "track": "SDE", "entry_date": "2026-10-05"}


def test_numbers_agree_across_views():
    s = snap()
    assert s["summary"]["valid_jobs"] == s["insights"]["valid_postings"] == len(s["jobs"]) == 3
    assert s["summary"]["sponsored_share"] == round(2 / 3, 4)
    stripe = next(c for c in s["companies"] if c["company"] == "Stripe")
    assert stripe == {"company": "Stripe", "total": 2, "valid": 2, "sponsored": 2, "sponsorship_rate": 1.0,
                      "valid_rate": 1.0, "sources": 1}
    assert s["insights"]["companies_sponsoring"] == 1 and s["insights"]["top_sponsors"][0]["company"] == "Stripe"
    assert sum(w["valid"] for w in s["weekly"]) == 3
    assert sum(w["postings"] for w in s["weekly"]) == 3 + 2, "the discarded row with an unparseable date is skipped"
    assert s["summary"]["runs"] == 1 and s["summary"]["jobs_evaluated"] == 875


def test_sources_and_discard_reasons():
    s = snap()
    swe = next(x for x in s["sources"] if x["source"] == "SWE List")
    assert (swe["accepted"], swe["rejected"], swe["yield_rate"]) == (2, 1, round(2 / 3, 4))
    assert [(q["reason"], q["rows"]) for q in s["quarantine"]] == [("Expired", 2), ("Senior role", 1)]


def test_records_handles_short_rows_and_duplicate_headers():
    rows = ss.records([["A", "B", "A"], ["1"], ["", "", ""], ["x", "y", "z"]])
    assert rows == [{"A": "1", "B": ""}, {"A": "x", "B": "y"}]


def test_yearless_dates_raise_no_deprecation_warning():
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert ss.parse_entry_date("05 October, 09:37 AM", TODAY) == date(2026, 10, 5)
        assert ss.parse_entry_date("29 February", date(2028, 3, 1)) == date(2028, 2, 29)
        assert ss.parse_entry_date("29 February", date(2026, 3, 1)) is None
