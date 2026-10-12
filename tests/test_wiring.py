"""
The five tests that would have caught every bug found in the audit.

Each targets a real failure that 265 existing tests missed, because they
tested that code EXISTED rather than that it WORKED.
"""
import ast
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ── 1. Age parser must handle every format the 9 repos actually emit ──
def test_age_parser_handles_all_real_formats():
    from aggregator.run_aggregator import UnifiedJobAggregator as U
    p = U._parse_github_age
    expect = {
        "0d": 0, "3d": 3, "11d": 11, "116d": 116,
        "11m": 0, "52m": 0, "20h": 0, "0m": 0,     # zapplyjobs minutes/hours
        "1mo": 30, "2mo": 60, "1w": 7, "2w": 14,
    }
    for raw, want in expect.items():
        got = p(raw)
        assert got == want, "{!r} -> {} (expected {})".format(raw, got, want)
    for junk in ("", "N/A", "Apply", None):
        assert p(junk) is None, "{!r} should be unparseable".format(junk)


# ── 2. A blank company cell must not shift every column left ──
def test_blank_cell_does_not_shift_columns():
    from aggregator.extractors import SimplifyGitHubScraper as S
    md = ("| Company | Role | Location | Terms | Application | Age |\n"
          "|---|---|---|---|---|---|\n"
          "| Disney | CS Intern | Lake Buena Vista, FL | Spring 2027 | [Apply](https://x.co/a) | 0d |\n"
          "|  | Labor Systems Intern | Lake Buena Vista, FL | Winter 2027 | [Apply](https://x.co/b) | 0d |\n")
    rows = S._parse_markdown_text(md, "test")
    assert len(rows) == 2, "expected 2 rows, got {}".format(len(rows))
    # the blank-company row must INHERIT, not absorb its own title
    assert rows[1]["company"] == "Disney", rows[1]
    assert "Intern" in rows[1]["title"], rows[1]
    assert "," in rows[1]["location"], rows[1]


# ── 3. ATS scrapers must never hardcode an age ──
def test_ats_scrapers_do_not_hardcode_age():
    src = open(os.path.join(BASE, "aggregator", "direct_sources.py"), encoding="utf-8").read()
    hardcoded = src.count('"age": "0d"')
    # HackerNews is the only legitimate case (monthly thread, no per-job date)
    assert hardcoded <= 1, (
        "{} scrapers still hardcode age='0d'. Every direct-ATS job would "
        "claim it was posted today and the age filter would be meaningless."
        .format(hardcoded))


def test_pick_age_uses_the_right_loop_variable():
    """A mismatched variable raises NameError that gets swallowed, so the
    scraper silently returns zero jobs."""
    src = open(os.path.join(BASE, "aggregator", "direct_sources.py"), encoding="utf-8").read()
    bad = []
    for fn in re.finditer(r"def (scrape_\w+)\(", src):
        name = fn.group(1)
        i = fn.start()
        j = src.find("\ndef ", i + 1)
        body = src[i:j if j != -1 else len(src)]
        loops = set(re.findall(r"for (\w+) in ", body))
        for var in re.findall(r"_pick_age\((\w+),", body):
            if var not in loops:
                bad.append("{}: _pick_age({}) but loops are {}".format(name, var, sorted(loops)))
    assert not bad, "\n".join(bad)


# ── 4. The learning loop must round-trip: writer -> brain -> reader ──
def test_learning_loop_round_trips(tmp_path):
    import aggregator.apply_learned as al
    # the reader must resolve to the REAL brain.json, not a subfolder
    assert os.path.basename(os.path.dirname(al.BRAIN_FILE)) == ".local", al.BRAIN_FILE
    assert os.path.exists(al.BRAIN_FILE), "reader points at a nonexistent file: " + al.BRAIN_FILE

    # writer and reader must agree on the key names
    qg = open(os.path.join(BASE, "scripts", "quality_gate.py"), encoding="utf-8").read()
    for key in ("learned_slugs", "learned_non_tech", "learned_clearance"):
        assert key in qg, "quality_gate never writes " + key
        assert key in open(al.__file__, encoding="utf-8").read(), "apply_learned never reads " + key

    # and the writer must actually be CALLED, not just defined
    for meth in ("add_slug_fix", "add_non_tech_title", "add_clearance_company"):
        calls = len(re.findall(r"\.{}\(".format(meth), qg))
        assert calls >= 1, "{} is defined but never called".format(meth)


