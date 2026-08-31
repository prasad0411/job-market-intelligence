#!/usr/bin/env python3

import time
import datetime
import random
import re
import json
import os
import logging
import sqlite3
from collections import defaultdict
from bs4 import BeautifulSoup

from aggregator.url_validator import validate_job, validate_job_integrity
from aggregator.source_health import SourceHealthMonitor
from aggregator.config import (
    GREENHOUSE_COMPANY_MAP,
    GARBAGE_COMPANY_NAMES,
    COMPANY_NAME_FIXES,
    SIMPLIFY_URL,
    VANSHB03_URL,
    SPEEDYAPPLY_SWE_URL,
    SPEEDYAPPLY_AI_URL,
    ZAPPLYJOBS_URL,
    JOBRIGHT_GITHUB_URL,
    SIMPLIFY_OFFSEASON_URL,
    SIMPLIFY_2026_URL,
    ZAPPLYJOBS_2026_URL,
    VANSHB03_OFFSEASON_URL,
    NEWGRAD_SIMPLIFY_URL,
    NEWGRAD_CVRVE_URL,
    SPEEDYAPPLY_SWE_NEWGRAD_URL,
    SPEEDYAPPLY_AI_NEWGRAD_URL,
    VANSHB03_NEWGRAD_URL,
    ZAPPLYJOBS_IT_URL,
    ZAPPLYJOBS_ML_INTERN_URL,
    ZAPPLYJOBS_INTERNSHIPS_2027_URL,
    MAX_JOB_AGE_DAYS,
    PAGE_AGE_THRESHOLD_DAYS,
    MIN_QUALITY_SCORE,
    COMPANY_BLACKLIST,
    COMPANY_BLACKLIST_REASONS,
    PLATFORM_BLACKLIST,
    PLATFORM_BLACKLIST_REASONS,
    BLACKLIST_DOMAINS,
    PROCESSED_EMAILS_FILE,
    EMAIL_TRACKING_RETENTION_DAYS,
    REPROCESS_EMAILS_DAYS,
    EMAIL_DATE_FILTER_ENABLED,
    TERMINAL_COMPANY_WIDTH,
    VERBOSE_OUTPUT,
    SHOW_GITHUB_COUNTS,
)

from aggregator.extractors import (
    EmailExtractor,
    PageFetcher,
    PageParser,
    SourceParsers,
    JobrightAuthenticator,
    JobrightRedirectResolver,
    SimplifyRedirectResolver,
    SimplifyGitHubScraper,
    ZipRecruiterResolver,
    safe_parse_html,
    retry_request,
)

try:
    from scripts.pipeline_brain import PipelineBrain
    _BRAIN = PipelineBrain.get()
except ImportError:
    _BRAIN = None

from aggregator.processors import (
    TitleProcessor,
    LocationExtractor,
    LocationProcessor,
    ValidationHelper,
    CompanyExtractor,
    log_detailed_rejection,
)

from aggregator.sheets_manager import SheetsManager

from aggregator.utils import (
    QualityScorer,
    PlatformDetector,
    CompanyNormalizer,
    CompanyValidator,
    RoleCategorizer,
    URLCleaner,
    DateParser,
    DataSanitizer,
    ExtractionVoter,
)

