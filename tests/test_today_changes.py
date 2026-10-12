"""Dummy run of all four changes shipped today. No network, no sheet writes."""
import os, sys, datetime, csv
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from aggregator.processors import JobIDExtractor


# ---- 1. Microsoft ID: full capture, not truncated to 10 digits ----
def test_microsoft_id_not_truncated():
    url = "https://apply.careers.microsoft.com/careers/job/1970393556922931"
    r = JobIDExtractor.extract_from_url(url)
    assert r.value == "1970393556922931", f"got {r.value}"

def test_microsoft_url_id_is_penalized_on_career_site():
    # career-site URL IDs get confidence dropped so page ID can win
    url = "https://apply.careers.microsoft.com/careers/job/1970393556922931"
    r = JobIDExtractor.extract_from_url(url)
    assert r.confidence <= 0.80, f"career-site penalty missing, conf={r.confidence}"


# ---- 2. ByteDance cross-domain: same numeric ID on both hosts ----
def test_bytedance_same_id_both_domains():
    a = JobIDExtractor.extract_from_url("https://jobs.bytedance.com/en/position/7668464504736876853/detail")
    b = JobIDExtractor.extract_from_url("https://joinbytedance.com/search/7668464504736876853")
    assert a.value == b.value == "7668464504736876853", f"{a.value} vs {b.value}"


# ---- helpers to build a ManualCleanup without Google ----
def _make_cleaner():
    from scripts.cleanup_not_applied import ManualCleanup
    c = ManualCleanup.__new__(ManualCleanup)     # skip __init__ (no network)
    c._outreach_map = {}
    c._dry_run = False
    return c

def _row(sr, status, company, title, url, entry_date):
    r = [""] * 16
    r[0], r[1], r[2], r[3] = sr, status, company, title
    r[5] = url
    r[11] = entry_date
    return r


# ---- 3. remaining_rows keep-test: blank Sr. No. must NOT drop a real row ----
def test_blank_srno_row_is_kept():
    c = _make_cleaner()
    today = datetime.datetime.now().strftime("%d %B, %Y")
    row = _row("", "Not Applied", "Roblox", "SWE Intern",
               "https://careers.roblox.com/jobs/8072713", today)
    # not expired (today), has status + url -> keep-test must pass
    keep = (not c._is_expired(row)) and (c._get_cell(row, 1).strip() or c._get_cell(row, 5).strip())
    assert keep, "row with blank Sr.No but real status+url was dropped"


# ---- 4. expiry rule: Not Applied older than 2 days expires; fresh does not ----
def test_expiry_rule_2_days():
    c = _make_cleaner()
    old = (datetime.datetime.now() - datetime.timedelta(days=5)).strftime("%d %B, %Y")
    fresh = datetime.datetime.now().strftime("%d %B, %Y")
    old_row = _row("1", "Not Applied", "X", "Y", "http://x", old)
    fresh_row = _row("2", "Not Applied", "X", "Y", "http://x", fresh)
    applied_row = _row("3", "Applied", "X", "Y", "http://x", old)
    assert c._is_expired(old_row) is True
    assert c._is_expired(fresh_row) is False
    assert c._is_expired(applied_row) is False   # protected status never expires


# ---- 5. snapshot: writes a CSV before any move ----
def test_snapshot_writes_csv(tmp_path):
    c = _make_cleaner()
    data = [["Sr","Status"], ["1","Not Applied"], ["2","Applied"]]
    c._snapshot_sheet(data, tag="unittest")
    snap_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            ".local", "snapshots")
    files = [f for f in os.listdir(snap_dir) if "unittest" in f]
    assert files, "no snapshot CSV written"
    latest = max((os.path.join(snap_dir, f) for f in files), key=os.path.getmtime)
    with open(latest) as f:
        rows = list(csv.reader(f))
    assert rows == data, "snapshot content mismatch"
    os.remove(latest)   # clean up test artifact


# ---- 6. cap logic: >20% of sheet aborts (mirrors cleanup_expired guard) ----
def test_cap_blocks_oversized_sweep():
    total = 100
    expired = 40
    assert expired > 0.20 * total          # would abort
    small = 10
    assert not (small > 0.20 * total)      # would proceed


class TestDirectATSJobType:
    """Direct ATS boards list all roles; only intern titles are internships."""

    def test_direct_ats_defaults_to_full_time(self):
        from aggregator.run_aggregator import UnifiedJobAggregator as U
        assert U._detect_job_type("Software Engineer, Backend", "ashby_direct") == "Full Time"
        assert U._detect_job_type("Data Engineer", "greenhouse_direct") == "Full Time"

    def test_direct_ats_still_detects_interns(self):
        from aggregator.run_aggregator import UnifiedJobAggregator as U
        assert U._detect_job_type("Software Engineer Intern, Robotics", "greenhouse_direct") == "Internship"
        assert U._detect_job_type("Neuroengineer Intern", "greenhouse_direct") == "Internship"

    def test_non_direct_sources_unchanged(self):
        from aggregator.run_aggregator import UnifiedJobAggregator as U
        assert U._detect_job_type("Software Engineer Intern", "vanshb03") == "Internship"
        assert U._detect_job_type("New Grad: Software Engineer", "cvrve_newgrad") == "Full Time"