# ── 5. Config rewrites must produce parseable Python ──
def test_auto_blacklist_rewrite_is_valid_python():
    """f'\\1' in an f-string is the control character \\x01, not a regex
    backreference. That silently destroyed the variable declaration."""
    cfg = 'COMPANY_BLACKLIST = [\n    "Existing",\n]\n'
    company = "BadCorp"
    out = re.sub(r"(COMPANY_BLACKLIST\s*=\s*\[)", f'\\1\n    "{company}",', cfg, count=1)
    ast.parse(out)                                   # must not raise
    assert "COMPANY_BLACKLIST" in out, "declaration was destroyed"
    assert "\x01" not in out, "control character present"

    src = open(os.path.join(BASE, "scripts", "build_auto_blacklist.py"), encoding="utf-8").read()
    assert "\x01" not in src, "build_auto_blacklist contains a literal control character"


# ── bonus: no source file may contain control characters ──
def test_no_control_characters_in_source():
    """The \\b -> \\x08 bug killed 4 filters silently. The \\1 -> \\x01 bug
    killed auto-blacklist. Same class, twice — so guard the whole tree."""
    bad = []
    for root, dirs, files in os.walk(BASE):
        dirs[:] = [d for d in dirs if d not in
                   ("venv", ".venv", "node_modules", "__pycache__", ".git", ".local")]
        for f in files:
            if not f.endswith(".py"):
                continue
            p = os.path.join(root, f)
            try:
                text = open(p, encoding="utf-8", errors="replace").read()
            except Exception:
                continue
            for ch in ("\x01", "\x08", "\x02", "\x03", "\x07"):
                if ch in text:
                    bad.append("{}: contains {!r}".format(os.path.relpath(p, BASE), ch))
    assert not bad, "\n".join(bad)


# ── bonus: every scheduler job type must have a dispatch branch ──
def test_every_job_type_is_dispatched():
    """quality_gate and health_heartbeat were type='post_write' with no
    branch in the loop, so they never ran once."""
    src = open(os.path.join(BASE, "scripts", "scheduler.py"), encoding="utf-8").read()
    declared = set(re.findall(r'"type"\s*:\s*"(\w+)"', src))
    loop = src[src.find("for job in JOBS:", src.find("while True")):]
    loop = loop[:loop.find("time.sleep")]
    handled = set(re.findall(r'job\["type"\] == "(\w+)"', loop))
    extra = set(re.findall(r'get\("type"\) != "(\w+)"', src))   # post_write runner
    missing = declared - handled - extra
    assert not missing, "job types with no dispatch branch: {}".format(sorted(missing))


# ── 6. The ATS resolver must be reachable and see discovered boards ──
def test_resolver_sees_discovered_boards():
    """_try_ats_lookup imports the hardcoded company dicts at call time.
    Without merging ats_discovery's findings it sees ~263 companies instead
    of ~820, so most lookups fail and jobs fall back to search URLs."""
    src = open(os.path.join(BASE, "aggregator", "run_aggregator.py"), encoding="utf-8").read()
    i = src.find("def _try_ats_lookup")
    assert i != -1, "_try_ats_lookup is gone"
    j = src.find("\n    def ", i + 1)
    body = src[i:j if j != -1 else len(src)]
    assert "_load_discovered_companies" in body, (
        "resolver does not merge discovered boards — it will only see the "
        "hardcoded companies")


def test_resolver_is_actually_called():
    """It was built, tested, and then only wired into one path. Assert it is
    invoked from at least two places, not merely defined."""
    src = open(os.path.join(BASE, "aggregator", "run_aggregator.py"), encoding="utf-8").read()
    calls = len(re.findall(r"self\._try_ats_lookup\(", src))
    assert calls >= 2, "_try_ats_lookup called from only {} place(s)".format(calls)


# ── 7. The preflight must be clean, and must actually detect breakage ──
def test_preflight_passes_on_current_code():
    from aggregator.preflight import run_preflight
    ok, problems = run_preflight(verbose=False)
    # ADVISORY lines are informational, not gates. The dead code check reports
    # a 68 item backlog that predates it; failing the suite on that would get
    # the whole check muted, which is how _sheets_retry stayed unreachable for
    # months. Only real wiring breaks should fail here.
    blocking = [p for p in problems if "ADVISORY" not in p]
    assert not blocking, "preflight found wiring problems:\n" + "\n".join(blocking)