logging.basicConfig(
    filename=os.path.join(".local", "skipped_jobs.log"),
    filemode="a",
    level=logging.INFO,
    force=True,
    format="%(asctime)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

# Log header written via logging
logging.info("=" * 80)
logging.info(f"JOB PROCESSING LOG - {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

# Greenhouse company slug extraction
# GREENHOUSE_COMPANY_MAP lives in config.py - identical copies today,
# but two copies drift. One source of truth.

# GARBAGE_COMPANY_NAMES lives in config.py (114 entries). A local
# 42-entry copy shadowed it here, so 72 curated entries were ignored on
# the GitHub path - 'Myworkdaysite', 'Smartrecruiters' and 'SMX' all
# reached the sheet as a result. Verified subset: nothing local-only.


# Company name normalization — fix common extraction errors
# COMPANY_NAME_FIXES lives in config.py (339 entries). A local 40-entry
# copy used to shadow it here, so the GitHub path only ever applied 40 of
# the fixes you had curated. Verified subset with zero conflicting values.


class JobrightEmailParser:
    @staticmethod
    def parse_email_jobs(email_html):
        if not email_html:
            return {}

        try:
            soup = BeautifulSoup(email_html, "html.parser")
            job_map = {}

            job_sections = soup.find_all("table", id="job-section")

            for section in job_sections:
                try:
                    parent_link = section.find_parent(
                        "a", href=re.compile(r"jobright\.ai/jobs/info/")
                    )
                    if not parent_link:
                        title_link = section.find("p", id="job-title")
                        if title_link:
                            a_tag = title_link.find(
                                "a", href=re.compile(r"jobright\.ai/jobs/info/")
                            )
                            if a_tag:
                                jr_url = a_tag.get("href", "")
                            else:
                                continue
                        else:
                            continue
                    else:
                        jr_url = parent_link.get("href", "")

                    if not jr_url or "jobright.ai/jobs/info/" not in jr_url:
                        continue

                    company_elem = section.find("p", id="job-company-name")
                    company = (
                        re.sub(r"\s+", " ", company_elem.get_text(separator=" ", strip=True)).strip()
                        if company_elem else "Unknown"
                    )

                    title_elem = section.find("p", id="job-title")
                    title = (
                        re.sub(r"\s+", " ", title_elem.get_text(separator=" ", strip=True)).strip()
                        if title_elem else "Unknown"
                    )

                    location = "Unknown"
                    tags = section.find_all("p", id="job-tag")
                    for tag in tags:
                        tag_text = tag.get_text(strip=True)
                        if not tag_text:
                            continue
                        if any(
                            skip in tag_text
                            for skip in ["$", "/hr", "/yr", "/mo", "referral"]
                        ):
                            continue
                        if re.search(r"[A-Z][a-z]+.*,\s*[A-Z]{2}", tag_text):
                            location = tag_text
                            break
                        if "remote" in tag_text.lower() and len(tag_text) < 20:
                            location = "Remote"
                            break
                        if len(tag_text) < 60 and "," in tag_text:
                            location = tag_text
                            break

                    clean_jr_url = re.sub(r"\?.*$", "", jr_url)
                    job_data = {
                        "company": company,
                        "title": title,
                        "location": location,
                        "apply_url": None,
                    }
                    job_map[clean_jr_url] = job_data
                    job_map[jr_url] = job_data

                except Exception as e:
                    logging.debug(f"Failed to parse job section: {e}")
                    continue

            unique_count = len(set(id(v) for v in job_map.values()))
            logging.info(f"Jobright email parser: extracted {unique_count} job cards")
            return job_map

        except Exception as e:
            logging.error(f"Jobright email parser failed: {e}")
            return {}


class ProcessedEmailTracker:
    @staticmethod
    def load():
        if os.path.exists(PROCESSED_EMAILS_FILE):
            try:
                with open(PROCESSED_EMAILS_FILE, "r") as f:
                    data = json.load(f)
                cutoff = (
                    datetime.datetime.now()
                    - datetime.timedelta(days=EMAIL_TRACKING_RETENTION_DAYS)
                ).strftime("%Y-%m-%d")
                cleaned = {
                    k: v
                    for k, v in data.items()
                    if v.get("processed_date", "") >= cutoff
                }
                return cleaned
            except Exception:
                return {}
        return {}

    @staticmethod
    def save(processed_emails):
        try:
            # FIX 11: cap at 10,000 entries — prune oldest to prevent unbounded growth
            MAX_ENTRIES = 10000
            if len(processed_emails) > MAX_ENTRIES:
                sorted_items = sorted(
                    processed_emails.items(),
                    key=lambda x: x[1].get("processed_date", "") + x[1].get("processed_time", "")
                )
                processed_emails = dict(sorted_items[-MAX_ENTRIES:])
                logging.info(f"ProcessedEmailTracker pruned to {MAX_ENTRIES} entries")
            with open(PROCESSED_EMAILS_FILE, "w") as f:
                json.dump(processed_emails, f, indent=2)
        except Exception as e:
            logging.error(f"Failed to save processed emails: {e}")

    @staticmethod
    def mark_email_processed(processed_emails, email_id, subject, url_count):
        processed_emails[email_id] = {
            "subject": subject,
            "processed_date": datetime.datetime.now().strftime("%Y-%m-%d"),
            "processed_time": datetime.datetime.now().strftime("%H:%M:%S"),
            "url_count": url_count,
        }


# Module-level Claude sponsorship classifier
# Returns "no" if company is known to not sponsor, "unknown" otherwise.
# Uses a simple cache to avoid repeat API calls for same company.
_SPONSORSHIP_CACHE = {}
import threading as _threading
class _NOOP_LOCK:
    def __enter__(self): return self
    def __exit__(self, *a): pass
_NOOP_LOCK = _NOOP_LOCK()

# (a stub 'def _load_sponsorship_from_brain(): pass' lived here and was
#  shadowed by the real definition below - removed)

# Auto-prune stale failed URL cache on startup
try:
    from aggregator.extractors import PageFetcher as _PF
    _PF._prune_failed_urls()
except Exception:
    pass

def _load_sponsorship_from_brain():
    """Load Brain sponsorship cache into memory at startup."""
    try:
        from outreach.brain import Brain
        b = Brain.get()
        cached = b._data.get("sponsorship", {})
        _SPONSORSHIP_CACHE.update(cached)
        if cached:
            import logging as _log
            _log.getLogger(__name__).info(
                f"Loaded {len(cached)} sponsorship entries from Brain"
            )
    except Exception:
        pass

_load_sponsorship_from_brain()

def _claude_sponsorship_check(company, title):
    """
    Ask Claude whether this company sponsors F-1/H-1B visas.
    Returns 'no' ONLY when highly confident. Returns 'unknown' on any doubt.
    Self-learning: results cached in Brain permanently — same company never re-queried.
    Skips if ANTHROPIC_API_KEY not set — zero impact on existing behaviour.
    """
    # Check Brain cache first — permanent, cross-run memory
    try:
        from outreach.brain import Brain
        _brain = Brain.get()
        _bspons = _brain._data.get("sponsorship", {}).get(company.lower().strip())
        if _bspons is not None:
            return _bspons
    except Exception:
        pass
    import os as _os
    _root = _os.path.dirname(_os.path.abspath(__file__))
    _env = _os.path.join(_root, ".env")
    _api_key = ""
    if _os.path.exists(_env):
        for ln in open(_env):
            ln = ln.strip()
            if ln.startswith("ANTHROPIC_API_KEY="):
                _api_key = ln.split("=", 1)[1].strip()
                break
    _api_key = _api_key or _os.environ.get("ANTHROPIC_API_KEY", "")
    if not _api_key:
        return "unknown"

    cache_key = company.lower().strip()
    if cache_key in _SPONSORSHIP_CACHE:
        return _SPONSORSHIP_CACHE[cache_key]

    try:
        import urllib.request, json as _j
        prompt = (
            f"Company: {company}\nJob title: {title}\n\n"
            "Does this company sponsor F-1 OPT or H-1B visas for internships? "
            "Answer ONLY with one word: 'yes', 'no', or 'unknown'. "
            "IMPORTANT: Answer 'no' ONLY if you are 95%+ certain this company NEVER "
            "sponsors international students. Answer 'unknown' for any uncertainty. "
            "When in doubt, always answer 'unknown'. Never guess 'no'."
        )
        body = _j.dumps({
            "model": "claude-haiku-4-5-20251001",
            "max_tokens": 10,
            "messages": [{"role": "user", "content": prompt}]
        }).encode()
        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=body,
            headers={
                "x-api-key": _api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            }
        )
        resp = urllib.request.urlopen(req, timeout=8)
        data = _j.loads(resp.read())
        answer = data["content"][0]["text"].strip().lower().rstrip(".")
        result = "no" if answer == "no" else "unknown"
        _SPONSORSHIP_CACHE[cache_key] = result
        # Save to Brain permanently — never re-query same company again
        try:
            from outreach.brain import Brain
            b = Brain.get()
            if "sponsorship" not in b._data:
                b._data["sponsorship"] = {}
            b._data["sponsorship"][cache_key] = result
            b.save()
        except Exception as _sw:
            from aggregator.swallowed import swallow as _s; _s('brain.sponsorship_cache_write', _sw)
        logging.info(f"Claude sponsorship: {company} → {result} (saved to Brain)")
        return result
    except Exception as e:
        logging.debug(f"Claude sponsorship check failed for {company}: {e}")
        return "unknown"


def _h1b_sponsorship(company, feed_value="Unknown"):
    """Sponsorship verdict, feed signal first then official USCIS records.

    Order matters: the feed's Visa column reflects THIS posting, while USCIS
    reflects the company historically. A posting that says "does not sponsor"
    outranks the fact that the parent company sponsors elsewhere.
    """
    if feed_value and feed_value != "Unknown":
        return feed_value
    try:
        from aggregator.h1b_data import lookup
        verdict, approvals, _matched = lookup(company)
        if verdict == "Yes":
            return "Yes"
    except Exception as _he:
        logging.debug(f"h1b lookup failed for {company}: {_he}")
    return "Unknown"


def _feed_sponsorship(job, default="Unknown"):
    """Use the sponsorship the feed gave us (zapplyjobs Visa column, or the
    legend emoji other repos use) instead of blanking it to Unknown. The
    parser already extracted this; three build paths were discarding it."""
    if isinstance(job, dict):
        v = job.get("sponsorship")
        if v and v != "Unknown":
            return v
    return default


_SENIOR_TITLE_RE = re.compile(
    r"\b(?:senior|sr\.?|staff|principal|lead|manager|director|head\s+of|"
    r"vp|vice\s+president|architect|distinguished|fellow|chief)\b", re.I)


def _is_senior_title(title):
    """True for roles above entry level.

    Seniority used to be filtered as a side effect of the internship check.
    Once full-time roles were allowed through that check, Senior/Staff/
    Principal/Manager titles had nothing stopping them. direct_sources and
    the LinkedIn path already filter these; the GitHub feeds and Indeed did
    not. An explicit intern/new-grad marker always wins - "New Grad Lead
    Engineer" is a new-grad role.
    """
    if not title:
        return False
    if re.search(r"\b(?:intern|co-?op|new\s*grad|newgrad|entry[\s-]level|"
                 r"university\s+grad|campus)\b", title, re.I):
        return False
    return bool(_SENIOR_TITLE_RE.search(title))


def _dedup_key(company, title):
    """THE dedup key. One definition, used by every site that builds one.

    There were three: line 3899 stripped Inc/LLC/Ltd/Corp, line 3309 called
    normalize_company_for_dedup, and line 3819 - the one that RECORDS the key
    after a write - used the raw company with no normalisation at all.

    So a job was stored under "bosch group_software engineer" and looked up
    under "bosch_software engineer". They never matched, and the job was
    rewritten every run. 153 duplicate pairs in Not Applied, every one
    appearing exactly twice, same source, same day.

    COMPANY_NAME_FIXES is applied FIRST, because that is the name the sheet
    ends up holding. Normalising after the fix is what makes the stored key
    and the lookup key identical.
    """
    import re as _r
    from aggregator.utils import URLCleaner as _U
    c = (company or "").strip()
    try:
        from aggregator.config import COMPANY_NAME_FIXES as _F
        c = _F.get(c, _F.get(c.lower(), c))
    except Exception:
        pass
    c = _r.sub(r",?\s*(Inc\.?|LLC|Ltd\.?|Corp\.?|Corporation|Group|"
               r"L\.?P\.?|Co\.?)\s*$", "", str(c), flags=_r.I).strip()
    c = _r.sub(r"\s*\([^)]+\)\s*$", "", c).strip()
    return _U.normalize_text("{}_{}".format(c, title or ""))


class UnifiedJobAggregator:
    def __init__(self):
        print("=" * 80)
        self.sheets = SheetsManager()
        self.email_extractor = EmailExtractor()
        self.page_fetcher = PageFetcher()
        self.jobright_auth = JobrightAuthenticator()

        existing = self.sheets.load_existing_jobs()
        self.existing_jobs = existing["jobs"]
        self.existing_urls = existing["urls"]
        self.existing_job_ids = existing["job_ids"]
        self.processed_cache = existing["cache"]

        self.processing_lock = set()
        self.valid_jobs = []
        self.discarded_jobs = []
        self.duplicate_jobs = []

        self.outcomes = defaultdict(int)
        self.source_stats = defaultdict(lambda: defaultdict(int))
        import threading as _t; self._github_lock = _t.Lock()  # thread safety for parallel processing

        print(
            # (loaded silently)
        )
        logging.info(f"Loaded {len(self.existing_jobs)} existing jobs from sheets")

    def run(self):
        # ── Run lock: prevent duplicate simultaneous runs ──
        _lock_file = os.path.join(".local", "aggregator.lock")
        if os.path.exists(_lock_file):
            try:
                _lock_age = time.time() - os.path.getmtime(_lock_file)
                if _lock_age < 600:  # 10 minutes
                    print("⚠️  Another aggregator run is in progress (lock file < 10 min old). Exiting.")
                    logging.warning(f"Skipped: lock file exists, age={_lock_age:.0f}s")
                    return
                else:
                    logging.info(f"Stale lock file ({_lock_age:.0f}s old) — removing")
                    os.remove(_lock_file)
            except Exception:
                pass
        try:
            os.makedirs(os.path.dirname(_lock_file), exist_ok=True)
            with open(_lock_file, "w") as _lf:
                _lf.write(f"{os.getpid()}\n{time.time()}")
        except Exception:
            pass

        start_time = time.time()

        # Selenium health check — catch ChromeDriver mismatches immediately
        self._check_selenium_health()

        if not self.jobright_auth.cookies:
            if os.environ.get("CI") or os.environ.get("GITHUB_ACTIONS"):
                logging.warning("CI environment: skipping Jobright interactive login. Using email-only mode.")
            else:
                self.jobright_auth.login_interactive()

        # (silent)

        # ── PREFLIGHT: verify every wire is connected before doing work ──
        # Lives HERE, inside the one process that provably runs 3x daily.
        # Every previous safety layer (config validator, watchdog, 265 tests)
        # had the same bug it was meant to catch. This breaks that recursion.
        # Never blocks a run - reports loudly and continues.
        try:
            from aggregator.preflight import run_preflight
            _pf_ok, _pf_problems = run_preflight(verbose=True)
            if not _pf_ok:
                self.outcomes["preflight_problems"] = len(_pf_problems)
        except Exception as _pfe:
            logging.warning(f"preflight check failed to run: {_pfe}")

        # ── Direct ATS API sources (Greenhouse, Lever, Ashby, HackerNews) ──
        try:
            from aggregator.direct_sources import fetch_all_direct_sources
            direct_jobs = fetch_all_direct_sources()
            for dj in direct_jobs:
                dj["_source_name"] = dj.get("source", "direct")
                dj["github_category"] = "Direct ATS API"
            if direct_jobs:
                import concurrent.futures as _cf
                from aggregator.extractors import (
                    already_fetched, mark_fetched, save_fetched_urls,
                )
                # Skip job pages we already read in a previous run
                _fresh = [_j for _j in direct_jobs
                          if _j.get("url") and not already_fetched(_j["url"])]
                logging.info(
                    f"Direct ATS: {len(direct_jobs)} fetched, "
                    f"{len(_fresh)} new (rest already seen)"
                )

                def _read_page(_j):
                    """Open the real job page so every column is accurate."""
                    try:
                        self._process_single_job_comprehensive(
                            _j["url"],
                            company_hint=_j.get("company", ""),
                            title_hint=_j.get("title", ""),
                            location_hint=_j.get("location", ""),
                            source=_j.get("source", "direct_ats"),
                        )
                        mark_fetched(_j["url"])
                    except Exception:
                        raise

                _errs = 0
                with _cf.ThreadPoolExecutor(max_workers=6) as _pool:
                    _futs = {_pool.submit(_read_page, _j): _j for _j in _fresh}
                    for _f in _cf.as_completed(_futs):
                        try:
                            _f.result()
                        except Exception as _e:
                            _errs += 1
                            logging.error(
                                f"Direct ATS job failed "
                                f"{_futs[_f].get('company','?')}: {_e}"
                            )
                save_fetched_urls()
                # Persist the HTTP response cache too. _save_http_cache() was
                # written with a 6-hour TTL and a 500-entry cap, then never
                # called - so the cache only ever lived inside a single run
                # and was thrown away at exit. Persisting it means the 15:00
                # and 21:00 runs reuse pages fetched at 08:00.
                try:
                    from aggregator.extractors import (
                        _save_http_cache, _HTTP_RESPONSE_CACHE,
                    )
                    _save_http_cache(_HTTP_RESPONSE_CACHE)
                    logging.info(
                        f"HTTP cache persisted: {len(_HTTP_RESPONSE_CACHE)} entries"
                    )
                except Exception as _ce:
                    logging.debug(f"http cache save failed: {_ce}")
                logging.info(
                    f"Direct ATS sources: {len(_fresh)} pages read "
                    f"({_errs} errors)"
                )
        except Exception as e:
            logging.error(f"Direct ATS sources failed: {e}")

        # GitHub feeds run AFTER direct sources (direct data is authoritative)
        self._scrape_simplify_github()

        print("\nProcessing email jobs...")
        try:
            emails_data = self.email_extractor.fetch_job_emails()
            if emails_data:
                total_urls = sum(len(email["urls"]) for email in emails_data)
                print(
                    f"Processing {total_urls} URLs from {len(emails_data)} emails...\n"
                )
                self._process_emails_grouped(emails_data)
            else:
                print("No email jobs found")
                logging.warning("No email data received from Gmail")
        except Exception as e:
            print(f"Email processing error: {e}")
            logging.error(f"Email processing error: {e}", exc_info=True)

        self._ensure_mutual_exclusion()

        # Save Brain once after all job_id registrations
        try:
            from outreach.brain import Brain
            Brain.get().save()
        except Exception as _sw:
            from aggregator.swallowed import swallow as _s; _s('brain.job_id_registry_save', _sw)

        rows = self.sheets.get_next_row_numbers()

        # ── WAL: wrap sheet writes in transactions for crash safety ──
        _wal = None
        _tx_valid = None
        _tx_discarded = None
        try:
            from aggregator.wal import WriteAheadLog
            _wal = WriteAheadLog()
            # Replay any pending transactions from previous crashed runs
            _pending = _wal.get_pending()
            if _pending:
                logging.info(f"WAL: {len(_pending)} pending transactions from previous run")
                _wal.replay_pending()
        except Exception as _wal_e:
            logging.debug(f"WAL init: {_wal_e}")

        # Write valid jobs with WAL protection
        try:
            if _wal and self.valid_jobs:
                _tx_valid = _wal.begin("add_valid_jobs", {
                    "count": len(self.valid_jobs),
                    "start_row": rows["valid"],
                })
        except Exception:
            pass

        added_valid = self.sheets.add_valid_jobs(
            self.valid_jobs, rows["valid"], rows["valid_sr_no"]
        )

        try:
            if _wal and _tx_valid:
                _wal.commit(_tx_valid)
        except Exception:
            pass

        # Write discarded jobs with WAL protection
        try:
            if _wal and self.discarded_jobs:
                _tx_discarded = _wal.begin("add_discarded_jobs", {
                    "count": len(self.discarded_jobs),
                    "start_row": rows["discarded"],
                })
        except Exception:
            pass

        added_discarded = self.sheets.add_discarded_jobs(
            self.discarded_jobs, rows["discarded"], rows["discarded_sr_no"]
        )

        try:
            if _wal and _tx_discarded:
                _wal.commit(_tx_discarded)
        except Exception:
            pass

        # ── Analytics: record every processed job in real-time ──
        try:
            from analytics.store import AnalyticsStore
            from analytics.models import JobRecord
            from datetime import datetime
            _astore = AnalyticsStore()
            _run_id = datetime.now().strftime("run_%Y%m%d_%H%M%S")
            _analytics_jobs = []

            for j in self.valid_jobs:
                _analytics_jobs.append(JobRecord(
                    url=j.get("url", ""),
                    company=j.get("company", "Unknown"),
                    title=j.get("title", "Unknown"),
                    location=j.get("location", "Unknown"),
                    source=j.get("source", "Unknown"),
                    outcome="valid",
                    resume_type=j.get("resume_type", "SDE"),
                    job_type=j.get("job_type", "Internship"),
                    job_id=j.get("job_id", "N/A"),
                    remote=j.get("remote", "Unknown"),
                    sponsorship=j.get("sponsorship", "Unknown"),
                    entry_date=j.get("entry_date", ""),
                ))

            for d in self.discarded_jobs:
                _analytics_jobs.append(JobRecord(
                    url=d.get("url", ""),
                    company=d.get("company", "Unknown"),
                    title=d.get("title", "Unknown"),
                    location=d.get("location", "Unknown"),
                    source=d.get("source", "Unknown"),
                    outcome="discarded",
                    rejection_reason=d.get("reason", ""),
                    job_type=d.get("job_type", "Internship"),
                    job_id=d.get("job_id", "N/A"),
                    entry_date=d.get("entry_date", ""),
                ))

            if _analytics_jobs:
                _astore.record_jobs_batch(_analytics_jobs, run_id=_run_id)
                logging.info(f"Analytics: recorded {len(_analytics_jobs)} jobs (run={_run_id})")
            _astore.close()
        except Exception as _a_e:
            logging.debug(f"Analytics recording skipped: {_a_e}")

        # ── WAL cleanup: remove old committed transactions ──
        try:
            if _wal:
                _wal.cleanup_committed(max_age_days=7)
        except Exception:
            pass

        # Swallowed-exception summary. Each guarded site records a count;
        # one swallow in a run is noise, 25+ means a feature stopped working.
        # This is how a dead learning loop becomes visible instead of silent.
        try:
            from aggregator.swallowed import report as _sw_report
            _sw_report(verbose=True)
        except Exception:
            pass

        self._print_summary()
        elapsed = time.time() - start_time
        print(f"\n✓ DONE: {added_valid} valid, {added_discarded} discarded")
        print(f"Execution time: {elapsed / 60:.1f} minutes")
        print("=" * 80 + "\n")

        logging.info(f"SUMMARY: {added_valid} valid, {added_discarded} discarded")
        self._log_run_to_db(added_valid, added_discarded, elapsed)

        # ── Cumulative metrics tracking ──
        try:
            from aggregator.metrics import PipelineMetrics
            _metrics = PipelineMetrics()
            _url_corrections = sum(
                1 for j in self.valid_jobs if j.get("_company_source") == "url_validator"
            )
            _metrics.record_run(
                valid=added_valid,
                discarded=added_discarded,
                url_corrections=_url_corrections,
                sources_active=len(self.source_stats),
                time_sec=int(elapsed),
            )
            print(f"  📊 {_metrics.summary()}")
        except Exception as _me:
            logging.warning(f"Metrics recording failed: {_me}")

    def _log_run_to_db(self, valid, discarded, elapsed_seconds):
        """Append this run's stats to .local/run_history.db for trend analysis."""
        try:
            db_path = os.path.join(".local", "run_history.db")
            os.makedirs(".local", exist_ok=True)
            # timeout: wait for a busy DB instead of raising immediately.
            # WAL: readers (nightly_digest) no longer block this writer.
            con = sqlite3.connect(db_path, timeout=30)
            con.execute("PRAGMA journal_mode=WAL")
            cur = con.cursor()
            cur.execute("""
                CREATE TABLE IF NOT EXISTS runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts TEXT NOT NULL,
                    valid INTEGER,
                    discarded INTEGER,
                    duplicate_url INTEGER,
                    duplicate_job INTEGER,
                    skipped_old INTEGER,
                    skipped_non_tech INTEGER,
                    skipped_international INTEGER,
                    skipped_clearance INTEGER,
                    skipped_blacklisted INTEGER,
                    failed_http INTEGER,
                    elapsed_seconds REAL
                )
            """)
            cur.execute("""
                INSERT INTO runs (
                    ts, valid, discarded, duplicate_url, duplicate_job,
                    skipped_old, skipped_non_tech, skipped_international,
                    skipped_clearance, skipped_blacklisted, failed_http, elapsed_seconds
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                datetime.datetime.now().isoformat(),
                valid,
                discarded,
                self.outcomes.get("skipped_duplicate_url", 0),
                self.outcomes.get("skipped_duplicate_company_title", 0),
                self.outcomes.get("skipped_too_old", 0),
                self.outcomes.get("skipped_non_tech", 0),
                self.outcomes.get("skipped_international", 0),
                self.outcomes.get("skipped_page_restriction", 0),
                self.outcomes.get("skipped_blacklisted", 0),
                self.outcomes.get("failed_http", 0),
                elapsed_seconds,
            ))
            con.commit()
            con.close()
            logging.info(f"Run logged to {db_path}")
            try:
                from outreach.brain import Brain
                b = Brain.get()
                for source_name, stats in self.source_stats.items():
                    fetched = stats.get("valid",0)+stats.get("rejected",0)+stats.get("failed",0)
                    b.record_source_run(source_name, fetched, stats.get("valid", 0))
                new_bl = b.new_blacklisted_companies()
                if new_bl:
                    logging.info(f"Brain: {len(new_bl)} companies ready for config sync")
            except Exception as _be:
                logging.debug(f"Brain run update failed: {_be}")
        except Exception as e:
            logging.debug(f"Run log failed (non-fatal): {e}")

    def _check_selenium_health(self):
        """Selenium health check with auto-repair and Brain tracking."""
        from outreach.brain import Brain
        b = Brain.get()
        try:
            from aggregator.extractors import PageFetcher as _PF
            _pf = _PF()
            resp, _, _ = _pf.fetch_page("https://www.google.com", force_requests=True)
            if resp:
                logging.info("Selenium health check: OK (requests mode)")
                b.record_selenium_ok()
                return
        except Exception:
            pass
        try:
            from selenium import webdriver
            from selenium.webdriver.chrome.options import Options
            opts = Options()
            opts.add_argument("--headless")
            opts.add_argument("--no-sandbox")
            opts.add_argument("--disable-dev-shm-usage")
            driver = webdriver.Chrome(options=opts)
            driver.get("about:blank")
            try:
                ver = driver.capabilities.get("chrome", {}).get("chromedriverVersion", "").split(" ")[0]
                b.record_selenium_ok(ver)
            except Exception:
                b.record_selenium_ok()
            driver.quit()
            logging.info("Selenium health check: OK")
            return
        except Exception as e:
            fail_count = b.record_selenium_failure(str(e))
            logging.warning(f"Selenium health check FAILED (attempt {fail_count}): {e}")
            repaired = False
            # Method 1: webdriver-manager auto-download
            try:
                from selenium.webdriver.chrome.service import Service as _Svc
                from webdriver_manager.chrome import ChromeDriverManager as _CDM
                from selenium import webdriver as _wd
                from selenium.webdriver.chrome.options import Options as _Opts
                _opts = _Opts()
                _opts.add_argument("--headless")
                _opts.add_argument("--no-sandbox")
                _opts.add_argument("--disable-dev-shm-usage")
                _driver = _wd.Chrome(service=_Svc(_CDM().install()), options=_opts)
                _driver.get("about:blank")
                _driver.quit()
                b.record_selenium_repair("webdriver_manager", True)
                b.record_selenium_ok()
                logging.info("Selenium repaired via webdriver-manager")
                print("  ✓ Selenium auto-repaired via webdriver-manager")
                repaired = True
            except Exception as _wde:
                b.record_selenium_repair("webdriver_manager", False)
                logging.debug(f"webdriver-manager repair failed: {_wde}")
            # Method 2: brew upgrade chromedriver
            if not repaired:
                try:
                    import subprocess, shutil
                    brew = shutil.which("brew") or "/opt/homebrew/bin/brew"
                    result = subprocess.run([brew, "upgrade", "chromedriver"],
                        capture_output=True, text=True, timeout=120)
                    if result.returncode == 0:
                        from selenium import webdriver as _wd2
                        from selenium.webdriver.chrome.options import Options as _O2
                        _o2 = _O2()
                        _o2.add_argument("--headless")
                        _o2.add_argument("--no-sandbox")
                        _d2 = _wd2.Chrome(options=_o2)
                        _d2.get("about:blank")
                        _d2.quit()
                        b.record_selenium_repair("brew_upgrade", True)
                        b.record_selenium_ok()
                        logging.info("Selenium repaired via brew upgrade chromedriver")
                        print("  ✓ Selenium repaired via brew upgrade chromedriver")
                        repaired = True
                    else:
                        b.record_selenium_repair("brew_upgrade", False)
                except Exception as _bre:
                    b.record_selenium_repair("brew_upgrade", False)
                    logging.debug(f"brew repair failed: {_bre}")
            if not repaired:
                if fail_count >= 3:
                    b.send_email_alert(
                        "🔧 Selenium broken — Workday/Ashby jobs failing",
                        f"ChromeDriver failed {fail_count} times.\nError: {e}\n\n"
                        f"Auto-repair failed. Manual fix:\n"
                        f"  chromedriver --version\n"
                        f"  google-chrome --version\n"
                        f"  brew install --cask chromedriver"
                    )
                print(f"\n{'='*60}\n  WARNING: Selenium auto-repair failed (attempt {fail_count})\n"
                      f"  Error: {e}\n  Workday/Ashby/Oracle jobs will fail.\n{'='*60}")

    def _scrape_simplify_github(self):
        import concurrent.futures

        _all_sources = [
            (SPEEDYAPPLY_SWE_URL, "speedyapply_swe"),
            (SPEEDYAPPLY_AI_URL, "speedyapply_ai"),
            (ZAPPLYJOBS_URL, "zapplyjobs_newgrad"),
            (SIMPLIFY_OFFSEASON_URL, "simplify_offseason"),

            (VANSHB03_OFFSEASON_URL, "vanshb03_offseason"),
            (NEWGRAD_SIMPLIFY_URL, "simplify_newgrad"),
            (NEWGRAD_CVRVE_URL, "cvrve_newgrad"),
            # Full-time new-grad lists — 1,125 roles never fetched before
            (SPEEDYAPPLY_SWE_NEWGRAD_URL, "speedyapply_swe_newgrad"),
            (SPEEDYAPPLY_AI_NEWGRAD_URL, "speedyapply_ai_newgrad"),
            (ZAPPLYJOBS_IT_URL, "zapplyjobs_it"),
            (ZAPPLYJOBS_ML_INTERN_URL, "zapplyjobs_ml_intern"),
            (ZAPPLYJOBS_INTERNSHIPS_2027_URL, "zapplyjobs_intern_2027"),
        ]

        _results = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as ex:
            _futures = {
                ex.submit(self._safe_scrape, url, name): name
                for url, name in _all_sources
            }
            for fut in concurrent.futures.as_completed(_futures):
                name = _futures[fut]
                _results[name] = fut.result()

        simplify_jobs = _results.get("SimplifyJobs", [])
        vanshb03_jobs = _results.get("vanshb03", [])
        speedyapply_jobs = _results.get("speedyapply_swe", [])

        self._github_mode = True

        _new_total = sum(len(v) for k, v in _results.items()
                         if k not in ("SimplifyJobs", "vanshb03", "speedyapply_swe"))
        logging.info(
            f"GitHub: {len(simplify_jobs)} SimplifyJobs + {len(vanshb03_jobs)} vanshb03"
            f" + {len(speedyapply_jobs)} speedyapply + {_new_total} new sources"
        )

        import concurrent.futures

        def _process_github_batch(jobs, source_name):
            fresh, skipped_old = [], 0
            for job in jobs:
                # BLACKLIST: companies you ruled out, and clearance shops.
                # GATE -2 in _process_single_job_comprehensive covered the
                # direct-ATS and email paths only, so GitHub feeds bypassed
                # every company-level block entirely - MORSE Corp came back
                # through speedyapply_swe hours after being blacklisted.
                # Sixth time today a check existed in one path and not the
                # other.
                try:
                    from aggregator.apply_learned import (
                        is_user_blacklisted, is_learned_clearance,
                        is_user_blacklisted_title)
                    _bc = job.get("company", "")
                    _bt = is_user_blacklisted_title(job.get("title", ""))
                    if is_user_blacklisted(_bc) or is_learned_clearance(_bc) or _bt:
                        self.outcomes["skipped_user_blacklist"] = (
                            self.outcomes.get("skipped_user_blacklist", 0) + 1)
                        logging.info(
                            f"REJECTED | {_bc} | {job.get('title','')} | Blacklisted")
                        continue
                except Exception as _ble:
                    logging.debug(f"blacklist check error (keeping job): {_ble}")

                # TERM FILTER: drop ONLY unambiguous Summer 2027 internships.
                # Full-time, Fall, Spring, Winter and anything ambiguous are
                # kept. An exception here keeps the job — never drops it.
                try:
                    from aggregator.term_filter import should_drop_summer
                    if should_drop_summer(
                        job.get("title", ""),
                        job_type=self._detect_job_type(job.get("title", ""), source_name),
                        company=job.get("company", ""),
                        source=source_name,
                    ):
                        self.outcomes["skipped_summer_2027"] = (
                            self.outcomes.get("skipped_summer_2027", 0) + 1
                        )
                        continue
                except Exception as _tfe:
                    logging.debug(f"term filter error (keeping job): {_tfe}")

                age_days = self._parse_github_age(job["age"])
                # DROP stale AND undated rows. Previously `is not None` let every
                # unparseable/blank date slip through, which is why weeks-old
                # postings were being accepted.
                if age_days is None or age_days > MAX_JOB_AGE_DAYS:
                    skipped_old += 1
                else:
                    job["_source_name"] = source_name
                    fresh.append(job)
            print(f"  {source_name}: {len(fresh)} fresh, {skipped_old} too old")
            errors = 0
            with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
                futures = {pool.submit(self._process_single_github_job, job): job for job in fresh}
                for fut in concurrent.futures.as_completed(futures):
                    try:
                        fut.result()
                    except Exception as e:
                        errors += 1
                        job = futures[fut]
                        logging.error(f"Failed {job.get('company','?')}: {e}", exc_info=True)
            print(f"  {source_name}: done ({errors} errors)")

        print(f"\n  Processing Simplify repository...")
        _process_github_batch(simplify_jobs, "SimplifyJobs")
        print(f"\n  Processing Vanshb repository...")
        _process_github_batch(vanshb03_jobs, "vanshb03")
        print(f"\n  Processing SpeedyApply SWE repository...")
        _process_github_batch(speedyapply_jobs, "speedyapply_swe")

        # ── New sources (fault-isolated: each source independent) ──
        # These 4 were being FETCHED every run and then silently dropped —
        # they never appeared in this processing list. simplify_offseason
        # alone is ~644 roles.
        for _src_name in ["speedyapply_ai", "vanshb03_offseason", "simplify_newgrad",
                          "cvrve_newgrad", "zapplyjobs_newgrad", "simplify_offseason",
                          "SimplifyJobs_2026", "zapplyjobs_2026",
                          "speedyapply_swe_newgrad", "speedyapply_ai_newgrad",
                          "vanshb03_newgrad", "zapplyjobs_it",
                          "zapplyjobs_ml_intern", "zapplyjobs_intern_2027"]:
            _src_jobs = _results.get(_src_name, [])
            if _src_jobs:
                print(f"\n  Processing {_src_name} ({len(_src_jobs)} listings)...")
                _process_github_batch(_src_jobs, _src_name)

        self._github_mode = False

        github_valid = sum(
            1 for j in self.valid_jobs if j["source"] in ["SimplifyJobs", "vanshb03", "speedyapply_swe"]
        )
        print(f"\n  GitHub: {github_valid} valid jobs total")
        logging.info(f"GitHub summary: {github_valid} valid jobs")

    def _process_single_github_job(self, job):
        title = TitleProcessor.clean_title_aggressive(job["title"])
        url = job["url"]
        source = job.get("source", "GitHub")
        company_from_github = job.get("company", "Unknown")
        location_from_github = job.get("location", "Unknown")

        # Capture TRUE originals before any modification for hint preservation
        _true_original_company = company_from_github
        _true_original_title = job.get("title", "").strip()
        _true_original_location = location_from_github

        # Normalize company name
        company_from_github_lower = company_from_github.lower().strip()
        if company_from_github_lower in COMPANY_NAME_FIXES:
            fixed = COMPANY_NAME_FIXES[company_from_github_lower]
            if fixed != "Unknown":
                logging.info(f"GitHub company normalized: '{company_from_github}' → '{fixed}'")
                company_from_github = fixed
            elif company_from_github_lower in GARBAGE_COMPANY_NAMES:
                # Try ATS path extraction before giving up
                _ATS_SLUGS = {"greenhouse", "lever", "workable", "ashbyhq", "rippling",
                              "smartrecruiters", "icims", "myworkdayjobs", "successfactors",
                              "bamboohr", "jobvite", "applytojob", "oraclecloud", "stream",
                              "telecom", "church", "atp", "hpiq", "beone", "bmwgroup"}
                if company_from_github_lower in _ATS_SLUGS and url:
                    try:
                        from urllib.parse import urlparse as _up, unquote as _uq
                        _path = _uq(_up(url).path)
                        _parts = [p for p in _path.split("/") if p and len(p) > 2
                                 and p.lower() not in ("jobs", "job", "careers", "apply",
                                 "en", "external", "internal", "hcmui", "sites",
                                 "candidateexperience", "recruiting", "search")]
                        if _parts:
                            _real = _parts[0].replace("-", " ").replace("_", " ").title()
                            if len(_real) > 2:
                                logging.info(f"ATS fix: '{company_from_github}' → '{_real}'")
                                company_from_github = _real
                                company_from_github_lower = _real.lower()
                                # Don't return — continue processing with fixed name
                            else:
                                return
                        else:
                            return
                    except Exception:
                        return
                else:
                    return  # Skip garbage companies with no fix

        if job.get("is_closed", False):
            return

        is_valid_title, reason = TitleProcessor.is_valid_job_title(title)
        if not is_valid_title:
            # Try extracting title from URL slug before rejecting
            url_title = None
            try:
                from urllib.parse import urlparse, unquote
                path = unquote(urlparse(url).path)
                # Get last meaningful path segment
                segments = [s for s in path.split("/") if s and len(s) > 5]
                if segments:
                    slug = segments[-1]
                    # Remove IDs, hashes, query fragments
                    slug = re.sub(r"^[a-f0-9-]{8,}[-]?", "", slug)
                    slug = re.sub(r"[-_]", " ", slug).strip()
                    if len(slug) > 10:
                        url_title = TitleProcessor.clean_title_aggressive(slug)
                        valid2, _ = TitleProcessor.is_valid_job_title(url_title)
                        if valid2:
                            title = url_title
                            is_valid_title = True
            except Exception as _sw:
                from aggregator.swallowed import swallow as _s; _s('title.url_slug_fallback', _sw)
            if not is_valid_title:
                self.outcomes["skipped_invalid_title"] += 1
                self.source_stats[source]["rejected"] += 1
                self._print_rejected(company_from_github, f"Invalid title: {reason}")
                logging.info(
                    f"REJECTED | {company_from_github} | {title} | Invalid title: {reason}"
                )
                return

        resolved_url = url
        if "simplify.jobs" in url.lower():
            resolved_url, resolved = SimplifyRedirectResolver.resolve(url)
            if resolved_url == "__INACTIVE__":
                self.outcomes["skipped_inactive"] = self.outcomes.get("skipped_inactive", 0) + 1
                self._add_discarded(
                    company_from_github, title, location_from_github, "Unknown",
                    url, "N/A", "Internship", source, "Simplify listing inactive/closed",
                )
                logging.info(f"REJECTED | {company_from_github} | {title} | Simplify INACTIVE")
                return
            if not resolved:
                # Don't write Simplify wrapper URL — queue for retry and skip
                try:
                    from outreach.brain import Brain
                    _jid_m = __import__('re').search(r'/p/([a-f0-9-]+)', url)
                    if _jid_m:
                        Brain.get().queue_simplify_retry(_jid_m.group(1), url, "github_unresolved")
                except Exception:
                    pass
                self.outcomes["failed_simplify_resolution"] = self.outcomes.get("failed_simplify_resolution", 0) + 1
                logging.info(f"Simplify unresolved — queued for retry: {url[:60]}")
                return
                # Fallback: extract title from URL slug if current title is Unknown/generic
                import re as _url_re
                slug_match = _url_re.search(r'/p/[a-f0-9-]+/([A-Za-z0-9-]+)', url)
                if slug_match and (not title or title == 'Unknown' or len(title) < 5):
                    slug = slug_match.group(1).replace('?utm_source=swelist', '').replace('?utm_source=', '')
                    slug_title = slug.replace('-', ' ').strip()
                    if len(slug_title) >= 5:
                        slug_title = TitleProcessor.clean_title_aggressive(slug_title)
                        if slug_title and len(slug_title) >= 5:
                            title = slug_title
                            logging.info(f'Title from URL slug: {title}')
                # Use metadata from Simplify page if available
                try:
                    from aggregator.extractors import SimplifyRedirectResolver as _SRR
                    smeta = _SRR._last_metadata
                    if smeta.get("location"):
                        logging.info(f"Simplify metadata location available: {smeta['location']}")
                    if smeta.get("remote"):
                        # Store remote status for later use
                        _simplify_remote = smeta['remote']
                        logging.info(f"Using Simplify metadata remote: {smeta['remote']}")
                    if smeta.get("no_h1b"):
                        logging.info(f"Simplify: No H1B sponsorship for this role")
                    if smeta.get("no_h1b"):
                        logging.info(f"Simplify metadata: No H1B sponsorship detected")
                        # Reject immediately — no sponsorship confirmed by Simplify
                        self.outcomes["skipped_page_restriction"] = self.outcomes.get("skipped_page_restriction", 0) + 1
                        self._add_discarded(
                            company_from_github, title, location_from_github, "Unknown",
                            resolved_url, "N/A", "Internship", source,
                            "No H1B sponsorship (Simplify metadata)"
                        )
                        self._print_rejected(company_from_github, "No H1B sponsorship (Simplify)")
                        logging.info(f"REJECTED | {company_from_github} | {title} | No H1B (Simplify metadata)")
                        return
                except Exception as _e:
                    logging.error(f"Simplify H1B check failed: {_e}")

        # Detect Simplify URL-company mismatches (e.g. Ingram Micro URL for Bose job)
        # If the URL domain clearly belongs to a different known company, reject
        try:
            from urllib.parse import urlparse as _urlp
            _url_domain = _urlp(resolved_url).netloc.lower().replace("www.", "")
            _company_norm = re.sub(r"[^a-z0-9]", "", company_from_github.lower())
            # Extract company slug from domain (e.g. "ingrammicro" from "ingrammicro.wd5.myworkdayjobs.com")
            _domain_slug = _url_domain.split(".")[0].lower()
            # Special case: ashbyhq.com/company-name/job → extract company from path
            if "ashbyhq" in _url_domain or "jobs.ashbyhq" in _url_domain:
                try:
                    from urllib.parse import urlparse as _up2
                    _path_parts = [p for p in _up2(resolved_url).path.split("/") if p]
                    if _path_parts:
                        _ashby_company = re.sub(r"[^a-z0-9]", "", _path_parts[0].lower())
                        if _ashby_company and _ashby_company not in _company_norm and len(_ashby_company) > 3:
                            logging.info(f"Ashby URL company: {_path_parts[0]} vs hint: {company_from_github}")
                            if _ashby_company not in _company_norm and _company_norm not in _ashby_company:
                                logging.info(f"Ashby URL-company mismatch (logged only): {_path_parts[0]} vs {company_from_github}")
                except Exception:
                    pass
            _domain_slug = re.sub(r"[^a-z0-9]", "", _domain_slug)
            # Only flag mismatch if domain slug is a known company AND clearly != hint company

            # Was a 41-entry dict rebuilt on every call, sharing only
            # 3 slugs with url_validator's copy and missing 'disney'.
            # Now derived from brain.json - self growing, one source.
            from aggregator.workday_map import company_for_slug as _WD_DERIVED
            class _WDProxy(dict):
                def __contains__(self, k): return _WD_DERIVED(k) is not None
                def __getitem__(self, k):
                    v = _WD_DERIVED(k)
                    if v is None: raise KeyError(k)
                    return v
                def get(self, k, d=None):
                    v = _WD_DERIVED(k)
                    return d if v is None else v
            _known_workday_companies = _WDProxy()
            if _domain_slug in _known_workday_companies:
                _expected_name = _known_workday_companies[_domain_slug]
                if _expected_name:
                    _expected = re.sub(r"[^a-z0-9]", "", _expected_name)
                    if _expected not in _company_norm and _company_norm not in _expected:
                        logging.info(f"URL-COMPANY MISMATCH: '{company_from_github}' → '{_expected_name}' (from URL domain: {_domain_slug})")
                        company_from_github = _expected_name.title()

            # General Workday mismatch: extract company from subdomain
            elif "myworkdayjobs.com" in _url_domain:
                _wd_slug = _url_domain.split(".")[0].lower()
                _wd_norm = re.sub(r"[^a-z0-9]", "", _wd_slug)
                if len(_wd_norm) > 3 and _wd_norm not in _company_norm and _company_norm not in _wd_norm:
                    # URL domain company doesn't match GitHub company — use URL mapping
                    _url_result = CompanyExtractor.extract_from_url_mapping(resolved_url)
                    if _url_result and _url_result.value:
                        logging.info(f"URL-COMPANY FIX: '{company_from_github}' → '{_url_result.value}' (from URL mapping)")
                        company_from_github = _url_result.value
                    else:
                        logging.info(f"URL-COMPANY MISMATCH: '{company_from_github}' vs domain '{_wd_slug}' (no mapping, using URL domain)")
                        company_from_github = _wd_slug.replace("-", " ").replace("_", " ").title()

            # Greenhouse/Lever/Ashby/Workable: extract from URL path
            elif any(ats in _url_domain for ats in ["greenhouse.io", "lever.co", "ashbyhq.com", "workable.com"]):
                _url_result = CompanyExtractor.extract_from_url_mapping(resolved_url)
                if _url_result and _url_result.value:
                    _url_co_norm = re.sub(r"[^a-z0-9]", "", _url_result.value.lower())
                    if _url_co_norm not in _company_norm and _company_norm not in _url_co_norm:
                        logging.info(f"URL-COMPANY FIX: '{company_from_github}' → '{_url_result.value}' (from ATS URL)")
                        company_from_github = _url_result.value
        except Exception:
            pass

        # GitHub sources: undo URL-domain override — source pairing is correct
        # DERIVED, not hardcoded. This list was written when there were 8
        # sources and never grew: simplify_offseason, zapplyjobs_*,
        # speedyapply_*_newgrad and indeed_direct were all missing, so their
        # rows fell through to the URL-shift logic below, lost their real
        # Greenhouse/Workday URLs to a Google search link, and then skipped
        # every page-based check - citizenship, clearance, sponsorship.
        # 196 sheet rows were unvalidated because of it.
        #
        # A feed-derived source is any source that is not a live page fetch,
        # so trust the company name the feed gave us. Same bug class as
        # COMPANY_NAME_FIXES and GARBAGE_COMPANY_NAMES: a hardcoded list that
        # did not grow with the system.
        _LIVE_PAGE_SOURCES = {"LinkedIn", "Email", "Manual", "ZipRecruiter",
                              "Jobright", "SWE List", "SWE List Email"}
        _GITHUB_SOURCES = set(getattr(self, "_all_feed_sources", ()) or ()) | {
            "SimplifyJobs", "vanshb03", "speedyapply_swe", "direct_ats",
            "speedyapply_ai", "vanshb03_offseason", "simplify_newgrad",
            "cvrve_newgrad", "simplify_offseason", "SimplifyJobs_2026",
            "zapplyjobs_newgrad", "zapplyjobs_2026", "zapplyjobs_it",
            "zapplyjobs_ml_intern", "zapplyjobs_intern_2027",
            "speedyapply_swe_newgrad", "speedyapply_ai_newgrad",
            "indeed_direct", "greenhouse_direct", "ashby_direct",
            "lever_direct", "workday_direct", "smartrecruiters_direct",
            "workable_direct", "rippling_direct"}
        if source and source not in _LIVE_PAGE_SOURCES:
            _GITHUB_SOURCES.add(source)
        if source in _GITHUB_SOURCES:
            company_from_github = _true_original_company

        # ── Advanced degree filter (🎓 emoji from GitHub source) ──
        if '🎓' in title or '🎓' in company_from_github:
            logging.info(f"REJECT: Advanced degree role: {company_from_github} | {title}")
            return

        # ── MBA-only filter ──
        import re as _re
        if _re.search(r'\bmba\b', title, _re.I) and not _re.search(r'(?:software|swe|engineer|data|ml|ai)', title, _re.I):
            logging.info(f"REJECT: MBA-only role: {company_from_github} | {title}")
            return

        # ── URL-Company Validator (self-healing) ──
        # _true_original captured at top of function before any modifications

        _vj = validate_job({"company": company_from_github, "title": title, "url": resolved_url})
        if source not in _GITHUB_SOURCES:
            company_from_github = _vj["company"]
            title = _vj.get("title", title)
        _ok, _why = validate_job_integrity(_vj)
        if not _ok:
            logging.info(f"INTEGRITY FAIL: {company_from_github} | {_why}")
            return

        # If mismatch detected, the URL company is different from the hint company
        # The hint data (original company/title) is a REAL job — queue it separately
        if source not in _GITHUB_SOURCES and _true_original_company.lower().strip() != company_from_github.lower().strip():
            _hint_norm = re.sub(r"[^a-z0-9]", "", _true_original_company.lower())
            _url_norm = re.sub(r"[^a-z0-9]", "", company_from_github.lower())
            if (_hint_norm != _url_norm and len(_true_original_company) > 1
                    and _true_original_company not in ("Unknown", "unknown", "N/A", "")):
                # Save the original hint as a separate entry (URL unknown due to shift)
                _hint_job = {
                    "company": _true_original_company,
                    "title": _true_original_title,
                    "location": _true_original_location,
                    "remote": "Unknown",
                    "url": "URL_SHIFTED",
                    "job_id": "N/A",
                    "job_type": self._detect_job_type(_true_original_title, job.get("_source_name", "")),
                    "sponsorship": _h1b_sponsorship(job.get("company", ""), _feed_sponsorship(job)),
                    "entry_date": self._format_date(),
                    "source": source,
                    "_hint_preserved": True,
                }
                # Only add if not already in existing jobs
                _hint_key = re.sub(r"[^a-z0-9]", "", f"{_true_original_company}_{_true_original_title}".lower())
                if _hint_key not in self.existing_jobs:
                    # Filter hints: reject international, non-tech, garbage titles
                    _hint_valid = True
                    # International check
                    _hint_loc = _true_original_location or ""
                    _intl_keywords = ["uk", "canada", "india", "germany", "france", "japan",
                                      "australia", "brazil", "mexico", "china", "singapore",
                                      "ireland", "netherlands", "sweden", "denmark", "norway",
                                      "switzerland", "israel", "korea", "taiwan", "hong kong"]
                    if any(kw in _hint_loc.lower() for kw in _intl_keywords):
                        _hint_valid = False
                        logging.info(f"HINT REJECTED (international): {_true_original_company} | {_hint_loc}")
                    # Non-tech check
                    if _hint_valid:
                        _is_tech = TitleProcessor.is_cs_engineering_role(_true_original_title)
                        if not _is_tech:
                            _hint_valid = False
                            logging.info(f"HINT REJECTED (non-tech): {_true_original_company} | {_true_original_title}")
                    # Garbage title check
                    if _hint_valid:
                        _title_ok, _title_reason = TitleProcessor.is_valid_job_title(_true_original_title)
                        if not _title_ok:
                            _hint_valid = False
                            logging.info(f"HINT REJECTED (invalid title): {_true_original_company} | {_true_original_title}")
                    # XMLNAME / garbage check
                    if _hint_valid and "xmlname" in _true_original_title.lower():
                        _hint_valid = False
                        logging.info(f"HINT REJECTED (XMLNAME garbage): {_true_original_company} | {_true_original_title}")
                    if _hint_valid:
                        with self._github_lock:
                            self.valid_jobs.append(_hint_job)
                            self.existing_jobs.add(_hint_key)
                        logging.info(f"HINT PRESERVED: {_true_original_company} | {_true_original_title} (URL shifted, original data saved)")

        if self._is_duplicate(company_from_github, title, resolved_url):
            return
        # Thread safety ensured via _github_lock for all shared state below

        # Skip internship check for new-grad sources
        _src = job.get("_source_name", "")
        _is_newgrad_source = "newgrad" in _src or "new_grad" in _src
        # Source-name matching missed every direct-ATS source and Indeed.
        # Decide by JOB TYPE: a full-time role must never be rejected for
        # failing an internship check. 1,594 new-grad roles per run were
        # being discarded as "senior role" because of this.
        _jt = self._detect_job_type(title, _src)
        if _jt and _jt.strip().lower() not in ("internship", "intern", "co-op", "coop"):
            # Full-time is fine, but only at entry level.
            if _is_senior_title(title):
                self.outcomes["skipped_senior_role"] += 1
                self.source_stats[source]["rejected"] += 1
                logging.info(f"REJECTED | {company_from_github} | {title} | Senior role")
                return
            _is_newgrad_source = True

        is_internship, intern_reason = TitleProcessor.is_internship_role(title, github_category="Software Engineering Internship")
        if not is_internship and not _is_newgrad_source:
            self.outcomes["skipped_senior_role"] += 1
            self.source_stats[source]["rejected"] += 1
            self._print_rejected(company_from_github, intern_reason)
            logging.info(
                f"REJECTED | {company_from_github} | {title} | {intern_reason}"
            )
            return

        season_ok, season_reason = TitleProcessor.check_season_requirement(title)
        if not season_ok:
            self.outcomes["skipped_wrong_season"] += 1
            self.source_stats[source]["rejected"] += 1
            self._print_rejected(company_from_github, season_reason)
            logging.info(
                f"REJECTED | {company_from_github} | {title} | {season_reason}"
            )
            return

        github_category = job.get("github_category", "")
        is_tech = TitleProcessor.is_cs_engineering_role(title)
        if not is_tech and github_category:
            logging.info(f"OVERRIDE | {company_from_github} | {title} | GitHub category: {github_category}")
            is_tech = True
        if not is_tech:
            self.outcomes["skipped_non_tech"] += 1
            self.source_stats[source]["rejected"] += 1
            self._print_rejected(company_from_github, "Not CS/Engineering")
            logging.info(
                f"REJECTED | {company_from_github} | {title} | Not a CS/Engineering role"
            )
            return

        company_lower = company_from_github.lower().strip()
        if any(bl.lower() == company_lower for bl in COMPANY_BLACKLIST):
            reason = COMPANY_BLACKLIST_REASONS.get(
                company_from_github, "Blacklisted company"
            )
            self.outcomes["skipped_blacklisted"] += 1
            self.source_stats[source]["rejected"] += 1
            self._add_discarded(
                company_from_github,
                title,
                location_from_github,
                "Unknown",
                resolved_url,
                "N/A",
                "Internship",
                source,
                reason,
            )
            self._print_rejected(company_from_github, f"Blacklisted")
            logging.info(
                f"REJECTED | {company_from_github} | {title} | Blacklisted: {reason}"
            )
            return

        # Sponsorship check disabled — unreliable and slow, handled by page fetch
        # sponsorship_github = _claude_sponsorship_check(company_from_github, title)

        # HQ fallback: if location still Unknown, try known company HQ
        if not location_from_github or location_from_github == "Unknown":
            try:
                from aggregator.config import COMPANY_HQ as _HQ
                _hq = _HQ.get(company_from_github.lower().strip())
                if _hq:
                    location_from_github = _hq
                    logging.info(f"HQ fallback: {company_from_github} → {_hq}")
            except Exception:
                pass

        # URL-based location extraction using Workday URL city slugs
        if not location_from_github or location_from_github == "Unknown":
            try:
                from aggregator.config import URL_CITY_STATE_MAP
                import re as _re_loc
                _loc_match = _re_loc.search(r"/job/([^/]+)/", resolved_url)
                if _loc_match:
                    _slug = _loc_match.group(1).lower()
                    # Remove country/state suffixes
                    for _sfx in ["-united-states-of-america", "-united-states", "-usa", "-us"]:
                        _slug = _slug.replace(_sfx, "")
                    # Try full slug first, then first part
                    _city_key = _slug.replace("-", " ").strip()
                    _city_key2 = _slug.split("-")[0]
                    _mapped = URL_CITY_STATE_MAP.get(_city_key) or URL_CITY_STATE_MAP.get(_city_key2) or URL_CITY_STATE_MAP.get(_slug)
                    if _mapped:
                        location_from_github = _mapped
                        logging.info(f"URL location: {company_from_github} → {_mapped}")
            except Exception:
                pass

        international_check = LocationProcessor.check_if_international(
            location_from_github, url=resolved_url, title=title
        )
        if international_check:
            self.outcomes["skipped_international"] += 1
            self.source_stats[source]["rejected"] += 1
            self._add_discarded(
                company_from_github,
                title,
                location_from_github,
                "Unknown",
                resolved_url,
                "N/A",
                "Internship",
                source,
                international_check,
            )
            short_reason = international_check.replace("Location: ", "")
            self._print_rejected(company_from_github, short_reason)
            logging.info(
                f"REJECTED | {company_from_github} | {title} | {international_check}"
            )
            return

        # ── Reject blacklisted companies (clearance, defense) before ANY processing ──
        try:
            from aggregator.config import CLEARANCE_COMPANIES
            _co_blacklist = company_from_github.lower().strip()
            if any(bc.lower() in _co_blacklist or _co_blacklist in bc.lower() for bc in CLEARANCE_COMPANIES):
                logging.info(f"EARLY BLACKLIST: {company_from_github} (clearance company)")
                return
        except (ImportError, AttributeError):
            pass

        # ── Reject blacklisted company patterns (universities, hospitals, government) ──
        try:
            from aggregator.config import COMPANY_BLACKLIST_PATTERNS
            _co_bl = company_from_github.lower().strip()
            if any(bp in _co_bl for bp in COMPANY_BLACKLIST_PATTERNS):
                logging.info(f"EARLY BLACKLIST: {company_from_github} (company pattern)")
                return
        except (ImportError, AttributeError):
            pass

        # ── Reject linkedin.com/jobs URLs — these are listings, not company pages ──
        if "linkedin.com/jobs" in resolved_url:
            logging.info(f"REJECTED | linkedin.com job listing URL (not a company page)")
            return

        # ── Early dedup: catch duplicates BEFORE expensive page fetch ──
        _early_norm_co = TitleProcessor.normalize_company_for_dedup(company_from_github) if hasattr(TitleProcessor, "normalize_company_for_dedup") else company_from_github.lower()
        _early_norm = re.sub(r"[^a-z0-9]", "", f"{_early_norm_co}_{title}".lower())
        # Also check job_id dedup — same company + same job_id = duplicate
        _job_id = job.get("job_id", "") or ""
        _job_id_key = f"{_early_norm_co}_{_job_id}" if _job_id and _job_id != "N/A" else ""
        with self._github_lock:
            if _early_norm in self.existing_jobs:
                logging.info(f"EARLY DEDUP: {company_from_github} | {title} (already in sheet)")
                return
            if not hasattr(self, "_existing_job_ids"):
                self._existing_job_ids = set()
                # Load existing job IDs from sheet
                for _ej_row in self.sheets.valid_sheet.get_all_values()[1:]:
                    if len(_ej_row) > 6 and _ej_row[6].strip() and _ej_row[6].strip() != "N/A":
                        _ej_co = re.sub(r"[^a-z0-9]", "", _ej_row[2].lower())
                        self._existing_job_ids.add(f"{_ej_co}_{_ej_row[6].strip()}")
            if _job_id_key and _job_id_key in self._existing_job_ids:
                logging.info(f"EARLY DEDUP (job_id): {company_from_github} | {title} | ID={_job_id}")
                return

        # ── Early non-English filter ──
        _NON_ENG = ["automatizare", "inteligenta artificiala", "dezvoltare", "platforme",
            "bazata", "senzori", "inginerie", "testare", "praktikum", "werkstudent",
            "alternance", "stagiaire", "ingeniero", "entwicklung", "forschung"]
        if any(kw in title.lower() for kw in _NON_ENG):
            logging.info(f"NON-ENGLISH: {company_from_github} | {title}")
            return
        
        # Pre-fetch: save source data for conflict detection after page fetch
        _pre_fetch_company = company_from_github
        _pre_fetch_title = title

        result = self._process_single_job_comprehensive(
            resolved_url,
            company_hint=company_from_github,
            title_hint=title,
            location_hint=location_from_github,
            source=source,
        )

        if result:
            alert = RoleCategorizer.get_terminal_alert(result["title"])
            company_display = result["company"][:TERMINAL_COMPANY_WIDTH]
            print(f"  {company_display}: ✓ Valid {alert}")
            with self._github_lock:
                self.source_stats[source]["valid"] += 1
            if _BRAIN:
                try:
                    _BRAIN.on_job_validated(
                        result.get("company", ""), result.get("title", ""),
                        result.get("location", ""), source,
                        result.get("sponsorship", "Unknown"))
                except Exception:
                    pass
        else:
            _fallback = self._try_trusted_fallback(company_from_github, title, resolved_url, location_from_github, source)
            if not _fallback:
                with self._github_lock:
                    self.source_stats[source]["rejected"] += 1
                logging.info(f"REJECTED (comprehensive) | {company_from_github} | {title} | url={resolved_url[:60]}")

        # Conflict detection: if page extracted a different company than source,
        # the source's job is a SEPARATE real job — preserve it
        if source in _GITHUB_SOURCES:
            _page_co_name = result["company"] if result else ""
            _src_co = re.sub(r"[^a-z0-9]", "", _true_original_company.lower())
            _page_co = re.sub(r"[^a-z0-9]", "", _page_co_name.lower()) if _page_co_name else ""
            # Conflict if: page showed different company, OR page rejected but URL domain != source company
            _has_conflict = False
            if _page_co and _src_co and _page_co != _src_co and _src_co not in _page_co and _page_co not in _src_co:
                _has_conflict = True
            elif not result:
                # Page rejected — check if URL domain suggests a different company
                try:
                    from urllib.parse import urlparse as _cf_urlp
                    _cf_domain = _cf_urlp(resolved_url).netloc.lower().split(".")[0]
                    _cf_domain_norm = re.sub(r"[^a-z0-9]", "", _cf_domain)
                    if _cf_domain_norm and _src_co and _cf_domain_norm not in _src_co and _src_co not in _cf_domain_norm:
                        _has_conflict = True
                except Exception:
                    pass
            if _has_conflict:
                _conflict_norm_co = TitleProcessor.normalize_company_for_dedup(_true_original_company) if hasattr(TitleProcessor, "normalize_company_for_dedup") else _true_original_company.lower()
                _conflict_key = re.sub(r"[^a-z0-9]", "", f"{_conflict_norm_co}_{_true_original_title}".lower())
                if _conflict_key not in self.existing_jobs:
                    # International check — use regex word boundaries to avoid matching US cities
                    # e.g. "uk" must not match "Milwaukee", ", in" must not match "Indianapolis, IN"
                    import re as _intl_re
                    _intl_patterns = [
                        r"\bcanada\b", r"\bindia\b(?!na)", r"\bgermany\b", r"\bfrance\b",
                        r"\bjapan\b", r"\baustralia\b", r"\bbrazil\b", r"\bmexico\b(?!\s*,)",
                        r"\bchina\b", r"\bsingapore\b", r"\btoronto\b", r"\bvancouver\b",
                        r"\blondon\b", r"\bberlin\b", r"\btokyo\b", r"\bbeijing\b",
                        r"\bshanghai\b", r"\bmumbai\b", r"\bbangalore\b", r"\bhyderabad\b",
                        r"\bdublin\b", r"\bsydney\b", r"\bmelbourne\b", r"\bcalgary\b",
                        r"\bmontreal\b", r"\bON,?\s*Canada\b", r"\bBC,?\s*Canada\b",
                        r"\bBucuresti\b", r"\bRomania\b", r"\bCroatia\b",
                        r",\s*UK\b", r",\s*United Kingdom\b",
                    ]
                    # US GUARD: a valid US state code/name means the job is US,
                    # even if the city name also exists abroad (Dublin OH,
                    # Toronto OH, Paris TX, Manchester NH, London KY...).
                    _US_STATES = (
                        "AL","AK","AZ","AR","CA","CO","CT","DE","FL","GA","HI","ID",
                        "IL","IN","IA","KS","KY","LA","ME","MD","MA","MI","MN","MS",
                        "MO","MT","NE","NV","NH","NJ","NM","NY","NC","ND","OH","OK",
                        "OR","PA","RI","SC","SD","TN","TX","UT","VT","VA","WA","WV",
                        "WI","WY","DC","PR",
                    )
                    _US_STATE_NAMES = (
                        "alabama","alaska","arizona","arkansas","california","colorado",
                        "connecticut","delaware","florida","georgia","hawaii","idaho",
                        "illinois","indiana","iowa","kansas","kentucky","louisiana",
                        "maine","maryland","massachusetts","michigan","minnesota",
                        "mississippi","missouri","montana","nebraska","nevada",
                        "new hampshire","new jersey","new mexico","new york",
                        "north carolina","north dakota","ohio","oklahoma","oregon",
                        "pennsylvania","rhode island","south carolina","south dakota",
                        "tennessee","texas","utah","vermont","virginia","washington",
                        "west virginia","wisconsin","wyoming",
                    )
                    _loc_raw = _true_original_location or ""
                    _is_us = bool(
                        _intl_re.search(
                            r",\s*(?:" + "|".join(_US_STATES) + r")\b",
                            _loc_raw,
                        )
                    ) or any(
                        _intl_re.search(r"\b" + _n + r"\b", _loc_raw, _intl_re.I)
                        for _n in _US_STATE_NAMES
                    )
                    # Canadian provinces must still count as international
                    if _intl_re.search(r",\s*(?:ON|BC|AB|QC|MB|SK|NS|NB|NL|PE)\b", _loc_raw):
                        _is_us = False
                    # A known FOREIGN CITY overrides an ambiguous 2-letter code
                    # ("Munich, DE" is Germany not Delaware; "Hyderabad, IN"
                    #  is India not Indiana; "Berlin, DE" is Germany).
                    _FOREIGN_CITIES = (
                        "munich","berlin","hamburg","frankfurt","hyderabad","bangalore",
                        "bengaluru","mumbai","chennai","pune","delhi","noida","gurgaon",
                        "kolkata","tokyo","osaka","beijing","shanghai","shenzhen",
                        "seoul","taipei","sydney","melbourne","auckland","dublin ireland",
                        "amsterdam","madrid","barcelona","milan","rome","zurich","geneva",
                        "stockholm","oslo","helsinki","copenhagen","warsaw","prague",
                        "budapest","bucharest","lisbon","athens","tel aviv","dubai",
                        "singapore","hong kong","manila","jakarta","bangkok",
                        "sao paulo","buenos aires","mexico city","bogota","santiago",
                        "cairo","nairobi","lagos","johannesburg","cape town",
                    )
                    if any(_intl_re.search(r"\b" + _c + r"\b", _loc_raw, _intl_re.I)
                           for _c in _FOREIGN_CITIES):
                        _is_us = False

                    _loc_ok = _is_us or not any(
                        _intl_re.search(p, _true_original_location, _intl_re.I)
                        for p in _intl_patterns
                    )
                    _tech_ok = TitleProcessor.is_cs_engineering_role(_true_original_title)
                    _title_ok, _ = TitleProcessor.is_valid_job_title(_true_original_title)

                    # Additional validation for conflict entries
                    _title_lower = _true_original_title.lower()

                    # Clearance check (skip for big tech companies that never require it)
                    _NO_CLEAR_CO = {"apple", "google", "meta", "amazon", "microsoft",
                        "netflix", "uber", "lyft", "stripe", "tesla", "nvidia",
                        "tiktok", "bytedance", "salesforce", "pinterest", "snap",
                        "rivian", "lucid", "waymo", "cruise", "nuro", "zoox",
                        "openai", "anthropic", "cerebras", "groq", "ramp",
                        "coinbase", "robinhood", "doordash", "instacart",
                        "databricks", "snowflake", "palantir", "figma"}
                    _clearance_kw = ["security clearance", "secret clearance", "ts/sci",
                        "top secret", "polygraph"]
                    _co_check = _true_original_company.lower().strip()
                    _clearance_ok = (_co_check in _NO_CLEAR_CO) or not any(kw in _title_lower for kw in _clearance_kw)

                    # PhD filter — reject "Research Intern/Scientist" without BS/MS signal
                    _phd_titles = ["research intern", "research scientist intern",
                        "research engineer intern"]
                    _is_research = any(pt in _title_lower for pt in _phd_titles)
                    _has_bs_ms = any(kw in _title_lower for kw in ["bs/ms", "bs", "ms", "bachelor", "master"])
                    _phd_ok = not _is_research or _has_bs_ms

                    # Non-English filter
                    _non_english_kw = ["automatizare", "inteligenta artificiala", "dezvoltare",
                        "platforme", "bazata", "senzori", "inginerie", "testare",
                        "praktikum", "werkstudent", "alternance", "stagiaire"]
                    _lang_ok = not any(kw in _title_lower for kw in _non_english_kw)

                    # Salary keyword check in title (rare but catches "$20/hr" etc)
                    _salary_ok = True  # Can't check salary without page fetch

                    if _loc_ok and _tech_ok and _title_ok and _clearance_ok and _phd_ok and _lang_ok:
                        # Determine which company matches URL domain
                        # Company matching domain gets real URL, other gets search link
                        _url_domain_co = ""
                        try:
                            from urllib.parse import urlparse as _cu
                            _url_dom = _cu(resolved_url).netloc.lower().split(".")[0].replace("www","")
                            _url_domain_co = _url_dom
                        except Exception:
                            pass
                        _src_matches_url = _url_domain_co and (
                            _url_domain_co in re.sub(r"[^a-z0-9]", "", _true_original_company.lower())
                            or re.sub(r"[^a-z0-9]", "", _true_original_company.lower()) in _url_domain_co
                        )
                        # If source company matches URL, give it the real URL
                        # and swap the result's URL to search link
                        if _src_matches_url and result:
                            # Source company owns this URL — swap
                            result["url"] = "URL_CONFLICT"
                            _conflict_url = resolved_url
                        else:
                            _conflict_url = "URL_CONFLICT"

                        _conflict_hint = {
                            "company": _true_original_company,
                            "title": _true_original_title,
                            "location": _true_original_location,
                            "remote": "Unknown",
                            "url": _conflict_url,
                            "job_id": "N/A",
                            "job_type": self._detect_job_type(_true_original_title, job.get("_source_name", "")),
                            "sponsorship": _h1b_sponsorship(job.get("company", ""), _feed_sponsorship(job)),
                            "entry_date": self._format_date(),
                            "source": source,
                        }
                        with self._github_lock:
                            self.valid_jobs.append(_conflict_hint)
                            self.existing_jobs.add(_conflict_key)
                        logging.info(f"CONFLICT PRESERVED: {_true_original_company} | {_true_original_title}")

    def _process_emails_grouped(self, emails_data):
        processed_emails = ProcessedEmailTracker.load()
        email_counter = 0

        self._jobright_email_map = {}
        seen_jobright_urls = set()
        seen_jobright_company_titles = set()

        for email in emails_data:
            email_id = email["email_id"]
            sender = email["sender"]
            subject = email["subject"]
            html_content = email.get("html", "")
            urls = email["urls"]

            if email_id in processed_emails:
                logging.info(f"Skipping already processed email: {subject}")
                continue

            if sender == "ZipRecruiter" and html_content:
                zr_jobs = ZipRecruiterResolver.parse_email_jobs(html_content)
                if zr_jobs:
                    self._ziprecruiter_jobs_cache = zr_jobs
                    logging.info(f"Pre-parsed {len(zr_jobs)} ZipRecruiter jobs from: {subject}")

            if sender == "Jobright" and html_content:
                parsed_jobs = JobrightEmailParser.parse_email_jobs(html_content)
                if parsed_jobs:
                    self._jobright_email_map.update(parsed_jobs)
                    unique = len(set(id(v) for v in parsed_jobs.values()))
                    logging.info(f"Pre-parsed {unique} Jobright jobs from: {subject}")

            if sender == "LinkedIn" and html_content:
                from aggregator.extractors import LinkedInEmailParser
                li_jobs = LinkedInEmailParser.parse_email_jobs(html_content)
                if li_jobs:
                    if not hasattr(self, "_linkedin_email_map"):
                        self._linkedin_email_map = {}
                    self._linkedin_email_map.update(li_jobs)
                    logging.info(f"Pre-parsed {len(li_jobs)} LinkedIn jobs from: {subject}")

            deduped_urls = []
            for url_entry in urls:
                # SWE List returns (url, company_hint, title_hint) tuples
                if isinstance(url_entry, tuple):
                    url, _co_hint, _ti_hint = url_entry
                else:
                    url, _co_hint, _ti_hint = url_entry, "", ""
                clean = re.sub(r"\?.*$", "", url).lower()
                if "jobright.ai/jobs/info/" in clean:
                    if clean in seen_jobright_urls:
                        self.outcomes["skipped_duplicate_url"] += 1
                        continue
                    seen_jobright_urls.add(clean)

                    fallback = self._get_jobright_email_fallback(url)
                    if fallback:
                        ct_key = URLCleaner.normalize_text(
                            f"{fallback.get('company', '')}_{fallback.get('title', '')}"
                        )
                        if (
                            ct_key in seen_jobright_company_titles
                            or ct_key in self.existing_jobs
                        ):
                            self.outcomes["skipped_duplicate_company_title"] += 1
                            continue
                        seen_jobright_company_titles.add(ct_key)

                deduped_urls.append(url_entry)  # preserve tuple for SWE List

            email_counter += 1
            pre_dedup = len(urls) - len(deduped_urls)
            dedup_parts = []
            if pre_dedup > 0:
                dedup_parts.append(f"{pre_dedup} pre-deduped")
            pre_msg = f" ({', '.join(dedup_parts)})" if dedup_parts else ""
            print(
                f"\n  Email #{email_counter}: {subject} ({sender}) - {len(deduped_urls)} URLs{pre_msg}"
            )

            inline_dups = 0
            # Process URLs in parallel (10 threads) for speed
            import concurrent.futures as _cf
            def _process_url(args):
                idx, url_entry = args
                # Handle SWE List (url, company, title) tuples
                if isinstance(url_entry, tuple):
                    url, _swe_co, _swe_ti = url_entry
                else:
                    url, _swe_co, _swe_ti = url_entry, "", ""
                # Build enhanced subject hint from SWE List structured data
                _effective_subject = subject
                if _swe_co and _swe_ti:
                    _effective_subject = f"{_swe_ti} @ {_swe_co}"
                try:
                    return self._process_single_email_url(
                        url, sender, html_content, _effective_subject,
                        url_idx=idx + 1, url_total=len(deduped_urls),
                    )
                except Exception as e:
                    logging.error(f"Failed to process email URL {url}: {e}")
                    return None

            with _cf.ThreadPoolExecutor(max_workers=10) as pool:
                results = list(pool.map(_process_url, enumerate(deduped_urls)))

            inline_dups = sum(1 for r in results if r == "duplicate")
            if inline_dups > 0:
                print(f"    [{inline_dups} duplicates skipped]")

            ProcessedEmailTracker.mark_email_processed(
                processed_emails, email_id, subject, len(urls)
            )

        ProcessedEmailTracker.save(processed_emails)

    def _process_single_email_url(
        self, url, sender, email_html, subject, url_idx=0, url_total=0
    ):
        if any(domain in url.lower() for domain in BLACKLIST_DOMAINS):
            return "skipped"

        is_valid_url, url_reason = ValidationHelper.is_valid_job_url(url)
        if not is_valid_url:
            return "skipped"

        resolved_url = url
        is_company_site = False

        if "simplify.jobs" in url.lower():
            resolved_url, resolved = SimplifyRedirectResolver.resolve(url)
            if resolved_url == "__INACTIVE__":
                self.outcomes["skipped_inactive"] = self.outcomes.get("skipped_inactive", 0) + 1
                logging.info(f"REJECTED | Simplify INACTIVE | {url[:60]}")
                return "skipped"
            if not resolved:
                # Don't write Simplify wrapper URL — queue for retry and skip
                try:
                    from outreach.brain import Brain
                    _jid_m = __import__('re').search(r'/p/([a-f0-9-]+)', url)
                    if _jid_m:
                        Brain.get().queue_simplify_retry(_jid_m.group(1), url, "github_unresolved")
                except Exception:
                    pass
                self.outcomes["failed_simplify_resolution"] = self.outcomes.get("failed_simplify_resolution", 0) + 1
                logging.info(f"Simplify unresolved — queued for retry: {url[:60]}")
                return
                # (10 unreachable lines removed here: a Simplify-metadata
                #  location block sitting AFTER the return above, so it never
                #  ran. It referenced location_from_github, which is not even
                #  a parameter of this function - leftover from a different
                #  one. The return is correct: an unresolved Simplify URL is
                #  queued for retry and skipped.)

        if "jobright.ai" in url.lower():
            self._process_jobright_url(url, sender, email_html, subject)
            return "processed"

        if "ziprecruiter.com" in url.lower():
            self._process_ziprecruiter_url(url, sender, email_html, subject)
            return "processed"

        if "linkedin.com" in url.lower() and "/jobs/view/" in url.lower():
            self._process_linkedin_url(url, sender, email_html, subject)
            return "processed"

        if self._is_duplicate_url(resolved_url):
            self.outcomes["skipped_duplicate_url"] += 1
            return "duplicate"

        # Extract company and title hints from SWE List subject "Title @ Company | Simplify"
        _company_hint = ""
        _title_hint = ""
        if subject:
            import re as _re
            _at_match = _re.search(r'@\s*([^|]+?)(?:\s*\||\s*$)', subject)
            if _at_match:
                _company_hint = _at_match.group(1).strip()
            _title_match = _re.match(r'^(.+?)\s*@', subject)
            if _title_match:
                _title_hint = _title_match.group(1).strip()

        result = self._process_single_job_comprehensive(
            resolved_url,
            source=sender,
            email_html=email_html,
            company_hint=_company_hint or "",
            title_hint=_title_hint or "",
        )
        if not result:
            result = self._try_trusted_fallback(_company_hint or "", _title_hint or "", resolved_url, "", sender)
        if result:
            # Cross-validate: if extracted company doesn't match hint, use hint
            # This catches SWE List URL/company mismatches
            if _company_hint and result.get("company", "Unknown") not in ("Unknown", ""):
                from aggregator.utils import CompanyNormalizer
                _extracted_norm = re.sub(r"[^a-z0-9]", "", result["company"].lower())
                _hint_norm = re.sub(r"[^a-z0-9]", "", _company_hint.lower())
                # If extracted company shares <40% of chars with hint, prefer hint
                _common = sum(1 for c in _hint_norm if c in _extracted_norm)
                # The job PAGE is authoritative. If the extracted company matches
                # the org slug in the URL, trust it and ignore the email hint.
                _url_slug = re.sub(r"[^a-z0-9]", "", 
                    (re.search(r"(?:greenhouse\.io|lever\.co|ashbyhq\.com)/([a-z0-9_.-]+)",
                     (resolved_url or "").lower()) or type("x",(),{"group":lambda s,n:""})()).group(1))
                _page_matches_url = bool(_url_slug) and (
                    _extracted_norm in _url_slug or _url_slug in _extracted_norm)
                if _hint_norm and _common / len(_hint_norm) < 0.4 and not _page_matches_url:
                    logging.info(
                        f"SWE List company mismatch: extracted='{result['company']}' "
                        f"hint='{_company_hint}' — using hint"
                    )
                    _cleaned_hint = CompanyNormalizer.normalize(_company_hint)
                    if _cleaned_hint:
                        result["company"] = _cleaned_hint
            if _title_hint and result.get("title", "Unknown") == "Unknown":
                result["title"] = _title_hint
            alert = RoleCategorizer.get_terminal_alert(result["title"])
            print(f"    {result['company'][:50]}: ✓ Valid {alert}")
            self.source_stats[sender]["valid"] += 1
            return "valid"
        else:
            self.source_stats[sender]["rejected"] += 1
            return "rejected"

    def _process_jobright_url(self, url, sender, email_html, subject):
        email_fallback = self._get_jobright_email_fallback(url)

        if email_fallback and email_fallback.get("title", "Unknown") != "Unknown":
            company = email_fallback.get("company", "Unknown")
            title = TitleProcessor.clean_title_aggressive(
                email_fallback.get("title", "Unknown")
            )
            location = email_fallback.get("location", "Unknown")

            logging.info(
                f"STEP 1 | Jobright email data: {company} | {title} | {location}"
            )

            if self._is_duplicate(company, title, url):
                logging.info(f"STEP 2 | Duplicate: {company} | {title}")
                return

            is_internship, intern_reason = TitleProcessor.is_internship_role(title)
            if not is_internship:
                self.outcomes["skipped_senior_role"] += 1
                self.source_stats[sender]["rejected"] += 1
                self._print_rejected(company, intern_reason)
                logging.info(
                    f"STEP 2 | REJECTED | {company} | {title} | {intern_reason}"
                )
                return

            is_tech = TitleProcessor.is_cs_engineering_role(title)
            if not is_tech:
                self.outcomes["skipped_non_tech"] += 1
                self.source_stats[sender]["rejected"] += 1
                self._print_rejected(company, "Not CS/Engineering")
                logging.info(
                    f"STEP 2 | REJECTED | {company} | {title} | Not CS/Engineering"
                )
                return

            company_lower = company.lower().strip()
            if any(bl.lower() == company_lower for bl in COMPANY_BLACKLIST):
                reason = COMPANY_BLACKLIST_REASONS.get(company, "Blacklisted")
                self.outcomes["skipped_blacklisted"] += 1
                self.source_stats[sender]["rejected"] += 1
                self._add_discarded(
                    company,
                    title,
                    location,
                    "Unknown",
                    url,
                    "N/A",
                    "Internship",
                    sender,
                    reason,
                )
                self._print_rejected(company, "Blacklisted")
                logging.info(f"STEP 2 | REJECTED | {company} | Blacklisted: {reason}")
                return

            international_check = LocationProcessor.check_if_international(
                location, url=url, title=title
            )
            if international_check:
                self.outcomes["skipped_international"] += 1
                self.source_stats[sender]["rejected"] += 1
                self._add_discarded(
                    company,
                    title,
                    location,
                    "Unknown",
                    url,
                    "N/A",
                    "Internship",
                    sender,
                    international_check,
                )
                short_reason = international_check.replace("Location: ", "")
                self._print_rejected(company, short_reason)
                logging.info(f"STEP 2 | REJECTED | {company} | {international_check}")
                return

            logging.info(f"STEP 3 | Extracting original URL for: {company} | {title}")
            actual_url = self._extract_original_job_post_url(url)

            if actual_url:
                logging.info(f"STEP 4 | Original URL found: {actual_url[:80]}")

                if self._is_duplicate_url(actual_url):
                    self.outcomes["skipped_duplicate_url"] += 1
                    logging.info(f"STEP 5 | Duplicate URL: {actual_url[:60]}")
                    return

                result = self._process_single_job_comprehensive(
                    actual_url,
                    company_hint=company,
                    title_hint=title,
                    location_hint=location,
                    source=sender,
                    email_html=email_html,
                )
                if not result:
                    result = self._try_trusted_fallback(company, title, actual_url, location, sender)
                if result:
                    alert = RoleCategorizer.get_terminal_alert(result["title"])
                    print(
                        f"    {result['company'][:50]}: ✓ Valid {alert} (email→original)"
                    )
                    self.source_stats[sender]["valid"] += 1
                else:
                    self.source_stats[sender]["rejected"] += 1
                return

            logging.info(f"STEP 4 | Original URL NOT found for: {company} | {title}")
            logging.info(
                f"SKIPPED | {company} | {title} | Jobright original URL extraction failed"
            )
            self._print_rejected(company, "Jobright URL unresolved")
            self.outcomes["failed_jobright_resolution"] += 1
            self.source_stats[sender]["failed"] += 1
            return

        logging.info(f"STEP 1 | No email data for Jobright URL: {url[:60]}")

        if self._is_duplicate_url(url):
            self.outcomes["skipped_duplicate_url"] += 1
            return

        actual_url = self._extract_original_job_post_url(url)
        if actual_url:
            logging.info(f"STEP 2 | Original URL (no email data): {actual_url[:80]}")

            if self._is_duplicate_url(actual_url):
                self.outcomes["skipped_duplicate_url"] += 1
                return

            result = self._process_single_job_comprehensive(
                actual_url,
                source=sender,
                email_html=email_html,
            )
            if not result:
                result = self._try_trusted_fallback("", title, actual_url, "", sender)
            if result:
                alert = RoleCategorizer.get_terminal_alert(result["title"])
                print(
                    f"    {result['company'][:50]}: ✓ Valid {alert} (jobright→original)"
                )
                self.source_stats[sender]["valid"] += 1
            else:
                self.source_stats[sender]["rejected"] += 1
            return

        logging.info(f"SKIPPED | Jobright URL unresolvable | {url[:60]}")
        self._print_rejected("Jobright", "URL unresolvable")
        self.outcomes["failed_jobright_resolution"] += 1
        self.source_stats[sender]["failed"] += 1


    # ── Dead URL patterns ──────────────────────────────────────────────────
    _DEAD_URL_PATTERNS = [
        "notfound=1", "not_found=true", "ss=1&notfound=1",
        "/jobnot found", "/job-not-found", "/position-not-available",
        "/jobs/search?ss=1", "/search?ss=1", "jobnotfound",
        "/careersmarketplace/error", "/careers/error", "/job/error",
        "/error?", "/jobs/error", "?error=true", "?error=404",
        "?not_found=true", "?notfound=true",
        "position-not-available", "job-not-available",
    ]

    _DEAD_PAGE_TITLES = [
        "not found", "page not found", "job not found", "no longer available",
        "position no longer", "posting is no longer", "job has been filled",
        "come work with us", "not ready to apply", "page does not exist",
        "this job is closed",
        "this opportunity is currently not available",
        "opportunity is not available",
        "this position is no longer available",
        "this role has been filled",
        "job posting has expired",
        "application deadline has passed",
        "no longer accepting applications", "job has expired", "position has been closed",
        "sorry, this job", "opening is no longer", "role is no longer",
        "no longer accepting", "job listing not found", "search results",
        "career opportunities", "all jobs", "explore opportunities",
        "join our team", "current openings", "working at ",
    ]

    def _is_dead_url(self, url):
        """Check if URL pattern indicates a dead/expired job posting."""
        if not url:
            return False
        url_lower = url.lower()
        for pattern in self._DEAD_URL_PATTERNS:
            if pattern in url_lower:
                return True
        return False

    def _is_dead_page(self, title, final_url=None):
        """Check if page title or final URL indicates an expired/dead posting."""
        if title:
            title_lower = title.lower().strip()
            for pattern in self._DEAD_PAGE_TITLES:
                if pattern in title_lower:
                    return True
        if final_url:
            return self._is_dead_url(final_url)
        return False

    def _process_ziprecruiter_url(self, url, sender, email_html, subject):
        """Process ZipRecruiter URL: try HTTP redirect first, fall back to pre-parsed email data."""
        try:
            # FIX 6: check expires param before fetching — skip if expired
            try:
                import urllib.parse as _urlparse, time as _time
                _parsed = _urlparse.urlparse(url)
                _params = _urlparse.parse_qs(_parsed.query)
                _expires = _params.get("expires", [None])[0]
                if _expires:
                    _exp_ts = int(_expires)
                    _now_ts = int(_time.time())
                    _age_days = (_now_ts - (_exp_ts - 345600)) / 86400  # expires is ~4 days after post
                    if _now_ts > _exp_ts:
                        logging.info(f"ZipRecruiter URL expired (expires={_expires}), skipping")
                        self.outcomes["skipped_too_old"] = self.outcomes.get("skipped_too_old", 0) + 1
                        return
                    # Also check if posted more than 3 days ago based on expires offset
                    if _age_days > 3:
                        logging.info(f"ZipRecruiter URL too old ({_age_days:.1f}d), skipping")
                        self.outcomes["skipped_too_old"] = self.outcomes.get("skipped_too_old", 0) + 1
                        return
            except Exception as _exp_e:
                logging.debug(f"ZipRecruiter expiry check failed: {_exp_e}")

            actual_url = None
            try:
                import requests as _req
                resp = _req.get(url, allow_redirects=True, timeout=10,
                               headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"})
                if resp and resp.status_code == 200 and resp.url != url and "ziprecruiter.com" not in resp.url:
                    actual_url = resp.url
                elif resp and resp.status_code == 200 and "ziprecruiter.com" in resp.url:
                    # Check posting age on ZipRecruiter page
                    import re as _re
                    age_match = _re.search(r"Posted\s+(\d+)\s+days?\s+ago", resp.text)
                    if age_match and int(age_match.group(1)) > 3:
                        logging.info(f"ZipRecruiter job too old: {age_match.group(0)}")
                        self.outcomes["skipped_too_old"] = self.outcomes.get("skipped_too_old", 0) + 1
                        return
                    age_match2 = _re.search(r"Posted\s+30\+\s+Days?\s+Ago", resp.text, _re.I)
                    if age_match2:
                        logging.info(f"ZipRecruiter job too old: 30+ days")
                        self.outcomes["skipped_too_old"] = self.outcomes.get("skipped_too_old", 0) + 1
                        return
                    actual_url = ZipRecruiterResolver.resolve(resp.url)
            except Exception as e:
                logging.debug(f"ZipRecruiter redirect failed: {e}")

            if actual_url:
                if self._is_duplicate_url(actual_url):
                    self.outcomes["skipped_duplicate_url"] += 1
                    return
                result = self._process_single_job_comprehensive(
                    actual_url, source=sender, email_html=email_html
                )
                if not result:
                    # was `title`, which does not exist in this function -
                    # the signature is (url, sender, email_html, subject).
                    # The email subject is what carries the role name here,
                    # so the fallback was raising NameError instead of
                    # attempting a recovery.
                    result = self._try_trusted_fallback("", subject or "", actual_url, "", sender)
                if result:
                    alert = RoleCategorizer.get_terminal_alert(result["title"])
                    print(f"    {result['company'][:50]}: ✓ Valid {alert} (ZipRecruiter→resolved)")
                    self.source_stats[sender]["valid"] += 1
                else:
                    self.source_stats[sender]["rejected"] += 1
                return

            cached = self._match_ziprecruiter_cache(url)
            if not cached:
                self.outcomes["failed_ziprecruiter_resolution"] = self.outcomes.get("failed_ziprecruiter_resolution", 0) + 1
                return

            company = cached.get("company", "").strip()
            title = cached.get("title", "").strip()
            location = cached.get("location", "Unknown").strip()

            if not title or not company or company == "Unknown":
                self.outcomes["failed_ziprecruiter_resolution"] = self.outcomes.get("failed_ziprecruiter_resolution", 0) + 1
                return

            ct_key = _dedup_key(company, title)
            if ct_key in self.existing_jobs:
                self.outcomes["skipped_duplicate_company_title"] += 1
                return

            # A truncated title is NOT a reason to drop the job. 652 of 5,089
            # feed rows arrive truncated and 510 of them are real SWE roles -
            # rejecting them cost far more than the unevaluable ones it kept
            # out. "Could not verify" is not "disqualified".
            #
            # title_reconcile already replaces the title with the page's when
            # a page is reachable, so most of these carry the full title by
            # now. The rest are accepted as-is: a partial title is still a
            # job worth seeing, and the URL is there to check.
            #
            # Only actual disqualifying evidence rejects - PhD required,
            # citizenship, hardware, wrong season.

            is_valid_title, reason = TitleProcessor.is_valid_job_title(title)
            if not is_valid_title:
                self.outcomes["skipped_invalid_title"] += 1
                self._print_rejected(company, f"Invalid title: {reason}")
                logging.info(f"REJECTED | {company} | {title} | Invalid title: {reason} | ZipRecruiter")
                self.source_stats[sender]["rejected"] += 1
                return

            is_intern = any(ind in title.lower() for ind in ["intern", "co-op", "coop", "co op", "apprentice"]) or title.lower().rstrip().endswith(" co")
            if not is_intern:
                self.outcomes["skipped_not_internship"] = self.outcomes.get("skipped_not_internship", 0) + 1
                self._print_rejected(company, "Not internship")
                logging.info(f"REJECTED | {company} | {title} | Not internship | ZipRecruiter")
                self.source_stats[sender]["rejected"] += 1
                return

            # Early CS role check on title alone — avoids fetching non-CS pages
            _early_cs = TitleProcessor.is_cs_engineering_role(title)
            if not _early_cs:
                self.outcomes["skipped_non_tech"] = self.outcomes.get("skipped_non_tech", 0) + 1
                self._print_rejected(company, "Not CS/Engineering (title)")
                logging.info(f"REJECTED | {company} | {title} | Not CS/Engineering (early title check) | ZipRecruiter")
                self.source_stats[sender]["rejected"] += 1
                return
            # ── Full page validation on ZipRecruiter page itself ──────
            try:
                import requests as _req
                from aggregator.extractors import safe_parse_html as _sph
                zr_resp = _req.get(url, allow_redirects=True, timeout=10,
                                   headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"})
                if zr_resp and zr_resp.status_code == 200:
                    zr_soup, _ = _sph(zr_resp.text)
                    if zr_soup:
                        # Check CS/Engineering role using page description
                        zr_desc = zr_soup.get_text(separator=" ", strip=True)[:8000]
                        is_cs = TitleProcessor.is_cs_engineering_role(title, zr_desc)
                        if not is_cs:
                            self.outcomes["skipped_not_cs"] = self.outcomes.get("skipped_not_cs", 0) + 1
                            self._print_rejected(company, "Not CS/Engineering")
                            logging.info(f"REJECTED | {company} | {title} | Not a CS/Engineering role | ZipRecruiter")
                            self.source_stats[sender]["rejected"] += 1
                            return
                        # Check undergraduate-only
                        ug_result, _ = ValidationHelper._check_undergraduate_only_requirements(zr_soup)
                        if ug_result:
                            self.outcomes["skipped_undergrad"] = self.outcomes.get("skipped_undergrad", 0) + 1
                            self._print_rejected(company, "Undergraduate students only")
                            logging.info(f"REJECTED | {company} | {title} | Undergraduate students only (MS not eligible) | ZipRecruiter")
                            self.source_stats[sender]["rejected"] += 1
                            return
                        # Check PhD-only
                        phd_result, _ = ValidationHelper._check_phd_only_requirements(zr_soup)
                        if phd_result:
                            self.outcomes["skipped_phd"] = self.outcomes.get("skipped_phd", 0) + 1
                            self._print_rejected(company, "PhD students only")
                            logging.info(f"REJECTED | {company} | {title} | PhD students only | ZipRecruiter")
                            self.source_stats[sender]["rejected"] += 1
                            return
                        # Check page age (e.g. "Posted 29 days ago")
                        zr_age = ValidationHelper.extract_page_age(zr_soup)
                        if zr_age is not None and zr_age > PAGE_AGE_THRESHOLD_DAYS:
                            self.outcomes["skipped_too_old"] += 1
                            self._print_rejected(company, f"Posted {zr_age}d ago")
                            logging.info(f"REJECTED | {company} | {title} | Posted {zr_age}d ago | ZipRecruiter")
                            self._add_discarded(company, title, location, "Unknown", url, "N/A", "Internship", sender, f"Posted {zr_age} days ago (max {PAGE_AGE_THRESHOLD_DAYS})")
                            self.source_stats[sender]["rejected"] += 1
                            return
            except Exception as _ze:
                logging.debug(f"ZipRecruiter page validation failed: {_ze}")

            remote = "Remote" if "remote" in location.lower() else "Unknown"
            if location.lower() in ("unknown", "", "n/a"):
                location = "Unknown"

            job_data = {
                "company": company,
                "title": title,
                "location": location,
                "remote": remote,
                "url": url,
                "job_id": "N/A",
                "job_type": "Internship",
                "sponsorship": _h1b_sponsorship(company),
                "entry_date": self._format_date(),
                "source": sender,
            }

            quality = QualityScorer.calculate_score(job_data)
            if quality < MIN_QUALITY_SCORE:
                self.outcomes["skipped_low_quality"] += 1
                self._print_rejected(company, f"Low quality ({quality})")
                logging.info(f"REJECTED | {company} | {title} | Low quality: {quality} | ZipRecruiter")
                self.source_stats[sender]["rejected"] += 1
                return

            # ── URL-Company Validator (self-healing) ──
            job_data = validate_job(job_data)
            _ok, _why = validate_job_integrity(job_data)
            if not _ok:
                logging.info(f"INTEGRITY FAIL: {job_data.get('company', '?')} | {_why}")
                return
            company = job_data.get("company", company)
            title = job_data.get("title", title)

            # Re-classify resume if title was corrected by validator
            if job_data.get("_was_mismatched"):
                from aggregator.sheets_manager import SheetsManager
                job_data["resume_type"] = SheetsManager._classify_resume(title)

            self.valid_jobs.append(job_data)
            self.outcomes["valid"] += 1
            self.existing_jobs.add(ct_key)
            alert = RoleCategorizer.get_terminal_alert(title)
            print(f"    {company[:50]}: ✓ Valid {alert} (ZipRecruiter)")
            self.source_stats[sender]["valid"] += 1
            logging.info(f"ACCEPTED | {company} | {title} | {location} | ZipRecruiter (email data)")

        except Exception as e:
            logging.error(f"ZipRecruiter processing failed for {url[:60]}: {e}")

    def _match_ziprecruiter_cache(self, url):
        if not hasattr(self, "_ziprecruiter_jobs_cache") or not self._ziprecruiter_jobs_cache:
            return None
        for job in self._ziprecruiter_jobs_cache:
            if job.get("url", "") == url:
                return job
        return None
    def _process_linkedin_url(self, url, sender, email_html, subject):
        """Process LinkedIn Job Alert URL using pre-parsed email data + ATS lookup."""
        source_name = "LinkedIn"

        # -- Step 1: Get pre-parsed email data --
        li_data = self._get_linkedin_email_data(url)

        if not li_data or li_data.get("title", "Unknown") == "Unknown":
            logging.info(f"LINKEDIN | No parsed data for: {url[:60]}")
            self.outcomes["skipped_no_data"] = self.outcomes.get("skipped_no_data", 0) + 1
            return

        company = li_data.get("company", "Unknown")
        title = TitleProcessor.clean_title_aggressive(li_data.get("title", "Unknown"))
        location = li_data.get("location", "Unknown")
        linkedin_job_id = li_data.get("linkedin_job_id", "")

        logging.info(f"LINKEDIN STEP 1 | Email data: {company} | {title} | {location}")

        # -- Step 2: Pre-filter (same gates as Jobright) --
        if self._is_duplicate(company, title, url):
            logging.info(f"LINKEDIN STEP 2 | Duplicate: {company} | {title}")
            return

        job_type = "Internship"  # Default, overridden to "Full Time" for new grad
        is_internship, intern_reason = TitleProcessor.is_internship_role(title)
        if not is_internship:
            # Also accept new grad / entry-level roles from LinkedIn
            _tl = title.lower()
            _new_grad_indicators = [
                "new grad", "newgrad", "entry level", "entry-level",
                "associate ", "junior ", " i ", " 1 ", "engineer i",
                "engineer 1", "developer 1", "analyst 1", "scientist 1",
                "graduate ", "new college", "early career", " ncg ",
                "ncg -", "ncg-", "software engineer i", "software developer i",
            ]
            _is_new_grad = any(ng in _tl for ng in _new_grad_indicators)
            _is_new_grad = _is_new_grad or _tl.endswith(" i") or _tl.endswith(" 1")
            
            # Reject senior roles even if they match new grad keywords
            _senior = ["senior", "sr.", "sr ", "staff", "principal", "lead", "director", "manager"]
            _is_senior = any(s in _tl for s in _senior)
            
            if not _is_new_grad or _is_senior:
                self.outcomes["skipped_senior_role"] += 1
                self.source_stats[source_name]["rejected"] += 1
                self._print_rejected(company, intern_reason)
                logging.info(f"LINKEDIN STEP 2 | REJECTED | {company} | {title} | {intern_reason}")
                return
            # Accepted as new grad role — override job_type
            job_type = "Full Time"
            logging.info(f"LINKEDIN STEP 2 | Accepted as new grad: {company} | {title}")

        is_tech = TitleProcessor.is_cs_engineering_role(title)
        if not is_tech:
            self.outcomes["skipped_non_tech"] += 1
            self.source_stats[source_name]["rejected"] += 1
            self._print_rejected(company, "Not CS/Engineering")
            logging.info(f"LINKEDIN STEP 2 | REJECTED | {company} | {title} | Not CS/Engineering")
            return

        company_lower = company.lower().strip()
        if any(bl.lower() == company_lower for bl in COMPANY_BLACKLIST):
            reason = COMPANY_BLACKLIST_REASONS.get(company, "Blacklisted")
            self.outcomes["skipped_blacklisted"] += 1
            self.source_stats[source_name]["rejected"] += 1
            self._add_discarded(company, title, location, "Unknown", url, "N/A", job_type, source_name, reason)
            self._print_rejected(company, "Blacklisted")
            logging.info(f"LINKEDIN STEP 2 | REJECTED | {company} | Blacklisted: {reason}")
            return

        international_check = LocationProcessor.check_if_international(location, url=url, title=title)
        if international_check:
            self.outcomes["skipped_international"] += 1
            self.source_stats[source_name]["rejected"] += 1
            self._add_discarded(company, title, location, "Unknown", url, "N/A", "Internship", source_name, international_check)
            short_reason = international_check.replace("Location: ", "")
            self._print_rejected(company, short_reason)
            logging.info(f"LINKEDIN STEP 2 | REJECTED | {company} | {international_check}")
            return

        # -- Step 3: ATS Lookup (find real career page URL) --
        logging.info(f"LINKEDIN STEP 3 | ATS lookup for: {company} | {title}")
        ats_url = self._try_ats_lookup(company, title)

        if ats_url:
            logging.info(f"LINKEDIN STEP 4 | ATS found: {ats_url[:80]}")

            if self._is_duplicate_url(ats_url):
                self.outcomes["skipped_duplicate_url"] += 1
                logging.info(f"LINKEDIN STEP 5 | Duplicate ATS URL: {ats_url[:60]}")
                return

            result = self._process_single_job_comprehensive(
                ats_url,
                company_hint=company,
                title_hint=title,
                location_hint=location,
                source=source_name,
                email_html=email_html,
            )
            if not result:
                result = self._try_trusted_fallback(company, title, ats_url, location, source_name)
            if result:
                alert = RoleCategorizer.get_terminal_alert(result["title"])
                print(f"    {result['company'][:50]}: \u2713 Valid {alert} (LinkedIn\u2192ATS)")
                self.source_stats[source_name]["valid"] += 1

                # Self-learn: cache LinkedIn company -> ATS mapping in brain
                try:
                    from outreach.brain import Brain
                    b = Brain.get()
                    # Brain exposes _data, not data - same AttributeError that
                    # silently killed learned_company_names on every job.
                    if "linkedin_ats_cache" not in b._data:
                        b._data["linkedin_ats_cache"] = {}
                    b._data["linkedin_ats_cache"][company_lower] = {
                        "ats_url": ats_url[:120],
                        "last_seen": __import__("time").strftime("%Y-%m-%d"),
                    }
                except Exception:
                    pass
            else:
                self.source_stats[source_name]["rejected"] += 1
            return

        # -- Step 4: No ATS match -- write directly with email metadata --
        logging.info(f"LINKEDIN STEP 4 | No ATS match for {company} | {title} -- direct write")

        import urllib.parse
        search_query = urllib.parse.quote(f"{company} {title} careers apply")
        search_url = f"https://www.google.com/search?q={search_query}"

        ct_key = _dedup_key(company, title)
        if ct_key in self.existing_jobs:
            self.outcomes["skipped_duplicate_company_title"] += 1
            logging.info(f"LINKEDIN STEP 4 | Duplicate ct_key: {company} | {title}")
            return

        remote = "Remote" if "remote" in location.lower() else "Unknown"
        if location.lower() in ("unknown", "", "n/a"):
            location = "Unknown"

        job_data = {
            "company": company,
            "title": title,
            "location": location,
            "remote": remote,
            "url": search_url,
            "job_id": "N/A",  # LinkedIn IDs are internal, not real job IDs
            "job_type": job_type,
            "sponsorship": _h1b_sponsorship(company),
            "entry_date": self._format_date(),
            "source": source_name,
        }

        quality = QualityScorer.calculate_score(job_data)
        if quality < MIN_QUALITY_SCORE:
            self.outcomes["skipped_low_quality"] += 1
            self._print_rejected(company, f"Low quality ({quality})")
            logging.info(f"REJECTED | {company} | {title} | Low quality: {quality} | LinkedIn")
            self.source_stats[source_name]["rejected"] += 1
            return

        job_data = validate_job(job_data)
        _ok, _why = validate_job_integrity(job_data)
        if not _ok:
            logging.info(f"INTEGRITY FAIL: {job_data.get('company', '?')} | {_why}")
            return

        company = job_data.get("company", company)
        title = job_data.get("title", title)
        from aggregator.sheets_manager import SheetsManager
        job_data["resume_type"] = SheetsManager._classify_resume(title)

        self.valid_jobs.append(job_data)
        self.outcomes["valid"] += 1
        self.existing_jobs.add(ct_key)
        alert = RoleCategorizer.get_terminal_alert(title)
        print(f"    {company[:50]}: \u2713 Valid {alert} (LinkedIn\u2192direct)")
        self.source_stats[source_name]["valid"] += 1
        logging.info(f"ACCEPTED | {company} | {title} | {location} | LinkedIn (email data)")

    def _get_linkedin_email_data(self, url):
        """Look up pre-parsed LinkedIn email data for a URL."""
        if not hasattr(self, "_linkedin_email_map"):
            return None
        # Direct match
        if url in self._linkedin_email_map:
            return self._linkedin_email_map[url]
        # Extract job ID and try canonical URL
        import re as _re
        _m = _re.search(r"/jobs/view/(\d+)", url)
        if _m:
            canonical = f"https://www.linkedin.com/jobs/view/{_m.group(1)}"
            if canonical in self._linkedin_email_map:
                return self._linkedin_email_map[canonical]
        return None


    def _get_jobright_email_fallback(self, url):
        if not hasattr(self, "_jobright_email_map"):
            return None

        clean_url = re.sub(r"\?.*$", "", url).lower()
        if url in self._jobright_email_map:
            return self._jobright_email_map[url]
        if clean_url in self._jobright_email_map:
            return self._jobright_email_map[clean_url]

        for key, data in self._jobright_email_map.items():
            if clean_url in key.lower() or key.lower() in clean_url:
                return data

        return None

    def _extract_original_job_post_url(self, jobright_url):
        soup = None

        try:
            auth_response = self.jobright_auth.session.get(
                jobright_url,
                timeout=15,
                headers={
                    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
                },
            )
            if auth_response and auth_response.status_code == 200:
                soup, _ = safe_parse_html(auth_response.content)
                logging.info(f"Jobright auth fetch OK: {jobright_url[:60]}")
        except Exception as e:
            logging.info(f"Jobright auth fetch failed: {e}")

        if soup:
            url = self._parse_original_url_from_soup(soup)
            if url:
                return url

        try:
            logging.info(f"Jobright trying Selenium: {jobright_url[:60]}")
            response, final_url, page_source = self.page_fetcher.fetch_page(
                jobright_url
            )
            if response:
                page_html = (
                    response.text if hasattr(response, "text") else str(response)
                )
                soup, _ = safe_parse_html(page_html)
                if soup:
                    url = self._parse_original_url_from_soup(soup)
                    if url:
                        return url
        except Exception as e:
            logging.info(f"Jobright Selenium fetch failed: {e}")

        logging.info(f"Jobright original URL not found: {jobright_url[:60]}")
        return None

    def _parse_original_url_from_soup(self, soup):
        try:
            origin_link = soup.find("a", class_=re.compile(r"index_origin"))
            if not origin_link:
                origin_link = soup.find(
                    "a", string=re.compile(r"original\s+job\s+post", re.I)
                )
            if not origin_link:
                for link in soup.find_all("a", href=True):
                    link_text = link.get_text(strip=True).lower()
                    if "original" in link_text and "job" in link_text:
                        href = link.get("href")
                        if href and "jobright.ai" not in href:
                            origin_link = link
                            break

            if origin_link:
                href = origin_link.get("href")
                if href and href.startswith("http") and "jobright.ai" not in href:
                    logging.info(f"Jobright original job post: {href[:80]}")
                    return href

            script_tag = soup.find("script", {"id": "__NEXT_DATA__"})
            if script_tag:
                data = json.loads(script_tag.string)
                job_result = (
                    data.get("props", {})
                    .get("pageProps", {})
                    .get("dataSource", {})
                    .get("jobResult", {})
                )
                actual_url = job_result.get("applyLink") or job_result.get(
                    "originalUrl"
                )
                if actual_url and "jobright.ai" not in actual_url:
                    logging.info(f"Jobright JSON data: {actual_url[:80]}")
                    return actual_url

        except Exception as e:
            logging.debug(f"Soup parsing failed: {e}")

        return None

    _TRUSTED_FALLBACK_COMPANIES = {"tesla", "apple", "google", "meta", "amazon",
        "microsoft", "nvidia", "netflix", "uber", "lyft", "stripe",
        "airbnb", "spotify", "pinterest", "snap", "reddit",
        "openai", "anthropic", "databricks", "snowflake",
        "salesforce", "oracle", "adobe", "intel", "cisco",
        "palantir", "coinbase", "robinhood", "doordash", "instacart",
        "figma", "notion", "ramp", "brex", "discord",
        "rivian", "lucid", "neuralink", "waymo", "cruise",
        "tiktok", "bytedance", "verkada", "scale ai",
    }

    @staticmethod
    def _ats_company_match(search_company, ats_company):
        """Word-boundary company matching to avoid substring false positives.
        e.g. 'intel' should NOT match 'united imaging intelligence'
        but 'intel' SHOULD match 'Intel Corporation'
        """
        a = search_company.lower().strip()
        b = ats_company.lower().strip()
        # Exact match
        if a == b:
            return True
        # Check if ATS name appears as whole word(s) in search company
        if re.search(r'\b' + re.escape(b) + r'\b', a):
            return True
        # Check if search company appears as whole word(s) in ATS name
        if re.search(r'\b' + re.escape(a) + r'\b', b):
            return True
        return False

    def _try_ats_lookup(self, company, title):
        """When we only have a search URL, try to find the real job via ATS APIs."""
        import urllib.request, ssl, json
        _ctx = ssl.create_default_context()
        _co_lower = company.lower().strip()
        _ti_lower = title.lower().strip()
        _ti_words = set(_ti_lower.split())

        try:
            # Load discovered boards ONCE per process. The dicts below start
            # with only the ~263 hardcoded entries; ats_discovery has found
            # ~600 more. Without this the resolver is blind to every board the
            # pipeline taught itself about.
            if not getattr(UnifiedJobAggregator, "_discovery_loaded", False):
                try:
                    from aggregator.direct_sources import _load_discovered_companies
                    _load_discovered_companies()
                    logging.info("Resolver: discovered boards merged (once per run)")
                except Exception as _de:
                    logging.debug(f"discovery load failed: {_de}")
                UnifiedJobAggregator._discovery_loaded = True
            from aggregator.direct_sources import GREENHOUSE_COMPANIES, LEVER_COMPANIES, ASHBY_COMPANIES
        except ImportError:
            return None

        def _fetch(url, timeout=5):
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                resp = urllib.request.urlopen(req, timeout=timeout, context=_ctx)
                return json.loads(resp.read())
            except Exception:
                return None

        # Check Greenhouse
        for slug, name in GREENHOUSE_COMPANIES.items():
            if self._ats_company_match(_co_lower, name.lower()):
                data = _fetch(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs")
                if data and data.get("jobs"):
                    for job in data["jobs"]:
                        j_title = job.get("title", "").lower()
                        # Fuzzy title match — at least 60% word overlap
                        j_words = set(j_title.split())
                        overlap = len(_ti_words & j_words)
                        if overlap >= max(2, len(_ti_words) * 0.5):
                            job_url = job.get("absolute_url", "")
                            if job_url:
                                logging.info(f"ATS LOOKUP: Found {company} | {title} → {job_url[:60]}")
                                return job_url
                return None

        # Check Lever
        for slug, name in LEVER_COMPANIES.items():
            if self._ats_company_match(_co_lower, name.lower()):
                data = _fetch(f"https://api.lever.co/v0/postings/{slug}?mode=json")
                if data and isinstance(data, list):
                    for job in data:
                        j_title = job.get("text", "").lower()
                        j_words = set(j_title.split())
                        overlap = len(_ti_words & j_words)
                        if overlap >= max(2, len(_ti_words) * 0.5):
                            job_url = job.get("hostedUrl", "")
                            if job_url:
                                logging.info(f"ATS LOOKUP: Found {company} | {title} → {job_url[:60]}")
                                return job_url
                return None

        # Check Ashby
        for slug, name in ASHBY_COMPANIES.items():
            if self._ats_company_match(_co_lower, name.lower()):
                data = _fetch(f"https://api.ashbyhq.com/posting-api/job-board/{slug}")
                if data and data.get("jobs"):
                    for job in data["jobs"]:
                        j_title = job.get("title", "").lower()
                        j_words = set(j_title.split())
                        overlap = len(_ti_words & j_words)
                        if overlap >= max(2, len(_ti_words) * 0.5):
                            job_url = job.get("jobUrl", "")
                            if job_url:
                                logging.info(f"ATS LOOKUP: Found {company} | {title} → {job_url[:60]}")
                                return job_url
                return None

        # Check Workday
        try:
            from aggregator.direct_sources import WORKDAY_COMPANIES
            for wd_name, (domain, tenant, site) in WORKDAY_COMPANIES.items():
                if self._ats_company_match(_co_lower, wd_name.lower()):
                    search_url = f"https://{domain}/wday/cxs/{tenant}/{site}/jobs"
                    payload = json.dumps({
                        "appliedFacets": {},
                        "limit": 20,
                        "offset": 0,
                        "searchText": title,
                    }).encode()
                    try:
                        req = urllib.request.Request(search_url,
                            data=payload,
                            headers={"User-Agent": "Mozilla/5.0", "Content-Type": "application/json"},
                            method="POST")
                        resp = urllib.request.urlopen(req, timeout=10, context=_ctx)
                        data = json.loads(resp.read())
                    except Exception:
                        data = None
                    if data and data.get("jobPostings"):
                        for job in data["jobPostings"]:
                            j_title = job.get("title", "").lower()
                            j_words = set(j_title.split())
                            overlap = len(_ti_words & j_words)
                            if overlap >= max(2, len(_ti_words) * 0.5):
                                ext_path = job.get("externalPath", "")
                                if ext_path:
                                    job_url = f"https://{domain}{ext_path}"
                                    logging.info(f"ATS LOOKUP (Workday): {company} | {title} -> {job_url[:60]}")
                                    return job_url
                    return None
        except (ImportError, AttributeError):
            pass

        # Check SmartRecruiters
        try:
            from aggregator.direct_sources import SMARTRECRUITERS_COMPANIES
            for sr_id, sr_name in SMARTRECRUITERS_COMPANIES.items():
                if self._ats_company_match(_co_lower, sr_name.lower()):
                    import urllib.parse
                    encoded_title = urllib.parse.quote(title)
                    sr_url = f"https://api.smartrecruiters.com/v1/companies/{sr_id}/postings?limit=20&q={encoded_title}"
                    data = _fetch(sr_url, timeout=8)
                    if data and data.get("content"):
                        for job in data["content"]:
                            j_title = job.get("name", "").lower()
                            j_words = set(j_title.split())
                            overlap = len(_ti_words & j_words)
                            if overlap >= max(2, len(_ti_words) * 0.5):
                                job_url = job.get("ref", "")
                                if not job_url:
                                    pid = job.get("id", "")
                                    if pid:
                                        job_url = f"https://jobs.smartrecruiters.com/{sr_id}/{pid}"
                                if job_url:
                                    logging.info(f"ATS LOOKUP (SmartRecruiters): {company} | {title} -> {job_url[:60]}")
                                    return job_url
                    return None
        except (ImportError, AttributeError):
            pass

        return None

    def _try_trusted_fallback(self, company, title, url, location, source):
        """When HTTP fetch fails for a trusted company, accept source data."""
        _co = company.lower().strip()

        # Try ATS lookup first — get real URL and process properly
        if company and title:
            real_url = self._try_ats_lookup(company, title)
            if real_url:
                logging.info(f"TRUSTED FALLBACK → ATS LOOKUP: {company} | {title}")
                result = self._process_single_job_comprehensive(
                    real_url, company_hint=company, title_hint=title,
                    location_hint=location, source=source)
                if result:
                    return result

        if not any(tc in _co or _co in tc for tc in self._TRUSTED_FALLBACK_COMPANIES):
            return None
        # Extract job_id from URL
        _job_id = "N/A"
        from aggregator.processors import _COMPILED_JOB_ID_PATTERNS
        for _pat, _ in _COMPILED_JOB_ID_PATTERNS:
            _m = _pat.search(url)
            if _m:
                _job_id = _m.group(1)
                break
        # Dedup check BEFORE writing: this path previously appended blindly,
        # re-adding the same job on every run (622 dupes in one day).
        if self._is_duplicate(company, title, url):
            logging.info(f"TRUSTED FALLBACK SKIP (dup): {company} | {title}")
            return None

        logging.info(f"TRUSTED FALLBACK: {company} | {title} (HTTP failed, using source data)")
        result = {
            "company": company,
            "title": title,
            "location": location or "Unknown",
            "remote": "Unknown",
            "url": url,
            "job_id": _job_id,
            "job_type": self._detect_job_type(title, source),
            "sponsorship": _h1b_sponsorship(company),
            "entry_date": self._format_date(),
            "source": source,
        }
        with self._github_lock:
            self.valid_jobs.append(result)
            # Was a fifth inline key format: it skipped COMPANY_NAME_FIXES
            # and the Inc/LLC/Corp stripping, so "Bosch Group" was STORED
            # as boschgroup... and LOOKED UP as bosch..., re-adding the
            # same job every run. Use the one canonical key.
            self.existing_jobs.add(_dedup_key(company, title))
            self.source_stats[source]["valid"] += 1
        alert = RoleCategorizer.get_terminal_alert(title)
        print(f"  {company[:25]}: ✓ Trusted fallback {alert}")
        return result

    def _process_single_job_comprehensive(
        self,
        url,
        company_hint="",
        title_hint="",
        location_hint="",
        source="Unknown",
        email_html=None,
    ):
        try:
            # ══════════════════════════════════════════════════════
            # UNIVERSAL PRE-VALIDATION GATE
            # Runs on EVERY job from EVERY path. Cannot be bypassed.
            # ══════════════════════════════════════════════════════
            _co_hint = (company_hint or "").strip()
            _ti_hint = (title_hint or "").strip()
            _co_lower = _co_hint.lower()
            _ti_lower = _ti_hint.lower()

            # ── GATE -2: companies YOU ruled out ──
            # Personal decisions, not something a page check could ever find:
            # Philips does not hire from Northeastern. Checked before anything
            # else so a blacklisted company costs no fetch and no processing.
            try:
                from aggregator.apply_learned import (
                    is_user_blacklisted, is_user_blacklisted_title,
                    is_learned_clearance)
                _bt = is_user_blacklisted_title(_ti_hint)
                if (is_user_blacklisted(_co_hint)
                        or is_learned_clearance(_co_hint) or _bt):
                    self.outcomes["skipped_user_blacklist"] = (
                        self.outcomes.get("skipped_user_blacklist", 0) + 1)
                    _why = "title:" + _bt if _bt else "company"
                    logging.info(
                        f"REJECTED | {_co_hint} | {_ti_hint} | User blacklist ({_why})")
                    return None
            except Exception as _ube:
                logging.debug(f"user blacklist check failed: {_ube}")

            # ── GATE -1: truncated titles ──
            # zapplyjobs cuts titles at ~38 chars with an ellipsis, so the
            # words that would trigger a rejection are exactly the ones that
            # got removed. "Product Engineering Internship, Elect..." passed
            # the hardware filter because it could not see "Electronics
            # Hardware Design". 64 rows reached the sheet this way, including
            # Grants Specialist II and Patient Care Associate.
            # A title we cannot read is a title we cannot judge.
            try:
                from aggregator.truncated_title import is_truncated
                if is_truncated(_ti_hint):
                    # Do NOT reject here. 652 of 5,089 feed rows arrive
                    # truncated and 510 of them are real SWE jobs - dropping
                    # them at this gate cost far more than the unevaluable
                    # ones it kept out.
                    #
                    # The page is authoritative and title_reconcile recovers
                    # the full title from it after the fetch, at which point
                    # the normal filters judge the real text. So only note it
                    # here; the post-fetch gate rejects if no page arrives.
                    self.outcomes["truncated_title_seen"] = (
                        self.outcomes.get("truncated_title_seen", 0) + 1)
                    logging.debug(
                        f"TRUNCATED (deferred to post-fetch) | {_co_hint} | {_ti_hint}")
            except Exception as _tte:
                logging.debug(f"truncation check failed: {_tte}")

            # ── GATE 0: Summer 2027 term filter ──
            # Applies to EVERY source, not just the GitHub feeds: direct ATS
            # (~3,500/run), Indeed, email, LinkedIn all pass through here.
            # Fail-open by design — full-time, Fall, Spring, Winter and
            # anything ambiguous are kept; an exception keeps the job too.
            try:
                from aggregator.term_filter import should_drop_summer
                if should_drop_summer(
                    _ti_hint,
                    job_type=self._detect_job_type(_ti_hint, source),
                    company=_co_hint,
                    source=source,
                ):
                    self.outcomes["skipped_summer_2027"] = (
                        self.outcomes.get("skipped_summer_2027", 0) + 1
                    )
                    return
            except Exception as _tfe:
                logging.debug(f"GATE 0 term filter error (keeping job): {_tfe}")

            # ── GATE 1: Company blacklist (clearance + non-CS) ──
            _BLACKLIST_COMPANIES = [
                # Defense — ALWAYS require clearance (confirmed)
                "northrop grumman", "raytheon", "rtx", "leidos",
                "lockheed martin", "general dynamics", "l3harris",
                "saic", "caci", "mantech", "kbr", "amentum", "gdit",
                "peraton", "sierra space", "parsons",
                "captivation", "wyetech", "visionist",
                "sparksoft", "bae systems", "leonardo drs",
                # Non-CS companies
                "gulfstream", "solar turbines",
                # Education/Government (not internship employers)
                "university of texas at austin", "north orange county",
                "nationwide children", "children's hospital",
                "community college", "school district",
            ]
            if any(bc in _co_lower for bc in _BLACKLIST_COMPANIES):
                self._add_discarded(_co_hint, _ti_hint, location_hint or "Unknown", "Unknown",
                    url, "N/A", "Internship", source, f"Blacklisted company: {_co_hint}")
                logging.info(f"GATE REJECT | {_co_hint} | Blacklisted company")
                return None

            # ── GATE 1b: Y Combinator batch companies ──
            # YC startups carry a batch code: "Sixtyfour (X25)", "Agave (W22)".
            # Their links are usually unresolvable, and they rarely sponsor.
            if re.search(r"\((?:[SWXFsw xf]\s*\d{2})\)", _co_hint) or \
               re.search(r"\b(?:ycombinator|workatastartup)\b", url.lower()):
                self._add_discarded(_co_hint, _ti_hint, location_hint or "Unknown", "Unknown",
                    url, "N/A", "Internship", source, f"Y Combinator company: {_co_hint}")
                logging.info(f"GATE REJECT | {_co_hint} | Y Combinator batch company")
                return None

            # ── GATE 2: Title blacklist (non-tech, garbage, PM) ──
            _BLACKLIST_TITLES = [
                "people operations", "aircraft technician", "aircraft mechanic",
                "aircraft painter", "aircraft composite", "interior installation",
                "structural mechanic", "gas compressor", "system product engineer",
                "generator application", "power generation", "power plant",
                "field sales", "business development", "venture capital",
                "partnerships & growth", "partnerships and growth",
                "product manager", "transit intern", "instructor",
                "teaching", "teacher", "recruitment", "staffing",
                "avionics", "metrology", "cabinet maker", "payroll",
                "erp supervisor", "mechatronics", "shipyard",
                "packaging engineer",
            ]
            _GARBAGE_TITLES = [
                "application", "apply", "apply now", "job", "careers",
                "sign in", "login", "home", "search", "page not found",
                "404", "let's confirm you are human",
                "your connection was interrupted", "career page",
                "503 service temporarily unavailable",
            ]
            if _ti_lower.strip() in _GARBAGE_TITLES:
                logging.info(f"GATE REJECT | {_co_hint} | Garbage title: {_ti_hint}")
                return None
            if any(bt in _ti_lower for bt in _BLACKLIST_TITLES):
                if "product manager" in _ti_lower and any(kw in _ti_lower for kw in 
                    ["engineer", "technical", "software", "data", "ml", "ai", "platform"]):
                    pass  # "Technical Product Manager" etc. is OK
                else:
                    self._add_discarded(_co_hint, _ti_hint, location_hint or "Unknown", "Unknown",
                        url, "N/A", "Internship", source, f"Non-tech title: {_ti_hint[:40]}")
                    logging.info(f"GATE REJECT | {_co_hint} | Non-tech title: {_ti_hint[:40]}")
                    return None

            # ── GATE 3: LinkedIn URL rejection ──
            if "linkedin.com/jobs" in url:
                logging.info(f"GATE REJECT | LinkedIn job listing URL")
                return None

            # ── GATE 4: Run-level dedup ──
            if not hasattr(self, "_run_dedup_keys"):
                self._run_dedup_keys = set()
            if not hasattr(self, "_run_dedup_jobids"):
                self._run_dedup_jobids = set()
            _dedup_key = re.sub(r"[^a-z0-9]", "", f"{_co_lower}_{_ti_lower}")
            if _dedup_key in self._run_dedup_keys:
                logging.info(f"GATE REJECT | Run dedup: {_co_hint} | {_ti_hint[:40]}")
                return None
            self._run_dedup_keys.add(_dedup_key)

            # ── GATE 5: Extract job_id from URL early (for dedup) ──
            _url_job_id = None
            _jid_patterns = [
                r"/jobs?/(\d{5,})",
                r"_([A-Z]{1,4}-?\d{4,})(?:-\d+)?(?:\?|$)",
                r"gh_jid=(\d{7,})",
                r"/([A-Z]{2,3}\d{5,})(?:-\d+)?(?:\?|$)",
                r"[/_](R\d{4}-\d{3,})(?:\?|$|/)",
                r"_?(REQ-\d{4,})(?:\?|$|/)",
                r"/(\d{6,})(?:\?|$)",
            ]
            for _jp in _jid_patterns:
                _jm = re.search(_jp, url)
                if _jm:
                    _url_job_id = _jm.group(1)
                    break
            if _url_job_id and _co_lower:
                _jid_key = f"{_co_lower}_{_url_job_id}"
                if _jid_key in self._run_dedup_jobids:
                    logging.info(f"GATE REJECT | Run dedup job_id: {_co_hint} | {_url_job_id}")
                    return None
                self._run_dedup_jobids.add(_jid_key)


            # ══════════════════════════════════════════════════════
            # END PRE-VALIDATION GATE
            # ══════════════════════════════════════════════════════

            # ── Pre-fetch dead URL check ──────────────────────────
            if self._is_dead_url(url):
                co = company_hint or "Unknown"
                ti = title_hint or "Unknown"
                self.outcomes["skipped_expired"] = self.outcomes.get("skipped_expired", 0) + 1
                self._print_rejected(co, "Job posting expired/unavailable")
                logging.info(f"REJECTED | {co} | {ti} | Job posting expired/unavailable (dead URL)")
                self._add_discarded(co, ti, "Unknown", "Unknown", url, "N/A", "Internship", source, "Job posting expired/unavailable")
                return None
            platform = PlatformDetector.detect(url)

            response, final_url, page_source = self.page_fetcher.fetch_page(url)

            if not response:
                self.outcomes["failed_http"] += 1
                co = company_hint or "Unknown"
                ti = title_hint or "Unknown"
                self._print_rejected(co, "HTTP fetch failed")
                logging.info(f"REJECTED | {co} | {ti} | HTTP fetch failed | {url[:80]}")
                self._add_discarded(co, ti, location_hint or "Unknown", "Unknown", url, "N/A", "Internship", source, "HTTP fetch failed")
                return None

            # ── Post-fetch dead URL check ─────────────────────────
            if self._is_dead_url(final_url or ""):
                co = company_hint or "Unknown"
                ti = title_hint or "Unknown"
                self.outcomes["skipped_expired"] = self.outcomes.get("skipped_expired", 0) + 1
                self._print_rejected(co, "Job posting expired/unavailable")
                logging.info(f"REJECTED | {co} | {ti} | Job posting expired/unavailable (redirect)")
                self._add_discarded(co, ti, "Unknown", "Unknown", url, "N/A", "Internship", source, "Job posting expired/unavailable")
                return None

            soup, _ = safe_parse_html(
                response.text if hasattr(response, "text") else str(response)
            )
            if not soup:
                self.outcomes["failed_parse"] += 1
                co = company_hint or "Unknown"
                ti = title_hint or "Unknown"
                logging.info(f"REJECTED | {co} | {ti} | HTML parse failed | {url[:80]}")
                self._add_discarded(co, ti, location_hint or "Unknown", "Unknown", url, "N/A", "Internship", source, "HTML parse failed")
                return None

            # ── Post-parse dead page title check ──────────────────
            page_title = soup.title.string.strip() if soup.title and soup.title.string else ""
            if self._is_dead_page(page_title, final_url):
                co = company_hint or "Unknown"
                ti = title_hint or "Unknown"
                self.outcomes["skipped_expired"] = self.outcomes.get("skipped_expired", 0) + 1
                self._print_rejected(co, "Job posting expired/unavailable")
                logging.info(f"REJECTED | {co} | {ti} | Job posting expired/unavailable | Title: '{page_title[:60]}'")
                self._add_discarded(co, ti, "Unknown", "Unknown", url, "N/A", "Internship", source, "Job posting expired/unavailable")
                return None

            company = CompanyExtractor.extract_all_methods(final_url or url, soup)

            if self._is_garbage_company(company) and company_hint:
                company = company_hint
            elif not company or company == "Unknown":
                company = company_hint if company_hint else "Unknown"
            else:
                company_clean = CompanyExtractor.clean_company_name(company)
                if company_clean and not self._is_garbage_company(company_clean):
                    company = company_clean

            normalized = CompanyNormalizer.normalize(company, url)
            if normalized and not self._is_garbage_company(normalized):
                company = normalized
            # Auto-learn: save URL domain → company name for future runs
            try:
                from aggregator.processors import CompanyExtractor as _CE
                _CE.learn_company_name(final_url or url, company)
            except Exception:
                pass

            if self._is_garbage_company(company) and company_hint:
                company = company_hint

            # Apply company name normalization
            if company and company.lower().strip() in COMPANY_NAME_FIXES:
                fixed = COMPANY_NAME_FIXES[company.lower().strip()]
                if fixed != "Unknown":
                    logging.info(f"Company normalized: '{company}' → '{fixed}'")
                    company = fixed
                elif company_hint:
                    company = company_hint

            title = PageParser.extract_title(soup)
            if not title or title == "Unknown":
                title = title_hint if title_hint else "Unknown"

            # ── POST-GATE: PhD detection from raw title + page <title> ──
            _raw_phd_check = (title or "") + " " + (soup.title.string if soup and soup.title else "")
            if re.search(r"\(ph\.?d\.?\)", _raw_phd_check, re.I):
                _co = company_hint or "Unknown"
                self._add_discarded(_co, title, location_hint or "Unknown", "Unknown",
                    url, "N/A", "Internship", source, "PhD required (title)")
                logging.info(f"POST-GATE REJECT | PhD in title: {title[:50]}")
                return None

            title = TitleProcessor.clean_title_aggressive(title)
            # If extracted title looks like a page/company headline, trust hint
            if title_hint:
                _hint_clean = TitleProcessor.clean_title_aggressive(title_hint)
                _hint_intern, _ = TitleProcessor.is_internship_role(_hint_clean, github_category="Software Engineering Internship")
                _hint_valid, _ = TitleProcessor.is_valid_job_title(_hint_clean)
                _is_headline = "|" in title or len(title.split()) > 10 or any(
                    kw in title.lower() for kw in ["positions at", "careers at", "jobs at", "opportunities at", "join our", "work with us", "open roles"]
                )
                _is_intern, _ = TitleProcessor.is_internship_role(title, github_category="Software Engineering Internship")
                _is_valid, _ = TitleProcessor.is_valid_job_title(title)
                if (_is_headline or not _is_intern or not _is_valid) and _hint_intern and _hint_valid:
                    logging.info(f"Title override: page={title!r} hint={title_hint!r}")
                    title = _hint_clean

            # Duplicate check. This used to skip processing_lock on the
            # assumption that "the caller already set it for this URL" - it
            # does not. existing_urls is the sheet snapshot from startup, so
            # when N threads process the same posting concurrently, none of
            # them is in it yet and ALL N pass. Seven identical speedyapply_ai
            # rows reached the sheet that way in a single run.
            #
            # Now: check the in-progress set as well, and CLAIM the url inside
            # the same locked block so the check and the claim are atomic.
            # _ident was imported inside _is_duplicate, a DIFFERENT function,
            # so it was undefined here and this check raised NameError on
            # every job - the url half of post-fetch dedup never ran.
            from aggregator.utils import is_identifying_url as _ident
            _clean = URLCleaner.clean_url(final_url or url)
            _norm = _dedup_key(company, title)
            with getattr(self, "_github_lock", _NOOP_LOCK):
                _ident_ok = _ident(final_url or url)
                _url_dup = _ident_ok and (_clean in self.existing_urls
                                          or _clean in self.processing_lock)
                _job_dup = (_norm in self.existing_jobs
                            or _norm in self.processing_lock)
                if not _url_dup and not _job_dup:
                    # claim both keys before releasing the lock
                    if _ident_ok:
                        self.processing_lock.add(_clean)
                    self.processing_lock.add(_norm)
            if _url_dup:
                logging.info(f"DUPLICATE (url, post-fetch) | {company} | {title} | {_clean[:60]}")
                return None
            if _job_dup:
                logging.info(f"DUPLICATE (company+title, post-fetch) | {company} | {title}")
                return None

            # PROVENANCE: the page is authoritative, the feed is a pointer.
            # Feeds rewrite titles and every rewrite defeats a filter: JHU
            # APL's "2027 PhD Graduate" arrived as "New Grad", Cadence's
            # "Electronics Hardware Design" as "Internship, Elect...".
            # The page title supersedes, then the filters below re-run on it.
            #
            # This block was first added to _process_ziprecruiter_url by
            # mistake, where `soup` does not exist - so it raised NameError,
            # the except swallowed it, and the reconciler never ran at all.
            try:
                from aggregator.title_reconcile import reconcile as _recon
                _pt = ""
                if soup is not None:
                    _h1 = soup.find("h1")
                    if _h1:
                        _pt = _h1.get_text(strip=True)
                    if not _pt and soup.title:
                        _pt = soup.title.get_text(strip=True)
                if _pt:
                    _better, _dis, _why = _recon(title, _pt, company)
                    if _dis and _better and _better != title:
                        logging.info(
                            f"TITLE FROM PAGE | {company} | feed={title[:44]!r} "
                            f"page={_better[:44]!r} | {_why}")
                        self.outcomes["title_from_page"] = (
                            self.outcomes.get("title_from_page", 0) + 1)
                        title = _better
            except Exception as _tre:
                logging.debug(f"title reconcile failed: {_tre}")

            is_valid_title, reason = TitleProcessor.is_valid_job_title(title)
            if not is_valid_title:
                self.outcomes["skipped_invalid_title"] += 1
                self._print_rejected(company, f"Invalid title: {reason}")
                logging.info(
                    f"REJECTED | {company} | {title} | Invalid title: {reason}"
                )
                return None

            is_internship, intern_reason = TitleProcessor.is_internship_role(
                title, page_text=soup.get_text()[:5000] if soup else ""
            )
            # Only "simplify_newgrad" was exempt, so full-time roles from
            # speedyapply_*_newgrad, cvrve_newgrad, zapplyjobs_*, indeed_direct
            # and every direct-ATS source were rejected as "senior role".
            # Job type is the right test, not the source name.
            _gate_jt = self._detect_job_type(title, source)
            _is_ft = bool(_gate_jt) and _gate_jt.strip().lower() not in (
                "internship", "intern", "co-op", "coop")
            # Entry-level full-time only - never Senior/Staff/Principal/Manager
            if _is_ft and _is_senior_title(title):
                _is_ft = False
            if not is_internship and not _is_ft and not source.startswith("simplify_newgrad"):
                self.outcomes["skipped_senior_role"] += 1
                self._add_discarded(
                    company,
                    title,
                    location_hint,
                    "Unknown",
                    final_url or url,
                    "N/A",
                    "Full Time",
                    source,
                    intern_reason,
                )
                self._print_rejected(company, intern_reason)
                logging.info(f"REJECTED | {company} | {title} | {intern_reason}")
                return None

            season_ok, season_reason = TitleProcessor.check_season_requirement(
                title, page_text=soup.get_text()[:5000] if soup else ""
            )
            if not season_ok:
                self.outcomes["skipped_wrong_season"] += 1
                self._add_discarded(
                    company,
                    title,
                    location_hint,
                    "Unknown",
                    final_url or url,
                    "N/A",
                    "Internship",
                    source,
                    season_reason,
                )
                self._print_rejected(company, season_reason)
                logging.info(f"REJECTED | {company} | {title} | {season_reason}")
                return None

            is_tech = TitleProcessor.is_cs_engineering_role(
                title, description=soup.get_text()[:3000] if soup else ""
            )
            if not is_tech:
                self.outcomes["skipped_non_tech"] += 1
                self._add_discarded(
                    company,
                    title,
                    location_hint,
                    "Unknown",
                    final_url or url,
                    "N/A",
                    "Internship",
                    source,
                    "Not a CS/Engineering role",
                )
                self._print_rejected(company, "Not CS/Engineering")
                logging.info(
                    f"REJECTED | {company} | {title} | Not CS/Engineering role"
                )
                return None

            company_lower = company.lower().strip()
            if any(bl.lower() == company_lower for bl in COMPANY_BLACKLIST):
                reason = COMPANY_BLACKLIST_REASONS.get(company, "Blacklisted company")
                self.outcomes["skipped_blacklisted"] += 1
                self._add_discarded(
                    company,
                    title,
                    location_hint,
                    "Unknown",
                    final_url or url,
                    "N/A",
                    "Internship",
                    source,
                    reason,
                )
                self._print_rejected(company, "Blacklisted")
                logging.info(f"REJECTED | {company} | {title} | Blacklisted: {reason}")
                return None

            # ── Undergrad-only check: MS students not eligible ──
            if soup:
                try:
                    ug_result, ug_reason = ValidationHelper._check_undergraduate_only_requirements(soup)
                    if ug_result == "REJECT":
                        self._add_discarded(company, title, location_hint or "Unknown", "Unknown",
                            final_url or url, "N/A", "Internship", source, ug_reason)
                        self._print_rejected(company, ug_reason)
                        logging.info(f"REJECTED | {company} | {title} | {ug_reason}")
                        return None
                except Exception as _e:
                    logging.error(f"Undergrad check failed for {company}: {_e}")

            # ── JD clearance check: scan page text for clearance requirements ──
            # FIRST check whitelist — skip entirely for companies that never require clearance
            _NO_CLEARANCE_COMPANIES = {"apple", "google", "meta", "amazon", "microsoft",
                "netflix", "uber", "lyft", "stripe", "airbnb", "spotify", "pinterest",
                "tesla", "nvidia", "tiktok", "bytedance", "salesforce", "slack",
                "snap", "reddit", "dropbox", "coinbase", "robinhood", "doordash",
                "instacart", "databricks", "snowflake", "figma",  # palantir removed: user blacklist
                "rivian", "rivian and volkswagen", "lucid", "lucid motors",
                "centerfield", "waymo", "cruise", "nuro", "zoox", "aurora",
                "openai", "anthropic", "cerebras", "groq", "ramp", "brex",
                "notion", "airtable", "asana", "canva", "miro", "vercel",
                "mongodb", "elastic", "confluent", "datadog", "cloudflare",
                "hubspot", "twilio", "okta", "crowdstrike", "sentinelone",
                "discord", "toast", "squarespace", "plaid", "affirm", "chime",
                "verkada", "scale ai", "tenstorrent", "meshy",
                "sandisk", "copart", "eversana", "zipline", "1password"}
            _co_lower = company.lower().strip()
            _is_whitelisted = any(wc in _co_lower or _co_lower in wc for wc in _NO_CLEARANCE_COMPANIES)
            if soup and not _is_whitelisted:
                try:
                    _clearance_pats = [
                        r"security\s+clearance\s+(?:is\s+)?required",
                        r"(?:must|required to)\s+(?:have|hold|possess|obtain|maintain)\s+.*(?:security\s+clearance|secret\s+clearance)",
                        r"ability to obtain.*(?:secret|top secret|ts.sci)\s+(?:security\s+)?clearance",
                        r"ability to obtain and maintain.*security clearance",
                        r"willing.*able.*obtain.*(?:top secret|ts.sci|secret clearance)",
                        r"this\s+position\s+requires.*obtain.*maintain.*security\s+clearance",
                        r"clearance type.*(?:secret|top secret)",
                        r"u\.s\.\s+dod\s+security\s+clearance",
                        r"active\s+(?:secret|top secret|ts/sci)\s+clearance",
                        r"(?:secret|top secret)\s+clearance\s+(?:required|needed|mandatory)",
                    ]
                    _page_text = soup.get_text()[:10000].lower()
                    for _clr_pat in _clearance_pats:
                        if re.search(_clr_pat, _page_text, re.I):
                            self._add_discarded(company, title, location_hint or "Unknown", "Unknown",
                                final_url or url, "N/A", "Internship", source, "Security clearance required (JD)")
                            self._print_rejected(company, "Security clearance required (JD)")
                            logging.info(f"REJECTED | {company} | {title} | Clearance in JD")
                            # LEARN: the JD just proved this company requires clearance.
                            # Without this write the verdict died here and the same
                            # company returned every run - including through the
                            # GitHub path where no JD is ever fetched.
                            try:
                                from outreach.brain import Brain as _CB
                                _cb = _CB.get()
                                _cl = _cb._data.setdefault("learned_clearance", [])
                                _cn = company.strip().lower()
                                if _cn and _cn not in _cl:
                                    _cl.append(_cn)
                                    _cb.save()
                                    logging.info(f"LEARNED clearance company: {_cn}")
                            except Exception as _lce:
                                from aggregator.swallowed import swallow as _s
                                _s('brain.learned_clearance_write', _lce)
                            return None
                except Exception as _e:
                    logging.error(f"JD clearance check failed for {company}: {_e}")

            # ── H1B sponsorship detection from company name + JD text ──
            _sponsorship = "Unknown"
            try:
                from aggregator.config import H1B_KNOWN_SPONSORS, H1B_NO_SPONSOR, H1B_SPONSOR_JD_YES, H1B_SPONSOR_JD_NO
                _co_lower = company.lower().strip()
                # Check company lists first
                # Exact word match to avoid false positives (e.g. 'meta' matching 'metadata')
                _co_words = set(_co_lower.split())
                _co_norm = re.sub(r"[^a-z0-9 ]", "", _co_lower).strip()
                if _co_norm in H1B_KNOWN_SPONSORS or any(
                    s == _co_norm or (_co_norm.startswith(s + " ") or _co_norm.endswith(" " + s))
                    for s in H1B_KNOWN_SPONSORS if len(s) > 3
                ):
                    _sponsorship = "Yes"
                elif _co_norm in H1B_NO_SPONSOR or any(
                    s == _co_norm or (_co_norm.startswith(s + " ") or _co_norm.endswith(" " + s))
                    for s in H1B_NO_SPONSOR if len(s) > 3
                ):
                    _sponsorship = "No"
                # Then check JD text
                if soup and _sponsorship == "Unknown":
                    _jd_text = soup.get_text()[:10000].lower()
                    for _pat in H1B_SPONSOR_JD_NO:
                        if re.search(_pat, _jd_text, re.I):
                            _sponsorship = "No"
                            break
                    if _sponsorship == "Unknown":
                        for _pat in H1B_SPONSOR_JD_YES:
                            if re.search(_pat, _jd_text, re.I):
                                _sponsorship = "Yes"
                                break
            except (ImportError, AttributeError):
                pass

            # ── Salary check: reject jobs below $25/hr ──
            if soup:
                try:
                    _jd = soup.get_text()[:15000].lower()
                    _min_hourly = 25.0
                    _min_annual = 52000  # ~$25/hr full time

                    # Extract hourly rates: "$20/hr", "$20.00 per hour", "$20 an hour"
                    _hourly_pats = [
                        r'\$\s*(\d+(?:\.\d+)?)\s*(?:/\s*hr|per\s+hour|an\s+hour|hourly)',
                        r'hourly\s+(?:rate|pay|wage|compensation)\s*(?:of|:|\s)\s*\$\s*(\d+(?:\.\d+)?)',
                        r'\$\s*(\d+(?:\.\d+)?)\s*(?:to|-|–)\s*\$\s*\d+(?:\.\d+)?\s*(?:per\s+hour|/\s*hr|hourly)',
                    ]
                    for _sp in _hourly_pats:
                        _sm = re.search(_sp, _jd)
                        if _sm:
                            # Get the first (lowest) rate
                            _rate = float(_sm.group(1))
                            if _rate > 0 and _rate < _min_hourly:
                                self._add_discarded(company, title, location_hint or "Unknown", "Unknown",
                                    final_url or url, "N/A", "Internship", source,
                                    f"Low salary: ${_rate:.0f}/hr (minimum ${_min_hourly:.0f}/hr)")
                                self._print_rejected(company, f"Low salary: ${_rate:.0f}/hr")
                                logging.info(f"REJECTED | {company} | {title} | Salary ${_rate:.0f}/hr < ${_min_hourly:.0f}/hr")
                                return None
                            break

                    # Extract annual salary: "$50,000", "$50K", "$48,000 - $68,000"
                    # $25/hr × 40hrs × 52wks = $52,000/yr minimum
                    _annual_pats = [
                        r'\$\s*(\d{2,3}),?(\d{3})\s*(?:to|-|–|\s*-\s*)\s*\$\s*\d{2,3},?\d{3}',
                        r'\$\s*(\d{2,3})(?:k|K)\s*(?:to|-|–)\s*\$\s*\d{2,3}(?:k|K)',
                        r'(?:salary|compensation|pay|range)[:\s]+\$\s*(\d{2,3}),?(\d{3})',
                        r'\$\s*(\d{2,3}),?(\d{3})(?:/year|/yr|\s*per\s*year|\s*annually)',
                        r'(?:us\s*salary|base\s*salary)[:\s]+\$\s*(\d{2,3}),?(\d{3})',
                    ]
                    for _ap in _annual_pats:
                        _am = re.search(_ap, _jd)
                        if _am:
                            try:
                                if 'k' in _ap.lower() or 'K' in _ap:
                                    _annual = float(_am.group(1)) * 1000
                                else:
                                    _annual = float(_am.group(1) + _am.group(2))
                                if _annual > 0 and _annual < _min_annual:
                                    self._add_discarded(company, title, location_hint or "Unknown", "Unknown",
                                        final_url or url, "N/A", "Internship", source,
                                        f"Low salary: ${_annual:,.0f}/yr (minimum ${_min_annual:,}/yr)")
                                    self._print_rejected(company, f"Low salary: ${_annual:,.0f}/yr")
                                    logging.info(f"REJECTED | {company} | {title} | Salary ${_annual:,.0f}/yr < ${_min_annual:,}/yr")
                                    return None
                            except (ValueError, IndexError):
                                pass
                            break
                except Exception as _e:
                    logging.error(f"Salary extraction failed for {company}: {_e}")

            location = LocationExtractor.extract_all_methods(
                final_url or url,
                soup,
                title=title,
                platform=platform,
                page_source=page_source or "",
            )
            # Filter tech stack / programming language text mistakenly parsed as location
            if location and location != "Unknown":
                _tech_words = {"python", "rust", "java", "javascript", "golang", "ruby",
                    "react", "node", "sql", "docker", "kubernetes", "terraform",
                    "bazel", "c++", "typescript", "swift", "kotlin", "scala"}
                _loc_words = set(w.strip().lower().rstrip(",") for w in location.replace("/", " ").split())
                if _loc_words & _tech_words:
                    logging.info(f"Tech stack in location: {location!r} -> falling back to hint")
                    location = None
            if (
                (not location or location == "Unknown")
                and location_hint
                and location_hint != "Unknown"
            ):
                location = location_hint
            # ── POST-GATE: Clean location (all formats) ──
            if location and location != "Unknown":
                # Country prefix: "CANYCBellevue, WA" → "Bellevue, WA"
                for _pfx in ["CANYC", "CANY", "Canada", "United States ", "USA"]:
                    if location.startswith(_pfx) and len(location) > len(_pfx):
                        _rest = location[len(_pfx):].strip()
                        if _rest and _rest[0].isupper():
                            location = _rest
                            break
                # State prefix: "WI Beloit" → "Beloit, WI"
                _sp_match = re.match(r"^([A-Z]{2})\s+([A-Z][a-z].+)$", location)
                if _sp_match:
                    _st = _sp_match.group(1)
                    _ALL_STATES = {"AL","AK","AZ","AR","CA","CO","CT","DE","FL","GA","HI","ID","IL",
                        "IN","IA","KS","KY","LA","ME","MD","MA","MI","MN","MS","MO","MT",
                        "NE","NV","NH","NJ","NM","NY","NC","ND","OH","OK","OR","PA","RI",
                        "SC","SD","TN","TX","UT","VT","VA","WA","WV","WI","WY","DC"}
                    if _st in _ALL_STATES:
                        location = f"{_sp_match.group(2)}, {_st}"
                # BGR = Bulgaria
                if location.startswith("BGR") or "sofia" in location.lower():
                    self._add_discarded(company or company_hint, title or title_hint,
                        location, "Unknown", url, "N/A", "Internship", source, "Location: Bulgaria")
                    logging.info(f"POST-GATE | Bulgaria location: {location}")
                    return None
            # Normalize city-only locations to "City, ST" format
            if location and location != "Unknown":
                import re as _reloc
                if not _reloc.search(r',\s*[A-Z]{2}\b', location):
                    try:
                        from aggregator.config import CITY_TO_STATE_EXTRA
                        _llow = location.lower().strip()
                        _llow = _reloc.sub(r',?\s*(?:usa|united states)$', '', _llow).strip()
                        for _city, _st in CITY_TO_STATE_EXTRA.items():
                            if _city in _llow:
                                location = f"{_city.title()}, {_st}"
                                break
                    except Exception:
                        pass
            # Clean location: strip job type and remote words that leak into location
            if location and location != "Unknown":
                import re as _re
                location = _re.sub(r"(?i)^\s*(?:Internship|Full[- ]?Time|Part[- ]?Time|Co-?op|Contract|Temporary)\s*[,;]\s*", "", location)
                location = _re.sub(r"(?i)\s*[,;]\s*(?:Internship|Full[- ]?Time|Part[- ]?Time|Co-?op|Contract|Temporary)\s*$", "", location)
                # Strip remote status leaked into location
                location = _re.sub(r"(?i)\s*,?\s*(?:Hybrid|In Person|On Site|On-Site|Remote)\s*,?\s*(?:in-office.*)?$", "", location)
                location = _re.sub(r"(?i)^\s*(?:Hybrid|In Person|On Site|On-Site|Remote)\s*,?\s*", "", location)
                # Fix cases like "City, STHybrid" where no space between state and remote
                location = _re.sub(r"([A-Z]{2})(?:Hybrid|Remote|On Site|In Person).*$", r"\1", location)
                location = location.strip().strip(",").strip()

            international_check = LocationProcessor.check_if_international(
                location, soup=soup, url=final_url or url, title=title
            )
            if international_check:
                self.outcomes["skipped_international"] += 1
                self._add_discarded(
                    company,
                    title,
                    location,
                    "Unknown",
                    final_url or url,
                    "N/A",
                    "Internship",
                    source,
                    international_check,
                )
                short_reason = international_check.replace("Location: ", "")
                self._print_rejected(company, short_reason)
                logging.info(f"REJECTED | {company} | {title} | {international_check}")
                return None

            company_intl = LocationProcessor.check_company_for_international(company)
            if company_intl:
                self.outcomes["skipped_international"] += 1
                self._add_discarded(
                    company,
                    title,
                    location,
                    "Unknown",
                    final_url or url,
                    "N/A",
                    "Internship",
                    source,
                    company_intl,
                )
                self._print_rejected(company, "International (company name)")
                logging.info(f"REJECTED | {company} | {title} | {company_intl}")
                return None

            page_decision, page_reason, _ = ValidationHelper.check_page_restrictions(
                soup
            )
            if page_decision == "REJECT":
                self.outcomes["skipped_page_restriction"] += 1
                self._add_discarded(
                    company,
                    title,
                    location,
                    "Unknown",
                    final_url or url,
                    "N/A",
                    "Internship",
                    source,
                    page_reason,
                )
                self._print_rejected(company, page_reason)
                logging.info(f"REJECTED | {company} | {title} | {page_reason}")
                return None

            page_age = ValidationHelper.extract_page_age(soup)
            # FALLBACK LADDER (rung 4): if the ATS API gave us a real date the
            # job already passed the age gate upstream. Reaching here with no
            # page date means NOTHING knows how old this job is. Log it so we
            # can see what falls through, then drop it rather than assume fresh.
            if page_age is None:
                self.outcomes["skipped_no_date"] = self.outcomes.get("skipped_no_date", 0) + 1
                logging.info(
                    f"NO DATE | {company} | {title[:50]} | {source} | {(final_url or url)[:70]}"
                )
            if page_age is not None and page_age > PAGE_AGE_THRESHOLD_DAYS:
                self.outcomes["skipped_too_old"] += 1
                self._add_discarded(
                    company,
                    title,
                    location,
                    "Unknown",
                    final_url or url,
                    "N/A",
                    "Internship",
                    source,
                    f"Posted {page_age} days ago (max {PAGE_AGE_THRESHOLD_DAYS})",
                )
                self._print_rejected(company, f"Posted {page_age}d ago")
                logging.info(f"REJECTED | {company} | {title} | Posted {page_age}d ago")
                return None

            # Salary check — reject if listed and under $25/hr
            sal_dec, sal_reason = ValidationHelper.check_salary_requirement(soup)
            if sal_dec == "REJECT":
                self.outcomes["skipped_low_salary"] = self.outcomes.get("skipped_low_salary", 0) + 1
                self._add_discarded(company, title, location, "Unknown",
                    final_url or url, "N/A", "Internship", source, sal_reason)
                self._print_rejected(company, sal_reason)
                logging.info(f"REJECTED | {company} | {title} | {sal_reason}")
                return None

            remote = LocationProcessor.extract_remote_status_enhanced(
                soup,
                location,
                final_url or url,
                description=soup.get_text()[:2000] if soup else "",
            )
            job_id = PageParser.extract_job_id(soup, final_url or url)
            # Fallback: use URL-extracted job_id if page extraction failed
            if (not job_id or job_id == "N/A") and _url_job_id:
                job_id = _url_job_id
            # Sponsorship only matters for FULL TIME roles. Internships and
            # co-ops run on CPT, which needs no H-1B sponsorship, so a "no
            # sponsorship" line on an internship posting is about the
            # full-time conversion, not about whether you can do the
            # internship. Rejecting on it costs real opportunities.
            _sp_jt = (self._detect_job_type(title, source) or "").strip().lower()
            if _sp_jt in ("internship", "intern", "co-op", "coop"):
                sponsorship = "Unknown"
            else:
                sponsorship = ValidationHelper.check_sponsorship_status(soup)

            # Use original URL if redirect crossed to different domain (prevents company/URL mismatch)
            _store_url = final_url or url
            if final_url and url:
                try:
                    from urllib.parse import urlparse as _up
                    _orig_domain = _up(url).netloc.lower()
                    _final_domain = _up(final_url).netloc.lower()
                    # If domains differ significantly (not just www vs non-www), use original
                    _o = _orig_domain.replace("www.", "")
                    _f = _final_domain.replace("www.", "")
                    if _o != _f and not _f.endswith(_o) and not _o.endswith(_f):
                        _store_url = url
                        logging.debug(f"Domain mismatch: {_orig_domain} → {_final_domain}, using original URL")
                except Exception:
                    pass

            job_data = {
                "company": company,
                "title": title,
                "location": location,
                "remote": remote,
                "url": _store_url,
                "job_id": "N/A" if _store_url and "greenhouse.io" in _store_url.lower() else (job_id if job_id else "N/A"),
                "job_type": self._detect_job_type(title, source),
                "sponsorship": sponsorship,
                "entry_date": self._format_date(),
                "source": source,
            }

            quality = QualityScorer.calculate_score(job_data)
            if quality < MIN_QUALITY_SCORE:
                self.outcomes["skipped_low_quality"] += 1
                self._print_rejected(company, f"Low quality ({quality})")
                logging.info(f"REJECTED | {company} | {title} | Low quality: {quality}")
                return None

            # ── URL-Company Validator (self-healing) ──
            job_data = validate_job(job_data)
            _ok, _why = validate_job_integrity(job_data)
            if not _ok:
                logging.info(f"INTEGRITY FAIL: {job_data.get('company', '?')} | {_why}")
                return None
            company = job_data.get("company", company)
            title = job_data.get("title", title)

            # Re-classify resume if title was corrected by validator
            if job_data.get("_was_mismatched"):
                from aggregator.sheets_manager import SheetsManager
                job_data["resume_type"] = SheetsManager._classify_resume(title)

            with getattr(self, "_github_lock", _NOOP_LOCK):
                self.valid_jobs.append(job_data)
                self.outcomes["valid"] += 1
                self.existing_urls.add(URLCleaner.clean_url(final_url or url))
                self.existing_jobs.add(_dedup_key(company, title))
            if job_id and job_id != "N/A" and not job_id.startswith("HASH_"):
                self.existing_job_ids.add(job_id.lower())
                try:
                    from outreach.brain import Brain
                    Brain.get().register_job_id(job_id, company, title)
                except Exception:
                    pass

            logging.info(f"ACCEPTED | {company} | {title} | {location} | {source}")
            return job_data

        except Exception as e:
            logging.error(f"Processing failed for {url}: {e}", exc_info=True)
            return None

    def _is_garbage_company(self, name):
        if not name:
            return True
        return name.lower().strip() in GARBAGE_COMPANY_NAMES

    def _is_duplicate(self, company, title, url, job_id="N/A"):
        # INTERNAL ONLY. When the feed gave us no job id, derive one from the
        # URL for deduplication. Ashby carries a uuid in the path and 154 of
        # 167 Ashby rows had job_id "N/A", so they fell through to the weaker
        # company+title check. Job id is the only signal that still matches
        # when a title is reworded or a url gains tracking parameters.
        #
        # This value is NOT written to the sheet: an Ashby uuid is an internal
        # identifier that appears nowhere on the posting, so it would be
        # meaningless in a human facing column. The sheet keeps only ids a
        # person could actually quote.
        if not job_id or job_id in ("N/A", ""):
            try:
                from aggregator.job_id_extract import extract_job_id
                _derived = extract_job_id(url)
                if _derived:
                    job_id = _derived
            except Exception as _je:
                logging.debug(f"job id derivation failed: {_je}")

        with getattr(self, "_github_lock", _NOOP_LOCK):
            if job_id and job_id not in ("N/A", "") and not job_id.startswith("HASH_"):
                try:
                    from outreach.brain import Brain
                    if Brain.get().is_duplicate_job_id(job_id, company, title):
                        self.outcomes["skipped_duplicate_job_id"] += 1
                        logging.info(f"DUPLICATE (job_id) | {company} | {title}")
                        return True
                except Exception:
                    pass
            # Run-scoped job_id lock: catch same ID arriving twice in one run
            # (e.g. ByteDance jobs.bytedance.com vs joinbytedance.com share a numeric ID)
            if job_id and job_id not in ("N/A", "") and not job_id.startswith("HASH_"):
                try:
                    from outreach.brain import Brain
                    _nid = "JID_" + Brain.get().normalize_job_id(job_id)
                    if _nid != "JID_" and _nid in self.processing_lock:
                        self.outcomes["skipped_duplicate_job_id"] += 1
                        logging.info(f"DUPLICATE (job_id run) | {company} | {title}")
                        return True
                    if _nid != "JID_":
                        self.processing_lock.add(_nid)
                except Exception:
                    pass
            clean_url = URLCleaner.clean_url(url)
            # A non-identifying URL (google search, listing page) is shared by
            # hundreds of unrelated jobs - never dedup on it. company+title
            # below still catches genuine repeats.
            from aggregator.utils import is_identifying_url as _ident
            _url_is_key = _ident(url)
            if _url_is_key and (clean_url in self.existing_urls
                                or clean_url in self.processing_lock):
                self.outcomes["skipped_duplicate_url"] += 1
                logging.info(f"DUPLICATE (url) | {company} | {title} | {url[:60]}")
                return True
            # Normalize company name for dedup: strip Inc., LLC, (SRA), etc.
            import re as _dn_re
            norm_key = _dedup_key(company, title)
            if norm_key in self.existing_jobs or norm_key in self.processing_lock:
                self.outcomes["skipped_duplicate_company_title"] += 1
                logging.info(f"DUPLICATE (company+title) | {company} | {title}")
                return True
            # TF-IDF fuzzy dedup: catch near-duplicates like
            # "Software Engineering Intern" vs "Software Engineer - Intern"
            try:
                if not hasattr(self, "_similarity_engine"):
                    from analytics.similarity import TitleSimilarity
                    self._similarity_engine = TitleSimilarity()
                    for existing in self.existing_jobs:
                        parts = existing.split("_", 1)
                        if len(parts) == 2:
                            self._similarity_engine.add(parts[1], company=parts[0])
                match = self._similarity_engine.is_near_duplicate(title, company=company, threshold=0.90)
                if match:
                    self.outcomes["skipped_duplicate_fuzzy"] = self.outcomes.get("skipped_duplicate_fuzzy", 0) + 1
                    logging.info(f"DUPLICATE (fuzzy) | {company} | {title} ≈ {match.title} ({match.score:.2f})")
                    return True
                self._similarity_engine.add(title, company=company)
            except Exception:
                pass
            # Add norm_key to processing_lock so parallel threads see it as duplicate
            self.processing_lock.add(norm_key)
            self.processing_lock.add(clean_url)
            if (
                job_id
                and job_id != "N/A"
                and not job_id.startswith("HASH_")
                and job_id.lower() in self.existing_job_ids
            ):
                self.outcomes["skipped_duplicate_job_id"] += 1
                logging.info(f"DUPLICATE (job_id2) | {company} | {title}")
                return True
            self.processing_lock.add(clean_url)
            return False

    def _is_duplicate_url(self, url):
        clean_url = URLCleaner.clean_url(url)
        return clean_url in self.existing_urls or clean_url in self.processing_lock

    def _add_discarded(
        self,
        company,
        title,
        location,
        remote,
        url,
        job_id,
        job_type,
        source,
        reason,
    ):
        with getattr(self, "_github_lock", _NOOP_LOCK):
            # Dedup: skip if same URL+reason OR same company+title already discarded
            _url_key = (url, reason)
            _ct_key = re.sub(r"[^a-z0-9]", "", f"{company}_{title}".lower())
            if not hasattr(self, "_discarded_url_seen"):
                self._discarded_url_seen = set()
            if not hasattr(self, "_discarded_ct_seen"):
                self._discarded_ct_seen = set()
            if _url_key in self._discarded_url_seen or _ct_key in self._discarded_ct_seen:
                return
            self._discarded_url_seen.add(_url_key)
            self._discarded_ct_seen.add(_ct_key)
            self.discarded_jobs.append(
                {
                    "company": company,
                    "title": title,
                    "location": location,
                    "remote": remote,
                    "url": url,
                    "job_id": job_id,
                    "job_type": job_type,
                    "source": source,
                    "reason": reason,
                    "entry_date": self._format_date(),
                    "sponsorship": _h1b_sponsorship(company),
                }
            )
            self.outcomes["discarded"] += 1
        # Register discarded job_id in Brain to prevent re-processing
        if job_id and job_id not in ("N/A", "") and not job_id.startswith("HASH_"):
            try:
                from outreach.brain import Brain
                Brain.get().register_job_id(job_id, company, title)
            except Exception:
                pass
        # Soft-track company rejection in Brain (for weekly review — NOT auto-blacklist)
        if company and company not in ("Unknown", "N/A", ""):
            try:
                from outreach.brain import Brain
                Brain.get().record_company_rejection(company, reason)
            except Exception:
                pass

    def _print_rejected(self, company, reason):
        display = (company or "Unknown")
        logging.info(f"REJECTED | {display} | {reason}")
        if not getattr(self, "_github_mode", False):
            print(f"    {display}: ✗ {reason}")

    def _ensure_mutual_exclusion(self):
        if not self.valid_jobs or not self.discarded_jobs:
            return
        valid_keys = {
            (
                URLCleaner.normalize_text(j["company"]),
                URLCleaner.normalize_text(j["title"]),
            )
            for j in self.valid_jobs
        }
        discarded_keys = {
            (
                URLCleaner.normalize_text(j["company"]),
                URLCleaner.normalize_text(j["title"]),
            )
            for j in self.discarded_jobs
        }
        overlap = valid_keys & discarded_keys
        if overlap:
            self.valid_jobs = [
                j
                for j in self.valid_jobs
                if (
                    URLCleaner.normalize_text(j["company"]),
                    URLCleaner.normalize_text(j["title"]),
                )
                not in overlap
            ]
            self.outcomes["valid"] = len(self.valid_jobs)

    def _print_summary(self):
        print("\n" + "=" * 80)
        print("SUMMARY:")
        print("=" * 80)

        summary_items = [
            ("✓ Valid", self.outcomes["valid"]),
            ("✗ Discarded", self.outcomes["discarded"]),
            ("⊘ Duplicate URL", self.outcomes["skipped_duplicate_url"]),
            ("⊘ Duplicate job", self.outcomes["skipped_duplicate_company_title"]),
            ("⊘ Duplicate ID", self.outcomes["skipped_duplicate_job_id"]),
            ("⊘ Too old", self.outcomes.get("skipped_too_old", 0)),
            ("⊘ Wrong season", self.outcomes["skipped_wrong_season"]),
            ("⊘ Senior role", self.outcomes["skipped_senior_role"]),
            ("⊘ Non-tech", self.outcomes["skipped_non_tech"]),
            ("⊘ Invalid title", self.outcomes.get("skipped_invalid_title", 0)),
            ("⊘ International", self.outcomes.get("skipped_international", 0)),
            ("⊘ Blacklisted", self.outcomes["skipped_blacklisted"]),
            ("⊘ Page restriction", self.outcomes.get("skipped_page_restriction", 0)),
            ("⊘ Low quality", self.outcomes["skipped_low_quality"]),
            ("✗ HTTP failed", self.outcomes["failed_http"]),
            ("✗ Parse failed", self.outcomes["failed_parse"]),
            ("✗ Jobright unresolved", self.outcomes["failed_jobright_resolution"]),
            ("✗ ZipRecruiter unresolved", self.outcomes.get("failed_ziprecruiter_resolution", 0)),
            ("⊘ Low salary", self.outcomes.get("skipped_low_salary", 0)),
        ]
        for label, count in summary_items:
            if count > 0:
                print(f"  {label}: {count}")

        if self.source_stats:
            print("\n  BY SOURCE:")
            for source_name in sorted(self.source_stats.keys()):
                stats = self.source_stats[source_name]
                v = stats.get("valid", 0)
                r = stats.get("rejected", 0)
                f_count = stats.get("failed", 0)
                parts = []
                if v:
                    parts.append(f"{v} valid")
                if r:
                    parts.append(f"{r} rejected")
                if f_count:
                    parts.append(f"{f_count} failed")
                if parts:
                    print(f"    {source_name}: {', '.join(parts)}")

        rejection_reasons = defaultdict(int)
        for job in self.discarded_jobs:
            reason = job.get("reason", "Unknown")
            short = reason.split(":")[0].split("(")[0].strip()[:40]
            rejection_reasons[short] += 1

        if rejection_reasons:
            print("\n  TOP REJECTION REASONS:")
            sorted_reasons = sorted(
                rejection_reasons.items(), key=lambda x: x[1], reverse=True
            )
            for reason, count in sorted_reasons[:10]:
                print(f"    {reason}: {count}")

        print("=" * 80)

    @staticmethod
    def _parse_github_age(age_str):
        if not age_str:
            return None
        age_str = age_str.strip().lower()
        # Format: "1mo", "2mo" → months. Must be checked BEFORE the bare
        # minute pattern, or "1mo" would match as 1 minute.
        mo_match = re.match(r"^(\d+)\s*mo$", age_str)
        if mo_match:
            return int(mo_match.group(1)) * 30
        # Format: "11m" / "52m" → MINUTES (zapplyjobs regenerates its README
        # every few minutes, so these are the freshest jobs we get). Anything
        # under a day is age 0.
        m_match = re.match(r"^(\d+)\s*m$", age_str)
        if m_match:
            return 0
        # Format: "20h" → hours, still today
        h_match = re.match(r"^(\d+)\s*h$", age_str)
        if h_match:
            return 0
        # Format: "1w" / "2w" → weeks
        w_match = re.match(r"^(\d+)\s*w$", age_str)
        if w_match:
            return int(w_match.group(1)) * 7
        # Format: "2026-08-22" → ISO date
        iso_match = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", age_str)
        if iso_match:
            import datetime as _isodt
            try:
                _d = _isodt.date(int(iso_match.group(1)),
                                 int(iso_match.group(2)),
                                 int(iso_match.group(3)))
                return (_isodt.date.today() - _d).days
            except ValueError:
                return 999
        # Format: "5d" → 5 days
        match = re.match(r"^(\d+)\s*d$", age_str)
        if match:
            return int(match.group(1))
        # Format: "2mo" → 60 days
        match = re.match(r"^(\d+)mo$", age_str.lower())
        if match:
            return int(match.group(1)) * 30
        # Format: "Oct 15", "Feb 19" etc — vanshb03 calendar dates
        import datetime as _dt
        month_map = {
            "jan":1,"feb":2,"mar":3,"apr":4,"may":5,"jun":6,
            "jul":7,"aug":8,"sep":9,"oct":10,"nov":11,"dec":12
        }
        cal_match = re.match(r"^([A-Za-z]{3})\s+(\d{1,2})$", age_str.strip())
        if cal_match:
            mon = cal_match.group(1).lower()
            day = int(cal_match.group(2))
            if mon in month_map:
                today = _dt.date.today()
                # Try current year first
                try:
                    candidate = _dt.date(today.year, month_map[mon], day)
                except ValueError:
                    return 999
                # If candidate is in the future, it must be from last year
                if candidate > today:
                    try:
                        candidate = _dt.date(today.year - 1, month_map[mon], day)
                    except ValueError:
                        return 999
                days_ago = (today - candidate).days
                return days_ago
        return DateParser.extract_days_ago(age_str)

    @staticmethod
    def _detect_job_type(title, source_name=""):
        """Smart job type detection from title and source name."""
        tl = title.lower() if title else ""
        sl = source_name.lower() if source_name else ""

        # TITLE FIRST. An explicit intern or co-op word in the title outranks
        # any source name. The newgrad source check used to run before the
        # title checks below, so EVERY job from a *_newgrad feed came back
        # Full Time - including "Summer 2027 Intern - Software Engineering"
        # from zapplyjobs_newgrad. Those feeds carry both role types.
        #
        # That single wrong label caused two wrong outcomes: the Job Type
        # column said Full Time, and the summer filter skipped the row,
        # because rule 1 never filters full-time roles.
        if any(kw in tl for kw in ["co-op", "co op", "coop"]):
            return "Co-op"
        if any(kw in tl for kw in ["intern", "internship"]):
            return "Internship"

        # New grad sources - only once the title has offered no signal
        if "newgrad" in sl or "new_grad" in sl or "new-grad" in sl:
            return "Full Time"

        # Title-based detection
        if any(kw in tl for kw in ["new grad", "new-grad", "entry level", "entry-level",
                                     "full time", "full-time", "junior engineer",
                                     "associate engineer", "sde i ", "sde 1 ",
                                     "software engineer i ", "engineer i "]):
            return "Full Time"

        if any(kw in tl for kw in ["co-op", "coop", "co op"]):
            return "Co-op"

        if any(kw in tl for kw in ["intern", "internship"]):
            return "Internship"

        # Off-season sources default to internship/co-op
        if "offseason" in sl or "off_season" in sl:
            return "Internship"

        # Direct ATS boards list ALL open roles. If no intern/co-op
        # signal matched above, this is a full-time posting.
        if "_direct" in sl or "direct_ats" in sl:
            return "Full Time"
        return "Internship"

    @staticmethod
    def _format_date():
        return datetime.datetime.now().strftime("%d-%b-%Y")

    @staticmethod
    def _looks_like_title(text):
        if not text:
            return False
        return (
            sum(
                1
                for kw in {"intern", "co-op", "engineer", "developer", "software"}
                if kw in text.lower()
            )
            >= 2
        )

    @staticmethod
    def _safe_scrape(url, source_name):
        try:
            # Source-specific preprocessing for non-standard markdown formats
            return SimplifyGitHubScraper.scrape(url, source_name=source_name)
        except Exception as e:
            print(f"  ✗ {source_name} error: {e}")
            logging.error(f"{source_name} scraping failed: {e}")
            return []

    @staticmethod
    def _scrape_zapplyjobs(url, source_name):
        """Zapplyjobs: Company | Role | Location | Posted | Visa | Apply(url)"""
        try:
            resp = retry_request(url)
            if not resp or resp.status_code != 200:
                return []
            import re as _zre
            text = resp.text
            text = _zre.sub(r'🏢\s*', '', text)
            text = _zre.sub(r'\*\*', '', text)
            jobs = []
            lines = text.split("\n")
            in_table = False
            last_company = ""
            for line in lines:
                if "Company" in line and "Role" in line and "|" in line:
                    in_table = True
                    continue
                if in_table and line.strip().startswith("|--"):
                    continue
                if not in_table or "|" not in line:
                    continue
                parts = [p.strip() for p in line.split("|") if p.strip() != ""]
                if len(parts) < 4:
                    continue
                if "🔒" in line:
                    continue
                company = parts[0].strip()
                if company and "↳" not in company:
                    last_company = company
                else:
                    company = last_company
                title = parts[1].strip() if len(parts) > 1 else ""
                location = parts[2].strip() if len(parts) > 2 else "Unknown"
                age = parts[3].strip() if len(parts) > 3 else "0d"
                # URL is in the last part: Apply(https://...) or [Apply](https://...)
                url_cell = parts[-1] if len(parts) > 4 else ""
                url_match = _zre.search(r'\((https?://[^)]+)\)', url_cell)
                if not url_match:
                    url_match = _zre.search(r'(https?://[^\s)]+)', url_cell)
                job_url = url_match.group(1) if url_match else ""
                if company and title and job_url:
                    jobs.append({
                        "company": company,
                        "title": title,
                        "url": job_url,
                        "location": location,
                        "age": age,
                        "is_closed": False,
                        "source": source_name,
                        "github_category": "",
                    })
            logging.info(f"{source_name}: Parsed {len(jobs)} jobs")
            return jobs
        except Exception as e:
            logging.error(f"zapplyjobs scrape failed: {e}")
            return []

    @staticmethod
    def _scrape_jobright_github(url, source_name):
        """Jobright uses **[Company](url)** | **[Title](url)** format."""
        try:
            resp = retry_request(url)
            if not resp or resp.status_code != 200:
                return []
            import re as _jre
            text = resp.text
            jobs = []
            lines = text.split("\n")
            header_idx = -1
            for i, line in enumerate(lines):
                if "Company" in line and "Job Title" in line and "|" in line:
                    header_idx = i
                    break
            if header_idx == -1:
                return []
            for line in lines[header_idx + 2:]:
                if not line.strip() or "|" not in line:
                    continue
                parts = [p.strip() for p in line.split("|") if p.strip()]
                if len(parts) < 5:
                    continue
                # Extract company: **[Name](url)** → Name
                company_match = _jre.search(r'\[([^\]]+)\]', parts[0])
                company = company_match.group(1) if company_match else parts[0]
                company = _jre.sub(r'\*+', '', company).strip()
                # Extract title: **[Title](url)** → Title
                title_match = _jre.search(r'\[([^\]]+)\]', parts[1])
                title = title_match.group(1) if title_match else parts[1]
                title = _jre.sub(r'\*+', '', title).strip()
                # Extract URL from title link
                url_match = _jre.search(r'\(([^)]+)\)', parts[1])
                job_url = url_match.group(1) if url_match else ""
                location = parts[2] if len(parts) > 2 else "Unknown"
                location = _jre.sub(r'\*+', '', location).strip()
                work_model = parts[3] if len(parts) > 3 else "Unknown"
                age = parts[4] if len(parts) > 4 else "0d"
                if company and title and job_url:
                    jobs.append({
                        "company": company,
                        "title": title,
                        "url": job_url,
                        "location": location,
                        "age": age,
                        "is_closed": False,
                        "source": source_name,
                        "github_category": "",
                    })
            logging.info(f"{source_name}: Parsed {len(jobs)} jobs")
            return jobs
        except Exception as e:
            logging.error(f"jobright_github scrape failed: {e}")
            return []

    @staticmethod
    def _scrape_simplify_offseason(url, source_name):
        """SimplifyJobs off-season uses HTML tables with <tr><td> format."""
        try:
            resp = retry_request(url)
            if not resp or resp.status_code != 200:
                return []
            import re as _ore
            text = resp.text
            jobs = []
            tr_blocks = _ore.findall(r'<tr>(.*?)</tr>', text, _ore.S)
            for tr in tr_blocks:
                if '🔒' in tr:
                    continue
                # Extract company
                co_match = _ore.search(r'<strong><a[^>]*>([^<]+)</a></strong>', tr)
                if not co_match:
                    continue
                company = co_match.group(1).strip()
                # Extract all <td> contents
                tds = _ore.findall(r'<td[^>]*>(.*?)</td>', tr, _ore.S)
                if len(tds) < 3:
                    continue
                # td[0] = company, td[1] = title, td[2] = location
                title_raw = tds[1] if len(tds) > 1 else ""
                title = _ore.sub(r'<[^>]+>', '', title_raw).strip()
                location = _ore.sub(r'<[^>]+>', '', tds[2]).strip() if len(tds) > 2 else "Unknown"
                # Extract URL from Apply link
                url_match = _ore.search(r'href="(https://[^"]+)"', tr)
                job_url = url_match.group(1) if url_match else ""
                # Extract season/date
                season = ""
                for td in tds:
                    s_match = _ore.search(r'(Fall|Spring|Winter|Summer)\s+(\d{4})', td)
                    if s_match:
                        season = f"{s_match.group(1)} {s_match.group(2)}"
                        break
                if company and title and job_url:
                    jobs.append({
                        "company": company,
                        "title": title,
                        "url": job_url,
                        "location": location,
                        "age": "0d",
                        "is_closed": False,
                        "source": source_name,
                        "github_category": "",
                        "_season": season,
                    })
            logging.info(f"{source_name}: Parsed {len(jobs)} jobs ({len([j for j in jobs if 'Fall 2026' in j.get('_season', '')])} Fall 2026)")
            return jobs
        except Exception as e:
            logging.error(f"simplify_offseason scrape failed: {e}")
            return []


if __name__ == "__main__":
    aggregator = UnifiedJobAggregator()
    aggregator.run()