def test_preflight_detects_control_characters(tmp_path, monkeypatch):
    """A checker that cannot fail is worthless - prove it catches the real bug."""
    import aggregator.preflight as pf
    fake = tmp_path / "broken.py"
    fake.write_text("x = 1\n# \x01 injected\n")
    monkeypatch.setattr(pf, "_iter_py", lambda: [str(fake)])
    found = pf.check_control_characters()
    assert found, "control-character check failed to detect \\x01"


def test_preflight_is_wired_into_the_aggregator():
    """It only protects anything if it actually runs."""
    src = open(os.path.join(BASE, "aggregator", "run_aggregator.py"), encoding="utf-8").read()
    assert "run_preflight" in src, "preflight is never called by the aggregator"


# ── 8. Full-time must survive the internship gate; senior must not ──
def test_fulltime_survives_internship_gate():
    """Two gates rejected any non-internship title as a 'senior role'. The only
    exemption was source.startswith('simplify_newgrad'), so 1,594 full-time
    new-grad roles per run were discarded - the largest single loss found."""
    from aggregator.run_aggregator import UnifiedJobAggregator as U, _is_senior_title
    from aggregator.processors import TitleProcessor as T

    def kept(title, source):
        jt = U._detect_job_type(title, source)
        intern, _ = T.is_internship_role(title)
        is_ft = (jt.strip().lower() not in ("internship", "intern", "co-op", "coop")
                 and not _is_senior_title(title))
        return intern or is_ft

    must_keep = [("Software Engineer I", "greenhouse_direct"),
                 ("New Grad: Software Engineer", "cvrve_newgrad"),
                 ("Associate Software Engineer", "indeed_direct"),
                 ("Backend Engineer, Payments", "ashby_direct"),
                 ("Software Engineer", "speedyapply_swe_newgrad"),
                 ("Software Engineer Intern", "vanshb03_offseason")]
    for t, s in must_keep:
        assert kept(t, s), "full-time/new-grad role wrongly rejected: {} [{}]".format(t, s)

    must_drop = [("Senior Staff Software Engineer", "greenhouse_direct"),
                 ("Principal Engineer", "indeed_direct"),
                 ("Engineering Manager", "zapplyjobs_it"),
                 ("Staff Data Scientist", "greenhouse_direct"),
                 ("Director of Engineering", "ashby_direct")]
    for t, s in must_drop:
        assert not kept(t, s), "senior role wrongly kept: {} [{}]".format(t, s)


def test_seniority_gate_is_not_source_name_matching():
    """The original bug: exemption keyed on the source STRING, which missed
    every direct-ATS source and Indeed. Assert job type drives it instead."""
    src = open(os.path.join(BASE, "aggregator", "run_aggregator.py"), encoding="utf-8").read()
    assert "_is_senior_title" in src, "seniority filter is gone"
    assert src.count("_detect_job_type(title, source)") >= 1 or \
           src.count("_detect_job_type(title, _src)") >= 1, \
           "internship gate is no longer job-type aware"


# ── 9. Fuzzy dedup must not collapse distinct roles ──
def test_fuzzy_dedup_keeps_specialisations_and_levels():
    """TF-IDF averages away the one token that carries the meaning:
    "Software Engineer II" scored 1.00 against "Software Engineer I", and
    "DeFi Algorithmic Trader" scored 0.99 against "Algorithmic Trader".
    Both were dropped as duplicates. The guard is word-set based, not
    positional, so reorderings still merge."""
    from analytics.similarity import _differs_on_discriminator as D

    must_differ = [
        ("Software Engineer II", "Software Engineer I"),
        ("DeFi Algorithmic Trader", "Algorithmic Trader"),
        ("Research Scientist - Agents", "Research Scientist"),
        ("Pharmacy Technician Back End", "Pharmacy Technician"),
        ("Data Scientist Intern - NLP", "Data Scientist Intern"),
        ("Backend Engineer, Payments", "Frontend Engineer, Payments"),
    ]
    for a, b in must_differ:
        assert D(a, b), "distinct roles wrongly merged: {!r} vs {!r}".format(a, b)

    must_match = [
        ("Software Engineer Intern", "Software Engineering Intern"),
        ("Single-Family Software Developer Intern",
         "Software Developer Intern - Single-Family"),
        ("Operations Analyst - US Government Services",
         "Operations Analyst - US Government"),
        ("Aerospace Algorithms Engineer Co-op",
         "Aerospace Algorithms Engineer Graduate Co-op"),
    ]
    for a, b in must_match:
        assert not D(a, b), "same role wrongly split: {!r} vs {!r}".format(a, b)


def test_non_identifying_urls_are_not_dedup_keys():
    """clean_url strips the query string, so all 306 google-search rows
    collapse to one key. Deduping on it would drop the next fallback job of
    any company as a duplicate of an unrelated one."""
    from aggregator.utils import is_identifying_url as I, URLCleaner
    a = "https://www.google.com/search?q=AMD+Data+Analyst"
    b = "https://www.google.com/search?q=Disney+SWE"
    assert URLCleaner.clean_url(a) == URLCleaner.clean_url(b), \
        "premise changed: these no longer collide"
    assert not I(a) and not I(b), "search URLs must not be dedup keys"
    assert I("https://job-boards.greenhouse.io/twitch/jobs/8459320002"), \
        "real job URLs must remain dedup keys"


# ── 10. Brain attribute name, and company-name learning ──
def test_brain_data_attribute_is_not_misused():
    """outreach.brain.Brain exposes _data, not data. Three call sites used
    .data and raised AttributeError on every job, all inside `except: pass` -
    so company-name learning and the LinkedIn->ATS cache never once worked.
    Found by the swallowed-exception counter on its first run: 145 failures."""
    from outreach.brain import Brain
    b = Brain.get()
    assert hasattr(b, "_data"), "Brain no longer has _data"
    assert not hasattr(b, "data"), \
        "Brain now has .data - the guard below can be relaxed"

    import re
    for rel in ("aggregator/processors.py", "aggregator/run_aggregator.py",
                "outreach/brain.py"):
        src = open(os.path.join(BASE, rel), encoding="utf-8").read()
        code = "\n".join(l for l in src.splitlines()
                         if not l.strip().startswith("#"))
        bad = re.findall(r"\bb\.data\[|\bBrain\.get\(\)\.data\b", code)
        assert not bad, "{} uses Brain.data (should be _data): {}".format(rel, bad[:3])


def test_company_slug_is_the_registrable_domain():
    """Taking domain.split('.')[0] learned {'boards': 'Figma'} from
    boards.greenhouse.io and skipped careers.stripe.com entirely - the
    subdomain was being treated as the company."""
    from outreach.brain import Brain
    from aggregator.processors import CompanyExtractor

    b = Brain.get()
    saved = dict(b._data.get("learned_company_names", {}))
    b._data["learned_company_names"] = {}
    try:
        CompanyExtractor.learn_company_name("https://careers.stripe.com/jobs/1", "Stripe")
        CompanyExtractor.learn_company_name("https://boards.greenhouse.io/figma/jobs/1", "Figma")
        CompanyExtractor.learn_company_name("https://jobs.lever.co/palantir/abc", "Palantir")
        got = dict(Brain.get()._data.get("learned_company_names", {}))
    finally:
        b._data["learned_company_names"] = saved
        b.save()

    assert "stripe" in got, "real company domain not learned: {}".format(got)
    for ats in ("boards", "greenhouse", "lever", "jobs", "careers"):
        assert ats not in got, "ATS/subdomain slug wrongly learned: {}".format(got)


# ── 11. Title filters must not reject software roles ──
def test_software_roles_survive_the_hardware_filters():
    """Three filters were rejecting genuine software jobs:
      - 'engineer II' was treated as senior; II is 1-3 years, still early career
      - the hardware list matched \\bembedded and \\bsensor before checking
        whether the title said 'software'
      - INVALID_TITLE_KEYWORDS had over-broad robotics / test-engineering /
        computer-vision patterns
    23 distinct titles in a single run were lost to these."""
    from aggregator.processors import TitleProcessor as T

    must_keep = [
        "Embedded Software Engineer Intern",
        "Embedded Software Engineer Co-op",
        "Sensor Software Engineer - Core Sensors",
        "Robotics Software Engineer",
        "Software Engineer II, Backend",
        "Deep Learning Engineer II",
        "Security Engineer II, Hybrid Cloud",
        "Software Test Engineering Intern",
        "Computer Vision Engineering Intern",
        "Manufacturing Software Controls Engineer",
        "Perception Software Engineer",
    ]
    for t in must_keep:
        ok, why = T.is_valid_job_title(t)
        assert ok, "software role wrongly rejected: {!r} ({})".format(t, why)


def test_hardware_and_trade_roles_still_rejected():
    """Relaxing the filters must not open the gate to non-software work."""
    from aggregator.processors import TitleProcessor as T

    must_drop = [
        "Electrical Engineering Intern",
        "Mechanical Engineering Intern",
        "Manufacturing Engineer",
        "Motor Controls Engineer II - R&D",
        "Hardware Support & Test Intern",
        "FPGA Design Engineer",
        "PCB Layout Intern",
        "Robotics Technician",
        "Wafer Manufacturing Process Technician",
        "Project Controls Engineer II",
        "Drafter/Design Engineer II",
        "Validation Engineer II",
        "Software Developer III",
    ]
    for t in must_drop:
        ok, _ = T.is_valid_job_title(t)
        assert not ok, "non-software role wrongly accepted: {!r}".format(t)


# ── 12. The user blacklist must be read, and must not over-match ──
def test_user_blacklist_is_actually_read():
    """user_blacklist_companies was write-only for a few hours: entries went
    into brain.json and nothing read them, so blacklisted companies would
    have reappeared on the next run. Same shape as the learning loop that
    stayed dead for months."""
    from aggregator import apply_learned
    assert hasattr(apply_learned, "is_user_blacklisted"), \
        "no reader for user_blacklist_companies"
    src = open(os.path.join(BASE, "aggregator", "run_aggregator.py"),
               encoding="utf-8").read()
    assert "is_user_blacklisted" in src, \
        "reader exists but the pipeline never calls it"


def test_user_blacklist_matches_by_prefix_not_substring():
    """A bare substring check blocked 'Phi' because 'philips' contains it.
    Real companies must not be caught by an unrelated blacklist entry."""
    from aggregator.apply_learned import is_user_blacklisted as B
    from outreach.brain import Brain

    b = Brain.get()
    saved = list(b._data.get("user_blacklist_companies", []))
    b._data["user_blacklist_companies"] = ["philips", "morse corp"]
    try:
        for name in ("Philips", "Philips North America", "MORSE Corp Co-op"):
            assert B(name), "blacklisted company not blocked: " + name
        for name in ("Phi", "Philadelphia Energy", "Morse Micro", "Stripe"):
            assert not B(name), "unrelated company wrongly blocked: " + name
    finally:
        b._data["user_blacklist_companies"] = saved
        b.save()


# ── 13. Summer filter must catch the split year/season form ──
def test_summer_filter_catches_separated_year_and_season():
    """'2027 Software Engineering Summer Internship' has the year and the
    season five words apart, so patterns anchored on 'Summer 2027' or
    '2027 Summer' both missed it and the job reached the sheet."""
    from aggregator.term_filter import should_drop_summer as D

    must_drop = [
        "2027 Software Engineering Summer Internship",
        "Software Engineering Intern - Summer 2027",
        "2027 Summer Analyst Program",
        "Summer 2027 Data Science Internship",
    ]
    for t in must_drop:
        assert D(t, job_type="Internship"), "summer role not caught: " + t

    must_keep = [
        "Software Engineer Intern - Fall 2026",
        "SWE Intern, Spring 2027",
        "Data Engineer Co-op - Winter 2027",
        "Software Engineer Intern - 2027",
        "2027 New Grad Software Engineer",
        "SWE Intern - Summer 2027 or Fall 2026",
        "2027 Software Engineering Co-op",
    ]
    for t in must_keep:
        assert not D(t, job_type="Internship"), "wanted role wrongly dropped: " + t

    # rule 1 is absolute: full-time never enters the filter
    assert not D("2027 Software Engineering Summer Internship", job_type="Full Time")


# ── 14. The page title supersedes the feed's ──
def test_page_title_supersedes_feed():
    """Feeds rewrite titles, and every rewrite defeats a filter: JHU APL's
    '2027 PhD Graduate' arrived as 'New Grad', Cadence's 'Electronics
    Hardware Design' as 'Internship, Elect...'. The page is authoritative."""
    from aggregator.title_reconcile import reconcile

    used, dis, _ = reconcile(
        "Data Scientist/Engineer New Grad - Analytic Capabilities",
        "2027 PhD Graduate - AI/ML Data Scientist/Engineer in Laurel, Maryland "
        "| Johns Hopkins Applied Physics Laboratory",
        "Johns Hopkins Applied Physics Laboratory")
    assert dis and "PhD" in used, "page title not preferred: " + used

    # site furniture must never replace a real title
    for junk in ("Careers Home | Acme", "Job Search", ""):
        used, dis, _ = reconcile("Software Engineer Intern", junk, "Acme")
        assert used == "Software Engineer Intern", "furniture won: " + used


def test_phd_titles_rejected_unless_ms_eligible():
    """Only 'phd intern' was covered, so '2027 PhD Graduate', 'Research
    Scientist, PhD' and 'Software Engineer, PhD New Grad' all passed. A title
    offering MS or BS alongside PhD is one you can apply to."""
    from aggregator.processors import TitleProcessor as T
    for t in ("2027 PhD Graduate - AI/ML Data Scientist",
              "Research Scientist, PhD", "Software Engineer, PhD New Grad",
              "PhD Internship - Computer Vision"):
        assert not T.is_valid_job_title(t)[0], "PhD role accepted: " + t
    for t in ("Research Intern - MS or PhD", "Data Scientist - MS/PhD",
              "Machine Learning Engineer - BS/MS/PhD", "Software Engineer I"):
        assert T.is_valid_job_title(t)[0], "MS-eligible role rejected: " + t


# ── 15. Job type must come from the title, not the feed name ──
def test_job_type_reads_title_before_source():
    """The *_newgrad source check ran before the title checks, so every job
    from those feeds came back Full Time - including 'Summer 2027 Intern'.
    One wrong label caused two wrong outcomes: a wrong Job Type column, and
    the summer filter skipping the row because rule 1 exempts full-time."""
    from aggregator.run_aggregator import UnifiedJobAggregator as U
    from aggregator.term_filter import should_drop_summer

    assert U._detect_job_type("Summer 2027 Intern - Software Engineering",
                              "zapplyjobs_newgrad") == "Internship"
    assert U._detect_job_type("Software Engineer Intern",
                              "simplify_newgrad") == "Internship"
    assert U._detect_job_type("Data Science Co-op", "cvrve_newgrad") == "Co-op"
    # genuine full-time roles from those feeds must not regress
    assert U._detect_job_type("New Grad Software Engineer",
                              "zapplyjobs_newgrad") == "Full Time"
    assert U._detect_job_type("Software Engineer",
                              "zapplyjobs_newgrad") == "Full Time"
    assert U._detect_job_type("Backend Engineer", "greenhouse_direct") == "Full Time"

    t = "Summer 2027 Intern - Software Engineering"
    assert should_drop_summer(
        t, job_type=U._detect_job_type(t, "zapplyjobs_newgrad")), \
        "summer role still exempt"


# ── 16. One dedup key, or jobs repeat forever ──
def test_dedup_key_is_stable_across_name_variants():
    """Three sites built the key differently: one stripped Inc/LLC, one called
    normalize_company_for_dedup, and the one that RECORDS the key after a
    write used the raw company. A job was stored as 'bosch group_...' and
    looked up as 'bosch_...', so it was rewritten every run - 153 duplicate
    pairs, each appearing exactly twice."""
    from aggregator.run_aggregator import _dedup_key as K

    for a, b in (("Bosch Group", "Bosch"), ("HP Inc", "HP"),
                 ("Acme Corporation", "Acme"), ("Foo LLC", "Foo")):
        assert K(a, "Software Engineer") == K(b, "Software Engineer"), \
            "name variants produce different keys: {} vs {}".format(a, b)

    # distinct companies must not collide
    assert K("Bosch", "SWE") != K("Boston Dynamics", "SWE")
    assert K("HP", "SWE") != K("HPE", "SWE")
    assert K("Acme", "SWE") != K("Acme", "Data Engineer")


def test_all_dedup_sites_use_the_shared_key():
    """Every place that builds or records a company+title key must call
    _dedup_key. A site that builds its own is how the mismatch happened."""
    import re
    src = open(os.path.join(BASE, "aggregator", "run_aggregator.py"),
               encoding="utf-8").read()
    code = "\n".join(l for l in src.splitlines() if not l.strip().startswith("#"))
    stray = re.findall(r'normalize_text\(f"\{[a-z_]*compan[^)]*\}_\{title\}"\)', code)
    assert not stray, "dedup key built outside _dedup_key: {}".format(stray[:3])
