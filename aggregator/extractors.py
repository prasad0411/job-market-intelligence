#!/usr/bin/env python3

import requests
import base64
import pickle
import os
import json
import time
import random
import re
import logging
import threading
import atexit
from functools import lru_cache
from contextlib import contextmanager
from bs4 import BeautifulSoup

try:
    from selenium import webdriver
    from selenium.webdriver.chrome.service import Service
    from selenium.webdriver.chrome.options import Options
    from webdriver_manager.chrome import ChromeDriverManager
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC

    SELENIUM_AVAILABLE = True
except ImportError:
    SELENIUM_AVAILABLE = False

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

from aggregator.config import (
    USER_AGENTS,
    GMAIL_CREDS_FILE,
    GMAIL_TOKEN_FILE,
    GMAIL_SCOPES,
    JOB_BOARD_DOMAINS,
    JOBRIGHT_COOKIES_FILE,
    COMPANY_SLUG_MAPPING,
    URL_TO_COMPANY_MAPPING,
    PLATFORM_CONFIGS,
    get_state_for_city,
    validate_us_state_code,
    parse_date_flexible,
    DATEUTIL_AVAILABLE,
    PARSER_CHAIN,
    DEFAULT_PARSER,
    MAX_RETRIES,
    RETRY_DELAY_SECONDS,
    BACKOFF_MULTIPLIER,
    MAX_REASONABLE_AGE_DAYS,
    FAILED_SIMPLIFY_CACHE,
)

from aggregator.utils import PlatformDetector, CompanyNormalizer, CompanyValidator, DateParser
from aggregator.processors import (
    JobIDExtractor,
    LocationExtractor,
    CompanyExtractor,
    ValidationHelper,
)

_SESSION = requests.Session()
_SESSION.headers.update({"User-Agent": USER_AGENTS[0]})
_SELENIUM_DRIVER = None

# FIX 3: persist URL health cache to disk with 24-hour TTL
_URL_HEALTH_CACHE_FILE = os.path.join(".local", "url_health_cache.json")
_URL_HEALTH_CACHE_TTL = 86400  # 24 hours

def _load_url_health_cache():
    try:
        if os.path.exists(_URL_HEALTH_CACHE_FILE):
            raw = json.load(open(_URL_HEALTH_CACHE_FILE))
            now = time.time()
            return {k: v for k, v in raw.items()
                    if now - v.get("ts", 0) < _URL_HEALTH_CACHE_TTL}
    except Exception:
        pass
    return {}

def _save_url_health_cache(cache):
    try:
        os.makedirs(".local", exist_ok=True)
        if len(cache) > 2000:
            sorted_items = sorted(cache.items(), key=lambda x: x[1].get("ts", 0))
            cache = dict(sorted_items[-2000:])
        # open(path,"w") truncates before dump runs: a crash or a second
        # writer mid-dump left a half-written file. Same bug the comment
        # at _save_http_response_cache already documents.
        from aggregator.atomic_json import write_json as _wj
        _wj(_URL_HEALTH_CACHE_FILE, cache, indent=None)
    except Exception as _sw:
        from aggregator.swallowed import swallow as _s; _s('cache.url_health_write', _sw)

_URL_HEALTH_CACHE = _load_url_health_cache()
_SELENIUM_LAST_USED = None

# FIX 8: persist HTTP response cache to disk with 6-hour TTL
_HTTP_CACHE_FILE = os.path.join(".local", "http_response_cache.json")
_HTTP_CACHE_TTL = 6 * 3600  # 6 hours

def _load_http_cache():
    """Load HTTP cache — keep all entries, check TTL per-entry at lookup time."""
    try:
        if os.path.exists(_HTTP_CACHE_FILE):
            return json.load(open(_HTTP_CACHE_FILE))
    except Exception:
        pass
    return {}

def _http_cache_get(url):
    """Get cached response only if not expired. Returns None if missing or stale."""
    entry = _HTTP_RESPONSE_CACHE.get(url)
    if not entry:
        return None
    if time.time() - entry.get("ts", 0) > _HTTP_CACHE_TTL:
        del _HTTP_RESPONSE_CACHE[url]
        return None
    return entry

def _save_http_cache(cache):
    try:
        os.makedirs(".local", exist_ok=True)
        # Keep max 500 entries — evict oldest
        if len(cache) > 500:
            sorted_items = sorted(cache.items(), key=lambda x: x[1].get("ts", 0))
            cache = dict(sorted_items[-500:])
        # Only persist what is serializable. The cache holds a live response
        # object per entry, which json cannot encode - so json.dump raised on
        # EVERY run. And open(path,"w") truncates before dump runs, so the
        # failure left the file truncated: it has been destroying itself once
        # per run since it was wired up. Write atomically and store only the
        # html and url.
        _safe = {}
        for _k, _v in cache.items():
            if not isinstance(_v, dict):
                continue
            _safe[_k] = {
                "page_source": _v.get("page_source") or "",
                "final_url": _v.get("final_url") or _k,
                "ts": _v.get("ts", 0),
            }
        import tempfile as _tf
        _fd, _tmp = _tf.mkstemp(dir=".local", suffix=".tmp")
        try:
            with os.fdopen(_fd, "w", encoding="utf-8") as _f:
                json.dump(_safe, _f)
            os.replace(_tmp, _HTTP_CACHE_FILE)
        except Exception:
            if os.path.exists(_tmp):
                os.remove(_tmp)
            raise
    except Exception as _sw:
        from aggregator.swallowed import swallow as _s; _s('cache.http_response_write', _sw)

_HTTP_RESPONSE_CACHE = _load_http_cache()

# Module-level Simplify method cache — loaded once, not per resolve() call
_SIMPLIFY_METHOD_CACHE_FILE = os.path.join(".local", "simplify_method_cache.json")
_SIMPLIFY_METHOD_CACHE = {}

def _load_simplify_method_cache():
    global _SIMPLIFY_METHOD_CACHE
    try:
        if os.path.exists(_SIMPLIFY_METHOD_CACHE_FILE):
            _SIMPLIFY_METHOD_CACHE = json.load(open(_SIMPLIFY_METHOD_CACHE_FILE))
    except Exception:
        _SIMPLIFY_METHOD_CACHE = {}

def _save_simplify_method_cache():
    try:
        from aggregator.atomic_json import write_json as _wj
        _wj(_SIMPLIFY_METHOD_CACHE_FILE, _SIMPLIFY_METHOD_CACHE, indent=None)
    except Exception as _sw:
        from aggregator.swallowed import swallow as _s; _s('cache.simplify_method_write', _sw)

_load_simplify_method_cache()


def _cleanup_selenium_driver():
    global _SELENIUM_DRIVER
    if _SELENIUM_DRIVER:
        try:
            _SELENIUM_DRIVER.quit()
            logging.info("Selenium driver cleaned up")
        except Exception as _e:
            logging.debug("suppressed: %s", _e)
            pass
        _SELENIUM_DRIVER = None


atexit.register(_cleanup_selenium_driver)

_EMOJI_PATTERN = re.compile(
    r"[\U0001f600-\U0001f64f\U0001f300-\U0001f5ff\U0001f680-\U0001f6ff\U0001f1e0-\U0001f1ff]+",
    re.UNICODE,
)
def _sponsorship_from_row(row_text):
    """zapplyjobs publishes a Visa column on every row:
         '🏛 H-1B Co.'  -> company has sponsored H-1Bs before
         '✅ Sponsor'   -> explicitly sponsors
       Other repos use legend emoji in the title/company cell instead:
         '🛂'           -> does NOT offer sponsorship
         '🇺🇸'          -> requires US citizenship
       This is free signal we were already fetching and discarding, and it is
       the fastest way to fill the Sponsorship column without scraping pages.
    """
    if not row_text:
        return "Unknown"
    t = row_text
    if "\U0001F6C2" in t:            # 🛂 does NOT sponsor
        return "No"
    if "\U0001F1FA\U0001F1F8" in t:  # 🇺🇸 US citizenship required
        return "No"
    low = t.lower()
    if "sponsor" in low and "not" not in low and "no sponsor" not in low:
        return "Yes"
    if "h-1b" in low or "h1b" in low:
        return "Yes"
    return "Unknown"


_HEADER_PATTERN = re.compile(
    r"Company.*(?:Role|Position|Job.?Title).*Location.*(?:Application|Link|Posting|Posted|Date|Age|Apply|Model|Visa)", re.I
)
_HTML_LINK_PATTERN = re.compile(r'<a\s+href="(https?://[^"]+)"')
_MD_LINK_PATTERN = re.compile(r"\[.*?\]\((https?://[^\)]+)\)")

STRICT_JOB_BOARDS = [
    "myworkdayjobs.com",
    "wd1.myworkdayjobs",
    "wd3.myworkdayjobs",
    "wd5.myworkdayjobs",
    "wd10.myworkdayjobs",
    "wd12.myworkdayjobs",
    "greenhouse.io",
    "boards.greenhouse.io",
    "job-boards.greenhouse.io",
    "lever.co",
    "jobs.lever.co",
    "smartrecruiters.com",
    "jobs.smartrecruiters.com",
    "ashbyhq.com",
    "jobs.ashbyhq.com",
    "icims.com",
    "workable.com",
    "apply.workable.com",
    "amazon.jobs",
    "jobs.ea.com",
    "breezy.hr",
    "applytojob.com",
]


def safe_parse_html(html_content, preferred_parser=None):
    parsers_to_try = PARSER_CHAIN.copy()
    if preferred_parser and preferred_parser in parsers_to_try:
        parsers_to_try.remove(preferred_parser)
        parsers_to_try.insert(0, preferred_parser)
    for parser in parsers_to_try:
        try:
            soup = BeautifulSoup(html_content, parser)
            return soup, parser
        except Exception as e:
            logging.debug(f"Parser {parser} failed: {e}")
            continue
    logging.error(f"All parsers failed")
    return None, None


# ── Politeness: per-domain rate limit so we never hammer one ATS ──
_DOMAIN_LAST_HIT = {}
_DOMAIN_LOCK = threading.Lock()
_MIN_DOMAIN_INTERVAL = 1.0  # seconds between requests to the SAME domain


def _polite_wait(url):
    """Sleep just enough that we hit any single domain at most 1x/sec."""
    try:
        from urllib.parse import urlparse
        host = (urlparse(url).netloc or "").lower()
        if not host:
            return
        with _DOMAIN_LOCK:
            last = _DOMAIN_LAST_HIT.get(host, 0.0)
            wait = _MIN_DOMAIN_INTERVAL - (time.time() - last)
            if wait > 0:
                time.sleep(wait)
            _DOMAIN_LAST_HIT[host] = time.time()
    except Exception:
        pass


# ── Permanent "already fetched" cache: never re-fetch a job page ──
_FETCHED_URLS_FILE = os.path.join(".local", "fetched_urls.json")
_FETCHED_URLS = None
_FETCHED_LOCK = threading.Lock()


def _load_fetched_urls():
    global _FETCHED_URLS
    if _FETCHED_URLS is None:
        try:
            with open(_FETCHED_URLS_FILE) as f:
                _FETCHED_URLS = set(json.load(f))
        except Exception:
            _FETCHED_URLS = set()
    return _FETCHED_URLS


def already_fetched(url):
    """True if this job page was fetched in a previous run."""
    return url in _load_fetched_urls()


def mark_fetched(url):
    with _FETCHED_LOCK:
        _load_fetched_urls().add(url)


def save_fetched_urls():
    """Persist the fetched-URL set (call at end of run)."""
    try:
        os.makedirs(os.path.dirname(_FETCHED_URLS_FILE), exist_ok=True)
        with _FETCHED_LOCK:
            data = list(_load_fetched_urls())
        tmp = _FETCHED_URLS_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(data, f)
        os.replace(tmp, _FETCHED_URLS_FILE)
    except Exception as e:
        logging.debug(f"save_fetched_urls failed: {e}")


def retry_request(url, method="GET", max_retries=MAX_RETRIES, **kwargs):
    _polite_wait(url)
    for attempt in range(max_retries):
        try:
            if method.upper() == "GET":
                response = _SESSION.get(url, timeout=20, **kwargs)
            elif method.upper() == "HEAD":
                response = _SESSION.head(url, timeout=5, **kwargs)
            else:
                response = _SESSION.request(method, url, timeout=20, **kwargs)
            if response.status_code == 200:
                return response
            elif response.status_code in [403, 429]:
                time.sleep(RETRY_DELAY_SECONDS * (BACKOFF_MULTIPLIER**attempt))
            else:
                logging.warning(f"HTTP {response.status_code} for {url}")
                return response
        except Exception as _e:
            logging.debug("suppressed: %s", _e)
            time.sleep(RETRY_DELAY_SECONDS * (BACKOFF_MULTIPLIER**attempt))
    return None


class SimplifyRedirectResolver:
    _github_readme_cache = None
    _github_readme_fetch_time = None

    @staticmethod
    def load_failed_cache():
        if os.path.exists(FAILED_SIMPLIFY_CACHE):
            try:
                with open(FAILED_SIMPLIFY_CACHE, "r") as f:
                    return json.load(f)
            except Exception as _e:
                logging.debug("suppressed: %s", _e)
                return {}
        return {}

    @staticmethod
    def save_failed_cache(cache):
        try:
            with open(FAILED_SIMPLIFY_CACHE, "w") as f:
                json.dump(cache, f, indent=2)
        except Exception as _e:
            logging.debug("suppressed: %s", _e)
            pass

    _success_cache = {}  # Only cache successful resolutions

    @staticmethod
    def resolve(simplify_url):
        if "simplify.jobs/p/" not in simplify_url.lower():
            return simplify_url, False
        job_id_match = re.search(r"/p/([a-f0-9-]+)", simplify_url)
        if not job_id_match:
            return simplify_url, False
        job_id = job_id_match.group(1)
        # Check success cache first (only successes are cached — never failures)
        if job_id in SimplifyRedirectResolver._success_cache:
            cached_url = SimplifyRedirectResolver._success_cache[job_id]
            logging.debug(f"Simplify success cache hit: {job_id}")
            return cached_url, True
        failed_cache = SimplifyRedirectResolver.load_failed_cache()
        import time as _time
        now_ts = _time.time()
        cached_entry = failed_cache.get(job_id)
        if cached_entry:
            # Support both old format (date string) and new format (timestamp)
            if isinstance(cached_entry, (int, float)):
                if now_ts - cached_entry < 28800:  # 8 hours
                    return simplify_url, False
            elif isinstance(cached_entry, str):
                # Legacy date format — retry if not today
                if cached_entry == _time.strftime("%Y-%m-%d"):
                    return simplify_url, False
        click_url = f"https://simplify.jobs/jobs/click/{job_id}"

        # Use module-level cache instead of reading JSON per call
        _method_cache = _SIMPLIFY_METHOD_CACHE

        def _record_best(method_num):
            try:
                _SIMPLIFY_METHOD_CACHE["_global_best"] = method_num
                _save_simplify_method_cache()
            except Exception:
                pass

        _preferred = _method_cache.get("_global_best")
        _tried = set()

        def _try(method_num, fn):
            _tried.add(method_num)
            result = fn()
            if result:
                _record_best(method_num)
            return result

        if _preferred == 1:
            actual_url = _try(1, lambda: SimplifyRedirectResolver._method_1_http_redirect(click_url))
            if actual_url:
                logging.info(f"Simplify HTTP (preferred): {actual_url[:70]}")
                SimplifyRedirectResolver._success_cache[job_id] = actual_url
                return actual_url, True
        elif _preferred == 2:
            actual_url = _try(2, lambda: SimplifyRedirectResolver._method_2_selenium_click(click_url))
            if actual_url:
                logging.info(f"Simplify Selenium (preferred): {actual_url[:70]}")
                SimplifyRedirectResolver._success_cache[job_id] = actual_url
                return actual_url, True
        elif _preferred == 3:
            actual_url = _try(3, lambda: SimplifyRedirectResolver._method_3_api_fetch(job_id))
            if actual_url:
                logging.info(f"Simplify API (preferred): {actual_url[:70]}")
                SimplifyRedirectResolver._success_cache[job_id] = actual_url
                return actual_url, True

        if 1 not in _tried:
            actual_url = _try(1, lambda: SimplifyRedirectResolver._method_1_http_redirect(click_url))
            if actual_url:
                logging.info(f"Simplify HTTP: {actual_url[:70]}")
                SimplifyRedirectResolver._success_cache[job_id] = actual_url
                return actual_url, True

        if 2 not in _tried:
            actual_url = _try(2, lambda: SimplifyRedirectResolver._method_2_selenium_click(click_url))
            if actual_url:
                logging.info(f"Simplify Selenium: {actual_url[:70]}")
                SimplifyRedirectResolver._success_cache[job_id] = actual_url
                return actual_url, True

        if 3 not in _tried:
            actual_url = _try(3, lambda: SimplifyRedirectResolver._method_3_api_fetch(job_id))
            if actual_url:
                logging.info(f"Simplify API: {actual_url[:70]}")
                SimplifyRedirectResolver._success_cache[job_id] = actual_url
                return actual_url, True

        actual_url = SimplifyRedirectResolver._method_4_github_lookup(job_id)
        if actual_url:
            logging.info(f"Simplify GitHub: {actual_url[:70]}")
            SimplifyRedirectResolver._success_cache[job_id] = actual_url
            return actual_url, True

        actual_url = SimplifyRedirectResolver._method_5_page_apply_button(simplify_url)
        if actual_url == "__INACTIVE__":
            logging.info(f"Simplify job INACTIVE: {simplify_url[:60]}")
            return "__INACTIVE__", False
        if actual_url:
            logging.info(f"Simplify Page: {actual_url[:70]}")
            SimplifyRedirectResolver._success_cache[job_id] = actual_url
            return actual_url, True
        failed_cache[job_id] = time.time()
        SimplifyRedirectResolver.save_failed_cache(failed_cache)

        try:
            from outreach.brain import Brain
            Brain.get().queue_simplify_retry(job_id, simplify_url, "all_methods_failed")
        except Exception as _be:
            logging.debug(f"Brain Simplify queue failed: {_be}")

        logging.warning(f"All 5 methods failed: {simplify_url[:60]}")
        return simplify_url, False

    @staticmethod
    def _method_1_http_redirect(click_url):
        try:
            response = requests.get(
                click_url,
                allow_redirects=True,
                timeout=15,
                headers={"User-Agent": USER_AGENTS[0]},
            )

            if response and response.url != click_url:
                if SimplifyRedirectResolver._is_valid_job_url(response.url):
                    return response.url

            if response and 200 <= response.status_code < 400:
                from aggregator.extractors import safe_parse_html

                soup, _ = safe_parse_html(response.text)
                if soup:
                    meta_refresh = soup.find("meta", {"http-equiv": "refresh"})
                    if meta_refresh:
                        content = meta_refresh.get("content", "")
                        match = re.search(r"url=(.+)", content, re.I)
                        if match:
                            redirect_url = match.group(1).strip().strip('"').strip("'")
                            if SimplifyRedirectResolver._is_valid_job_url(redirect_url):
                                return redirect_url

            if response and response.status_code == 200:
                js_match = re.search(
                    r'window\.location(?:\.href)?\s*=\s*["\']([^"\']+)', response.text
                )
                if js_match:
                    redirect_url = js_match.group(1)
                    if SimplifyRedirectResolver._is_valid_job_url(redirect_url):
                        return redirect_url

        except Exception as _e:
            logging.debug("suppressed: %s", _e)
            pass
        return None

    @staticmethod
    def _method_2_selenium_click(click_url):
        global _SELENIUM_DRIVER

        if not SELENIUM_AVAILABLE:
            return None

        try:
            if _SELENIUM_DRIVER is None:
                chrome_options = Options()
                chrome_options.add_argument("--headless")
                chrome_options.add_argument(
                    "--disable-blink-features=AutomationControlled"
                )
                chrome_options.add_argument("--no-sandbox")
                chrome_options.add_argument("--disable-dev-shm-usage")
                chrome_options.add_argument(f"user-agent={USER_AGENTS[0]}")
                chrome_options.add_experimental_option(
                    "excludeSwitches", ["enable-logging"]
                )
                service = Service(ChromeDriverManager().install())
                _SELENIUM_DRIVER = webdriver.Chrome(
                    service=service, options=chrome_options
                )
                _SELENIUM_DRIVER.set_page_load_timeout(20)
                logging.info(
                    "Selenium driver initialized (SimplifyRedirectResolver will reuse)"
                )

            _SELENIUM_DRIVER.get(click_url)

            for wait_time in [5, 5, 5]:
                time.sleep(wait_time)
                current_url = _SELENIUM_DRIVER.current_url

                if current_url != click_url:
                    if SimplifyRedirectResolver._is_valid_job_url(current_url):
                        return current_url

                if "simplify.jobs" not in current_url:
                    if SimplifyRedirectResolver._is_valid_job_url(current_url):
                        return current_url

        except Exception as e:
            logging.debug(f"SimplifyRedirectResolver Selenium failed: {e}")
            if _SELENIUM_DRIVER:
                try:
                    _SELENIUM_DRIVER.quit()
                except Exception as _e:
                    logging.debug("suppressed: %s", _e)
                    pass
                _SELENIUM_DRIVER = None

        return None


    @staticmethod
    def _method_5_page_apply_button(simplify_url):
        """ENHANCED: Parse Simplify page for Apply URL + detect INACTIVE status."""
        try:
            response = requests.get(
                simplify_url,
                timeout=15,
                headers={"User-Agent": USER_AGENTS[0]},
            )
            if not response or response.status_code != 200:
                return None

            text = response.text

            # Check for INACTIVE status in page text
            if "INACTIVE" in text and ("Save" in text[:5000] or "Overview" in text[:5000]):
                logging.info(f"Simplify INACTIVE detected: {simplify_url[:60]}")
                return "__INACTIVE__"

            soup, _ = safe_parse_html(text)
            if not soup:
                return None

            # Extract rich metadata from Simplify page
            try:
                page_text = soup.get_text(separator=" ")[:8000]
                import re as _sre
                meta = {}

                # Try __NEXT_DATA__ first for structured metadata
                _nd = soup.find("script", {"id": "__NEXT_DATA__"})
                if _nd and _nd.string:
                    try:
                        import json as _mj
                        _nd_data = _mj.loads(_nd.string)
                        _props = _nd_data.get("props", {}).get("pageProps", {})
                        _job = _props.get("job", _props.get("jobPosting", {}))
                        if _job:
                            # Location from structured data
                            _loc = (_job.get("location") or _job.get("city", "") or
                                    _job.get("jobLocation", {}).get("address", {}).get("addressLocality", ""))
                            _state = (_job.get("state") or
                                      _job.get("jobLocation", {}).get("address", {}).get("addressRegion", ""))
                            if _loc and _state:
                                meta["location"] = f"{_loc}, {_state}"
                            elif _loc:
                                meta["location"] = _loc
                            # Salary from structured data
                            _sal = (_job.get("salary") or _job.get("baseSalary", {}) or
                                    _job.get("compensation", ""))
                            if isinstance(_sal, dict):
                                _min = _sal.get("minValue") or _sal.get("min", "")
                                if _min:
                                    meta["salary_min"] = float(str(_min).replace("$","").replace(",","").strip())
                            elif isinstance(_sal, str) and "$" in _sal:
                                _sm = _sre.search(r'\$(\d+(?:\.\d+)?)', _sal)
                                if _sm:
                                    meta["salary_min"] = float(_sm.group(1))
                            # Sponsorship signal
                            _tags = _job.get("tags", []) or _job.get("recommendationTags", [])
                            if any("no h1b" in str(t).lower() or "no_h1b" in str(t).lower() for t in _tags):
                                meta["no_h1b"] = True
                            # Work type
                            _wt = (_job.get("workModel") or _job.get("workplaceType", "")).lower()
                            if "remote" in _wt:
                                meta["remote"] = "Remote"
                            elif "hybrid" in _wt:
                                meta["remote"] = "Hybrid"
                            elif "onsite" in _wt or "on_site" in _wt or "in_person" in _wt:
                                meta["remote"] = "On Site"
                    except Exception:
                        pass

                # Fallback: regex from page text
                if not meta.get("location"):
                    for _lp in [
                        r"(?:In Person|Hybrid|Remote)\s+([\w\s]+,\s*[A-Z]{2}),?\s*USA",
                        r"([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*,\s*[A-Z]{2},\s*USA)",
                        r"([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*,\s*[A-Z]{2})\s+(?:Hybrid|Remote|In Person|On Site)",
                        r"employees\s+([\w\s]+,\s*[A-Z]{2},\s*USA)",
                        r"([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*),\s+([A-Z]{2})\s+(?:USA)?",
                    ]:
                        _lm = _sre.search(_lp, page_text)
                        if _lm:
                            _lr = _lm.group(1).strip()
                            _lr = _sre.sub(r",?\s*USA\s*$", "", _lr).strip()
                            if _lr and len(_lr) > 3:
                                meta["location"] = _lr
                                break

                # Remote fallback from page text
                if not meta.get("remote"):
                    _pt3 = page_text[:3000]
                    if "In Person" in _pt3:
                        meta["remote"] = "On Site"
                    elif "Hybrid" in _pt3:
                        meta["remote"] = "Hybrid"
                    elif "Remote" in _pt3:
                        meta["remote"] = "Remote"

                # Salary fallback from page text
                if not meta.get("salary_min"):
                    for _sp in [
                        r'\$(\d+(?:\.\d+)?)\s*/\s*hr',
                        r'Compensation Overview\s+\$(\d+(?:\.\d+)?)',
                        r'Pay Range[:\s]+\$(\d+(?:\.\d+)?)',
                        r'\$(\d+(?:\.\d+)?)\s*(?:USD)?\s*/\s*hour',
                    ]:
                        _sm = _sre.search(_sp, page_text, _sre.I)
                        if _sm:
                            try:
                                _sv = float(_sm.group(1))
                                if 1 < _sv < 200:
                                    meta["salary_min"] = _sv
                                    break
                            except Exception:
                                pass

                # No H1B fallback
                if not meta.get("no_h1b"):
                    if "No H1B" in page_text[:3000] or "no h1b" in page_text[:3000].lower():
                        meta["no_h1b"] = True

                            # WARNING: shared class state — unreliable with concurrent threads
                SimplifyRedirectResolver._last_metadata = meta
                if meta.get("location"):
                    logging.info(f"Simplify metadata: location={meta['location']}, remote={meta.get('remote','?')}, salary_min={meta.get('salary_min','?')}")
            except Exception as e:
                logging.debug(f"Simplify metadata extraction failed: {e}")

            # Strategy 1: Extract from __NEXT_DATA__ JSON (Next.js app)
            next_data = soup.find("script", {"id": "__NEXT_DATA__"})
            if next_data and next_data.string:
                try:
                    import json as _json
                    data = _json.loads(next_data.string)
                    # Navigate common Next.js data paths
                    props = data.get("props", {}).get("pageProps", {})
                    # Try various keys where the external URL might live
                    for key in ["externalUrl", "applyUrl", "jobUrl", "url", "applicationUrl", "externalApplyUrl"]:
                        url = props.get(key) or props.get("job", {}).get(key, "")
                        if url and url.startswith("http") and "simplify" not in url.lower():
                            logging.info(f"Simplify __NEXT_DATA__ [{key}]: {url[:80]}")
                            return url
                    # Try nested job object
                    job = props.get("job", props.get("listing", props.get("posting", {})))
                    if isinstance(job, dict):
                        for key in ["externalUrl", "applyUrl", "url", "applicationUrl", "sourceUrl", "originalUrl"]:
                            url = job.get(key, "")
                            if url and url.startswith("http") and "simplify" not in url.lower():
                                logging.info(f"Simplify __NEXT_DATA__ job.{key}: {url[:80]}")
                                return url
                except Exception as e:
                    logging.debug(f"Simplify __NEXT_DATA__ parse failed: {e}")

            # Strategy 2: Find Apply link pointing to external job board
            for link in soup.find_all("a", href=True):
                href = link.get("href", "")
                link_text = link.get_text(strip=True).lower()
                # Apply button pointing to external site
                if ("apply" in link_text or "apply" in link.get("class", [""])) and href.startswith("http") and "simplify" not in href.lower():
                    logging.info(f"Simplify Apply link: {href[:80]}")
                    return href

            # Strategy 3: Find any job board URL in the page source
            job_board_patterns = [
                r'https?://jobs\.lever\.co/[^\s<>"\']+',
                r'https?://[a-z0-9-]+\.myworkdayjobs\.com/[^\s<>"\']+',
                r'https?://(?:boards|job-boards)\.(?:eu\.)?greenhouse\.io/[^\s<>"\']+',
                r'https?://[a-z0-9-]+\.ashbyhq\.com/[^\s<>"\']+',
                r'https?://jobs\.smartrecruiters\.com/[^\s<>"\']+',
                r'https?://[a-z0-9-]+\.icims\.com/[^\s<>"\']+',
                r'https?://[a-z0-9-]+\.eightfold\.ai/[^\s<>"\']+',
                r'https?://[a-z0-9-]+\.fa\.[a-z0-9]+\.oraclecloud\.com/[^\s<>"\']+',
                r'https?://[a-z0-9-]+\.wd\d+\.myworkdayjobs\.com/[^\s<>"\']+',
            ]
            for pattern in job_board_patterns:
                matches = re.findall(pattern, text)
                for match in matches:
                    clean = match.rstrip('"').rstrip("'").rstrip("\\").rstrip(")")
                    if any(kw in clean.lower() for kw in ["/job/", "/jobs/", "/external/", "/apply", "/career"]):
                        logging.info(f"Simplify page board URL: {clean[:80]}")
                        return clean

            # Strategy 4: Any external URL that looks like a career page
            for link in soup.find_all("a", href=True):
                href = link.get("href", "")
                if not href.startswith("http") or "simplify" in href.lower():
                    continue
                if any(board in href.lower() for board in [
                    "myworkdayjobs.com", "greenhouse.io", "lever.co", "ashbyhq.com",
                    "smartrecruiters.com", "icims.com", "oraclecloud.com", "eightfold.ai",
                    "workable.com", "breezy.hr", "bamboohr.com", "jobvite.com",
                ]):
                    logging.info(f"Simplify page external board link: {href[:80]}")
                    return href

        except Exception as e:
            logging.debug(f"Simplify page parse failed: {e}")
        return None

    @staticmethod
    def _is_valid_job_url(url):
        if not url or not url.startswith("http"):
            return False
        url_lower = url.lower()
        if "simplify.jobs" in url_lower:
            return False
        for board in STRICT_JOB_BOARDS:
            if board in url_lower:
                must_have = (
                    "/job/" in url_lower
                    or "/jobs/" in url_lower
                    or "/external/" in url_lower
                    or "/embed/" in url_lower
                    or "token=" in url_lower
                )
                if must_have:
                    reject = [
                        "/news/",
                        "/blog/",
                        "/press/",
                        "/article/",
                        "/accessibility",
                        "/privacy",
                        "/canada",
                        "/introduceyourself",
                        "/rewards",
                        "/wellness",
                        "/diversity",
                        "/inclusion",
                        "/about",
                        "/contact",
                    ]
                    if not any(pattern in url_lower for pattern in reject):
                        return True
        return False

    @staticmethod
    def _method_3_api_fetch(job_id):
        try:
            api_url = f"https://simplify.jobs/api/jobs/{job_id}"
            response = requests.get(
                api_url,
                timeout=10,
                headers={"User-Agent": USER_AGENTS[0]},
            )

            if response and response.status_code == 200:
                try:
                    data = response.json()
                    if "url" in data or "jobUrl" in data or "link" in data:
                        actual_url = (
                            data.get("url") or data.get("jobUrl") or data.get("link")
                        )
                        if SimplifyRedirectResolver._is_valid_job_url(actual_url):
                            return actual_url
                except Exception as _e:
                    logging.debug("suppressed: %s", _e)
                    pass

        except Exception as _e:
            logging.debug("suppressed: %s", _e)
            pass
        return None

    @staticmethod
    def _method_4_github_lookup(job_id):
        try:
            import time

            current_time = time.time()

            if (
                SimplifyRedirectResolver._github_readme_cache is None
                or SimplifyRedirectResolver._github_readme_fetch_time is None
                or current_time - SimplifyRedirectResolver._github_readme_fetch_time
                > 600
            ):

                readme_url = "https://raw.githubusercontent.com/SimplifyJobs/Summer2026-Internships/master/README.md"
                response = requests.get(readme_url, timeout=15)

                if response and response.status_code == 200:
                    SimplifyRedirectResolver._github_readme_cache = response.text
                    SimplifyRedirectResolver._github_readme_fetch_time = current_time
                else:
                    return None

            readme_text = SimplifyRedirectResolver._github_readme_cache
            if readme_text and job_id in readme_text:
                lines = readme_text.split("\n")
                for line in lines:
                    if job_id in line:
                        match = re.search(r"https?://[^\s\)]+", line)
                        if match:
                            url = match.group(0)
                            if "simplify.jobs" not in url and job_id not in url:
                                if SimplifyRedirectResolver._is_valid_job_url(url):
                                    return url
        except Exception as _e:
            logging.debug("suppressed: %s", _e)
            pass
        return None


class JobrightRedirectResolver:
    """NEW: Resolves Jobright tracking URLs to actual job URLs"""

    _email_html_cache = {}


    @staticmethod
    def resolve(jobright_url, email_html=None):
        """
        Resolve Jobright tracking URL to actual job URL
        Returns: (actual_url, success_boolean)
        """
        if "jobright.ai" not in jobright_url.lower():
            return jobright_url, False
        # Freshness check: warn if cookies are > 30 days old
        try:
            import os as _os, time as _t
            _cf = _os.path.join(_os.path.dirname(_os.path.dirname(
                _os.path.abspath(__file__))), ".local", "jobright_cookies.json")
            if _os.path.exists(_cf):
                age_days = (_t.time() - _os.path.getmtime(_cf)) / 86400
                if age_days > 30:
                    logging.warning(
                        f"⚠ Jobright cookies are {age_days:.0f} days old "
                        f"(>30) — Jobright may fail silently. "
                        f"Refresh by running: python3 -m aggregator --refresh-jobright"
                    )
        except Exception:
            pass

        job_id = JobrightRedirectResolver._extract_job_id(jobright_url)
        if not job_id:
            logging.debug("Jobright: No job ID found in URL")
            return jobright_url, False

        actual_url = JobrightRedirectResolver._method_1_email_html(job_id, email_html)
        if actual_url:
            logging.info(f"Jobright HTTP: {actual_url[:80]}")
            return actual_url, True

        actual_url = JobrightRedirectResolver._method_2_http_fetch(jobright_url)
        if actual_url:
            logging.info(f"Jobright HTTP: {actual_url[:80]}")
            return actual_url, True

        actual_url = JobrightRedirectResolver._method_3_selenium(jobright_url)
        if actual_url:
            logging.info(f"Jobright Selenium: {actual_url[:80]}")
            return actual_url, True

        actual_url = JobrightRedirectResolver._method_4_authenticated(
            jobright_url, job_id
        )
        if actual_url:
            logging.info(f"Jobright Auth API: {actual_url[:80]}")
            return actual_url, True

        logging.warning(f"Jobright resolution failed: {jobright_url}")
        return jobright_url, False

    @staticmethod
    def _extract_job_id(url):
        """Extract job ID from Jobright URL"""
        match = re.search(r"jobright\.ai/jobs/info/([a-f0-9]+)", url, re.I)
        if match:
            return match.group(1)
        return None

    @staticmethod
    def _method_1_email_html(job_id, email_html):
        """ENHANCED: Extract actual URL from email HTML with multiple strategies"""
        if not email_html:
            return None

        try:
            from bs4 import BeautifulSoup

            soup = BeautifulSoup(email_html, "html.parser")

            all_links = []
            for link in soup.find_all("a", href=True):
                href = link["href"]
                all_links.append(href)

                if job_id in href:
                    continue

                valid_domains = [
                    ".myworkdayjobs.com",
                    "greenhouse.io",
                    "lever.co",
                    "ashbyhq.com",
                    "smartrecruiters.com",
                    "icims.com",
                    "taleo.net",
                    "ultipro.com",
                    "workable.com",
                    "breezy.hr",
                    "bamboohr.com",
                    "jobvite.com",
                ]

                if any(domain in href for domain in valid_domains):
                    if "/job/" in href or "/jobs/" in href or "/career" in href:
                        logging.debug(f"Jobright email HTML: Found {href[:80]}")
                        return href

            for link_href in all_links:
                if (
                    link_href.startswith("http")
                    and "jobright" not in link_href
                    and "linkedin" not in link_href
                ):
                    if any(
                        x in link_href
                        for x in ["apply", "career", "position", "job", "requisition"]
                    ):
                        logging.debug(f"Jobright email HTML fallback: {link_href[:80]}")
                        return link_href

        except Exception as e:
            logging.debug(f"Jobright email HTML extraction failed: {e}")

        return None

    @staticmethod
    def _method_2_http_fetch(jobright_url):
        """ENHANCED: Fetch Jobright page and extract actual URL with multiple strategies"""
        try:
            response = retry_request(jobright_url, max_retries=2)
            if not response:
                return None

            from bs4 import BeautifulSoup

            soup = BeautifulSoup(response.content, "html.parser")

            script_tags = soup.find_all("script")
            for script in script_tags:
                if script.string:
                    url_matches = re.findall(
                        r'https?://[^\s"\'<>]+(?:myworkdayjobs|greenhouse|lever|ashby|icims|smartrecruiters)[^\s"\'<>]+',
                        script.string,
                    )
                    for url_match in url_matches:
                        if "job" in url_match.lower():
                            logging.debug(
                                f"Jobright script extraction: {url_match[:80]}"
                            )
                            return url_match

            apply_links = soup.find_all(
                "a", {"class": lambda x: x and "apply" in str(x).lower()}
            )
            for link in apply_links:
                href = link.get("href", "")
                if href and href.startswith("http") and "jobright.ai" not in href:
                    logging.debug(f"Jobright apply button: {href[:80]}")
                    return href

            for link in soup.find_all("a", href=True):
                href = link["href"]
                if "jobright.ai" in href or "linkedin.com" in href:
                    continue

                if any(
                    domain in href
                    for domain in [
                        ".myworkdayjobs.com",
                        "greenhouse.io",
                        "ashbyhq.com",
                        "icims.com",
                    ]
                ):
                    logging.debug(f"Jobright link scan: {href[:80]}")
                    return href

        except Exception as e:
            logging.debug(f"Jobright HTTP fetch failed: {e}")

        return None

    @staticmethod
    def _method_3_selenium(jobright_url):
        """ENHANCED: Use Selenium to click through and get final URL"""
        global _SELENIUM_DRIVER

        if not SELENIUM_AVAILABLE:
            return None

        try:
            if _SELENIUM_DRIVER is None:
                from selenium import webdriver
                from selenium.webdriver.chrome.service import Service
                from selenium.webdriver.chrome.options import Options
                from webdriver_manager.chrome import ChromeDriverManager

                chrome_options = Options()
                chrome_options.add_argument("--headless")
                chrome_options.add_argument("--no-sandbox")
                chrome_options.add_argument("--disable-dev-shm-usage")
                service = Service(ChromeDriverManager().install())
                _SELENIUM_DRIVER = webdriver.Chrome(
                    service=service, options=chrome_options
                )
                _SELENIUM_DRIVER.set_page_load_timeout(25)

            _SELENIUM_DRIVER.get(jobright_url)
            time.sleep(5)

            try:
                from selenium.webdriver.common.by import By
                from selenium.webdriver.support.ui import WebDriverWait
                from selenium.webdriver.support import expected_conditions as EC

                apply_button = WebDriverWait(_SELENIUM_DRIVER, 10).until(
                    EC.presence_of_element_located(
                        (
                            By.XPATH,
                            "//a[contains(text(), 'Apply') or contains(@class, 'apply')]",
                        )
                    )
                )

                apply_url = apply_button.get_attribute("href")
                if apply_url and "jobright.ai" not in apply_url:
                    logging.debug(f"Jobright Selenium button click: {apply_url[:80]}")
                    return apply_url

            except Exception as _e:
                logging.debug("suppressed: %s", _e)
                pass

            current_url = _SELENIUM_DRIVER.current_url
            if current_url != jobright_url and "jobright.ai" not in current_url:
                logging.debug(f"Jobright Selenium redirect: {current_url[:80]}")
                return current_url

        except Exception as e:
            logging.debug(f"Jobright Selenium failed: {e}")

        return None

    @staticmethod
    def _method_4_authenticated(jobright_url, job_id):
        """NEW: Try authenticated request to Jobright API"""
        try:
            import json
            import os

            cookies_file = "jobright_cookies.json"
            if not os.path.exists(cookies_file):
                return None

            with open(cookies_file, "r") as f:
                cookies = json.load(f)

            session = requests.Session()
            for cookie in cookies:
                session.cookies.set(
                    cookie["name"],
                    cookie["value"],
                    domain=cookie.get("domain", "jobright.ai"),
                )

            api_url = f"https://jobright.ai/api/jobs/{job_id}"
            response = session.get(api_url, timeout=10)

            if response.status_code == 200:
                data = response.json()
                if "jobUrl" in data:
                    logging.debug(f"Jobright auth API: {data['jobUrl'][:80]}")
                    return data["jobUrl"]
                elif "url" in data:
                    return data["url"]

        except Exception as e:
            logging.debug(f"Jobright authenticated API failed: {e}")

        return None


class JobrightAuthenticator:
    def __init__(self):
        self.cookies = None
        self.session = requests.Session()
        self.load_cookies()

    def load_cookies(self):
        if os.path.exists(JOBRIGHT_COOKIES_FILE):
            try:
                with open(JOBRIGHT_COOKIES_FILE, "r") as f:
                    self.cookies = json.load(f)
                    for cookie in self.cookies:
                        self.session.cookies.set(cookie["name"], cookie["value"])
                logging.info(f"Loaded {len(self.cookies)} Jobright cookies")
            except Exception as e:
                logging.error(f"Failed to load Jobright cookies: {e}")

    def _cookies_are_fresh(self) -> bool:
        """Check if Jobright cookies are still valid (< 6 hours old)."""
        import time as _t, os as _os
        cookie_file = _os.path.join(
            _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))),
            ".local", "jobright_cookies.json"
        )
        if not _os.path.exists(cookie_file):
            return False
        try:
            age = _t.time() - _os.path.getmtime(cookie_file)
            return age < 6 * 3600  # fresh if < 6 hours
        except Exception:
            return False

    def login_interactive(self):
        if not SELENIUM_AVAILABLE:
            logging.warning("Selenium not available")
            return False
        print("\n" + "=" * 60)
        print("JOBRIGHT AUTHENTICATION")
        print("=" * 60)
        with self._get_driver() as driver:
            try:
                driver.get("https://jobright.ai")
                time.sleep(3)
                print("[AUTH] Please log in through the browser window")
                print("       Press ENTER after completing login...")
                input()
                cookies = driver.get_cookies()
                if not cookies:
                    print("✗ No cookies captured")
                    return False
                self.cookies = cookies
                with open(JOBRIGHT_COOKIES_FILE, "w") as f:
                    json.dump(cookies, f, indent=2)
                for cookie in cookies:
                    self.session.cookies.set(cookie["name"], cookie["value"])
                print(f"✓ Authentication successful ({len(cookies)} cookies saved)\n")
                return True
            except Exception as e:
                logging.error(f"Authentication failed: {e}")
                print(f"✗ Authentication failed: {e}")
                return False

    def resolve_jobright_url(self, jobright_url):
        if "jobright.ai/jobs/info/" not in jobright_url.lower():
            return jobright_url, False
        if not self.cookies:
            return jobright_url, False
        try:
            response = retry_request(
                jobright_url, headers={"User-Agent": USER_AGENTS[0]}
            )
            if not response or response.status_code != 200:
                return jobright_url, False
            soup, _ = safe_parse_html(response.content)
            if not soup:
                return jobright_url, False
            script_tag = soup.find("script", {"id": "__NEXT_DATA__"})
            if not script_tag:
                return jobright_url, False
            data = json.loads(script_tag.string)
            job_result = (
                data.get("props", {})
                .get("pageProps", {})
                .get("dataSource", {})
                .get("jobResult", {})
            )
            actual_url = job_result.get("applyLink") or job_result.get("originalUrl")
            is_company_site = job_result.get("isCompanySiteLink", False)

            if not actual_url or "jobright.ai" in actual_url:
                try:
                    origin_link = soup.find("a", class_=re.compile(r"index_origin"))

                    if not origin_link:
                        origin_link = soup.find(
                            "a", string=re.compile(r"original\s+job\s+post", re.I)
                        )

                    if not origin_link:
                        for link in soup.find_all("a", href=True):
                            link_text = link.get_text().strip().lower()
                            if link_text and (
                                "original" in link_text or "job post" in link_text
                            ):
                                href = link.get("href")
                                if href and "jobright.ai" not in href:
                                    origin_link = link
                                    break

                    if origin_link:
                        html_url = origin_link.get("href")
                        if (
                            html_url
                            and html_url.startswith("http")
                            and "jobright.ai" not in html_url
                        ):
                            actual_url = html_url
                            is_company_site = True
                            logging.info(
                                f"Resolved Jobright URL via HTML to {actual_url[:70]}"
                            )

                except Exception as html_error:
                    logging.debug(f"HTML URL extraction failed: {html_error}")

            if actual_url and "jobright.ai" not in actual_url:
                logging.info(f"Resolved Jobright URL to {actual_url[:70]}")
                return actual_url, is_company_site
            return jobright_url, False
        except Exception as e:
            logging.error(f"Failed to resolve Jobright URL {jobright_url}: {e}")
            return jobright_url, False

    @contextmanager
    def _get_driver(self):
        driver = None
        try:
            chrome_options = Options()
            chrome_options.add_argument("--disable-blink-features=AutomationControlled")
            chrome_options.add_experimental_option(
                "excludeSwitches", ["enable-logging"]
            )
            service = Service(ChromeDriverManager().install())
            driver = webdriver.Chrome(service=service, options=chrome_options)
            yield driver
        finally:
            if driver:
                try:
                    driver.quit()
                except Exception as _e:
                    logging.debug("suppressed: %s", _e)
                    pass


class EmailExtractor:
    def __init__(self):
        self.service = None

    def authenticate(self):
        creds = None
        if os.path.exists(GMAIL_TOKEN_FILE):
            try:
                with open(GMAIL_TOKEN_FILE, "rb") as token:
                    creds = pickle.load(token)
            except Exception as e:
                logging.warning(f"Corrupted token file: {e}")
                try:
                    os.remove(GMAIL_TOKEN_FILE)
                except Exception as _e:
                    logging.debug("suppressed: %s", _e)
                    pass
                creds = None
        if creds and not creds.valid:
            if creds.expired and creds.refresh_token:
                try:
                    creds.refresh(Request())
                except Exception as e:
                    logging.warning(f"Token refresh failed: {e}")
                    print("⚠️  Gmail token expired - re-authenticating...")
                    try:
                        os.remove(GMAIL_TOKEN_FILE)
                    except Exception as _e:
                        logging.debug("suppressed: %s", _e)
                        pass
                    creds = None
        if not creds:
            try:
                flow = InstalledAppFlow.from_client_secrets_file(
                    GMAIL_CREDS_FILE, GMAIL_SCOPES
                )
                creds = flow.run_local_server(port=0)
                with open(GMAIL_TOKEN_FILE, "wb") as token:
                    pickle.dump(creds, token)
                print("✓ Gmail authenticated successfully")
            except Exception as e:
                logging.error(f"Gmail authentication failed: {e}")
                print(f"✗ Gmail authentication failed: {e}")
                return False
        self.service = build("gmail", "v1", credentials=creds)
        return True

    def fetch_job_emails(self, max_results=100):
        if not self.service:
            # (auth silently)
            if not self.authenticate():
                return []
        if not self.service:
            print("✗ Gmail authentication failed")
            logging.error("Gmail service not initialized")
            return []
        try:
            results = (
                self.service.users()
                .messages()
                .list(
                    userId="me",
                    q='label:"Job Hunt" newer_than:3d',
                    maxResults=max_results,
                )
                .execute()
            )
            messages = results.get("messages", [])
            if not messages:
                logging.info("No labeled emails found")
                print("No emails with 'Job Hunt' label found")
                return []
            print(f"Found {len(messages)} labeled emails")
            emails_with_data = []
            for message in messages:
                try:
                    msg = (
                        self.service.users()
                        .messages()
                        .get(userId="me", id=message["id"], format="full")
                        .execute()
                    )
                    internal_date = int(msg.get("internalDate", 0))
                    email_id = message["id"]
                    headers = {
                        h["name"]: h["value"] for h in msg["payload"].get("headers", [])
                    }
                    sender = self._detect_sender(headers.get("From", ""))
                    subject = headers.get("Subject", "Unknown Subject")
                    html_content = self._extract_html(msg["payload"])
                    if html_content:
                        urls = self._extract_job_urls(html_content)
                        if urls:
                            emails_with_data.append(
                                {
                                    "email_id": email_id,
                                    "timestamp": internal_date,
                                    "sender": sender,
                                    "subject": subject,
                                    "html": html_content,
                                    "urls": urls,
                                }
                            )
                except Exception as e:
                    logging.error(f"Failed to process email: {e}")
                    continue
            emails_with_data.sort(key=lambda x: x["timestamp"], reverse=True)
            total_urls = sum(len(email["urls"]) for email in emails_with_data)
            print(f"Total: {total_urls} job URLs from {len(emails_with_data)} emails\n")
            return emails_with_data
        except Exception as e:
            logging.error(f"Gmail fetch error: {e}")
            print(f"✗ Gmail error: {e}")
            return []

    @staticmethod
    def _detect_sender(from_field):
        from_lower = from_field.lower()
        senders = {
            "ziprecruiter": "ZipRecruiter",
            "adzuna": "Adzuna",
            "swelist": "SWE List",
            "jobright": "Jobright",
            "fursah": "Fursah",
            "jobalerts-noreply@linkedin.com": "LinkedIn",
            "jobs-noreply@linkedin.com": "LinkedIn",
            "linkedin.com": "LinkedIn",
        }
        for key, value in senders.items():
            if key in from_lower:
                return value
        return "Email"

    @staticmethod
    def _extract_html(payload):
        if "parts" in payload:
            for part in payload["parts"]:
                if part["mimeType"] == "text/html":
                    try:
                        return base64.urlsafe_b64decode(
                            part["body"].get("data", "")
                        ).decode("utf-8")
                    except Exception as e:
                        logging.error(f"Failed to decode email part: {e}")
                        continue
        elif "body" in payload:
            html_data = payload["body"].get("data", "")
            if html_data:
                try:
                    return base64.urlsafe_b64decode(html_data).decode("utf-8")
                except Exception as e:
                    logging.error(f"Failed to decode email body: {e}")
        return None

    @staticmethod
    def _extract_job_urls(email_html):
        """Extract job URLs from email HTML.

        For SWE List emails (class="internship" paragraphs), extracts
        (url, company, title) tuples to preserve context.
        For all other emails, returns plain URL list.
        """
        soup, _ = safe_parse_html(email_html)
        if not soup:
            return []
        seen = set()
        urls = []

        # ── SWE List format: <p class="internship"><strong>Co:</strong><a>Title</a></p>
        swe_paragraphs = soup.find_all("p", class_="internship")
        if swe_paragraphs:
            for p in swe_paragraphs:
                link = p.find("a", href=True)
                strong = p.find("strong")
                if not link:
                    continue
                url = link.get("href", "")
                if not url.startswith("http") or EmailExtractor._is_non_job_url(url):
                    continue
                if url in seen:
                    continue
                seen.add(url)
                # Extract company from <strong> tag
                company_hint = ""
                title_hint = ""
                if strong:
                    company_hint = strong.get_text().strip().rstrip(":").strip()
                    # Validate: reject if company looks like a date/season/title
                    import re as _swe_re
                    _bad_company = (
                        _swe_re.match(r'^(?:Fall|Spring|Summer|Winter)\s+20\d{2}$', company_hint, _swe_re.I)
                        or _swe_re.match(r'^(?:Visiting|Senior|Junior|Lead|Staff)\s+', company_hint, _swe_re.I)
                        or _swe_re.match(r'^(?:Intern|Co-op|Engineer|Developer|Analyst)', company_hint, _swe_re.I)
                        or len(company_hint) > 60
                        or not company_hint
                    )
                    if _bad_company:
                        company_hint = ""
                title_hint = link.get_text().strip()
                # Store as tuple (url, company_hint, title_hint) for SWE List
                urls.append((url, company_hint, title_hint))
            if urls:
                return urls

        # ── Standard format: extract all job URLs as plain strings
        for link in soup.find_all("a", href=True):
            url = link.get("href", "")
            if url.startswith("http") and url not in seen:
                if any(domain in url.lower() for domain in JOB_BOARD_DOMAINS):
                    if not EmailExtractor._is_non_job_url(url):
                        urls.append(url)
                        seen.add(url)
                elif "ziprecruiter.com/" in url.lower():
                    url_path = url.lower()
                    if any(p in url_path for p in ["/km/", "/ekm/", "/jobs/", "/k/"]):
                        if not EmailExtractor._is_non_job_url(url):
                            urls.append(url)
                            seen.add(url)
                elif "linkedin.com/" in url.lower() and "/jobs/view/" in url.lower():
                    # Deduplicate by job ID (each card has 3-4 links with different tracking)
                    _li_match = re.search(r"/jobs/view/(\d+)", url)
                    if _li_match:
                        _li_clean = f"https://www.linkedin.com/jobs/view/{_li_match.group(1)}"
                        if _li_clean not in seen:
                            urls.append(_li_clean)
                            seen.add(_li_clean)
        return urls

    @staticmethod
    def _is_non_job_url(url):
        non_job = [
            "/unsubscribe",
            "/my-alerts",
            "/blog",
            "/privacy",
            "/terms",
            "twitter.com",
            "facebook.com",
            "/preferences",
            "/settings",
            "/explore",
            "view-more",
            "install-autofill",
        ]
        return any(p in url.lower() for p in non_job)




class LinkedInEmailParser:
    """Parse LinkedIn Job Alert emails to extract structured job data.

    LinkedIn email structure (per job card):
      <td data-test-id="job-card">
        <img alt="CompanyName">                              -> company (backup)
        <a ...trk=...jobcard_body_ID...>Job Title</a>       -> title + LinkedIn URL
        <p class="text-system-gray-100 ...">Co . Loc</p>    -> company + location

    URLs: linkedin.com/comm/jobs/view/{job_id}?tracking...
    """

    @staticmethod
    def parse_email_jobs(email_html):
        """Parse LinkedIn alert email HTML -> {clean_url: {company, title, location, linkedin_job_id}}"""
        if not email_html:
            return {}

        try:
            soup, _ = safe_parse_html(email_html)
            if not soup:
                return {}

            jobs = {}

            # Primary anchor: data-test-id="job-card" -- LinkedIn own marker
            job_cards = soup.find_all("td", attrs={"data-test-id": "job-card"})

            if not job_cards:
                # Fallback: "similar jobs" / "explore" emails use different structure
                jobs = LinkedInEmailParser._parse_generic_format(soup)
                if jobs:
                    logging.info(f"LinkedIn email parser (generic format): extracted {len(jobs)} job cards")
                    return jobs
                logging.debug("LinkedIn parser: no job cards found in any format")
                return {}

            for card in job_cards:
                try:
                    job = LinkedInEmailParser._parse_single_card(card)
                    if job and job.get("url"):
                        jobs[job["url"]] = job
                except Exception as e:
                    logging.debug(f"LinkedIn card parse failed: {e}")
                    continue

            logging.info(f"LinkedIn email parser: extracted {len(jobs)} job cards")
            return jobs

        except Exception as e:
            logging.debug(f"LinkedIn email parsing failed: {e}")
            return {}

    @staticmethod
    def _parse_single_card(card):
        """Extract company, title, location, URL from a single LinkedIn job card."""

        # -- Step 1: Extract LinkedIn URL + job ID --
        url = None
        linkedin_job_id = None

        for link in card.find_all("a", href=True):
            href = link.get("href", "")
            match = re.search(r"linkedin\.com/(?:comm/)?jobs/view/(\d+)", href)
            if match:
                linkedin_job_id = match.group(1)
                url = f"https://www.linkedin.com/jobs/view/{linkedin_job_id}"
                break

        if not url or not linkedin_job_id:
            return None

        # -- Step 2: Extract title from jobcard_body link --
        title = "Unknown"

        for link in card.find_all("a", href=True):
            href = link.get("href", "")
            if "jobcard_body" in href:
                text = link.get_text(strip=True)
                if text and len(text) > 5:
                    title = text
                    break

        if title == "Unknown":
            title_link = card.find("a", class_=re.compile(r"text-md|text-color-brand"))
            if title_link:
                text = title_link.get_text(strip=True)
                if text and len(text) > 5:
                    title = text

        # -- Step 3: Extract company + location from info line --
        company = "Unknown"
        location = "Unknown"

        info_p = card.find("p", class_=re.compile(r"text-system-gray"))
        if info_p:
            info_text = info_p.get_text(strip=True)
            if "\u00b7" in info_text:
                parts = info_text.split("\u00b7", 1)
                company = parts[0].strip()
                raw_location = parts[1].strip() if len(parts) > 1 else "Unknown"
                location = re.sub(r",?\s*United States\s*$", "", raw_location, flags=re.I).strip()
                if not location:
                    location = "United States"

        # -- Step 4: Backup company from <img alt="CompanyName"> --
        if company == "Unknown":
            logo_img = card.find("img", alt=True)
            if logo_img:
                alt = logo_img.get("alt", "").strip()
                _skip_alts = {"LinkedIn", "radar icon", "", "Prasad Kanade"}
                if alt and alt not in _skip_alts and len(alt) > 1 and len(alt) < 100:
                    company = alt

        if title == "Unknown" and company == "Unknown":
            return None

        return {
            "company": company,
            "title": title,
            "location": location,
            "url": url,
            "linkedin_job_id": linkedin_job_id,
        }

    @staticmethod
    @staticmethod
    def _parse_generic_format(soup):
        """Parse LinkedIn 'similar jobs' and 'explore new jobs' email format.

        These emails have one job per container:
          <td data-test-id="email-generic-section-JOBS_POSTING_SECTION-job-cards">
        Each contains a link with "Title | Company · Location | flavor" text
        and a <p> with "Company · Location" info.
        """
        jobs = {}

        containers = soup.find_all("td", attrs={
            "data-test-id": "email-generic-section-JOBS_POSTING_SECTION-job-cards"
        })
        if not containers:
            return {}

        seen_ids = set()
        for container in containers:
            for link in container.find_all("a", href=True):
                href = link.get("href", "")
                match = re.search(r"linkedin\.com/(?:comm/)?jobs/view/(\d+)", href)
                if not match:
                    continue

                linkedin_job_id = match.group(1)
                if linkedin_job_id in seen_ids:
                    continue

                link_text = link.get_text(separator=" | ", strip=True)
                # Normalize Unicode hyphens/dashes to ASCII
                link_text = link_text.replace("‐", "-").replace("‑", "-").replace("–", "-").replace("—", "-").replace("−", "-")

                # lxml parser may return empty text for links wrapping tables
                # Fall back to container text which has "Title Company · Location flavor"
                if not link_text or len(link_text) < 5:
                    link_text = container.get_text(separator=" | ", strip=True)
                    if not link_text or len(link_text) < 5:
                        continue

                # Only mark as seen AFTER we confirm this link has text
                seen_ids.add(linkedin_job_id)

                url = f"https://www.linkedin.com/jobs/view/{linkedin_job_id}"
                title = "Unknown"
                company = "Unknown"
                location = "Unknown"

                # Extract company + location from <p> with middot
                for p in container.find_all("p"):
                    p_text = p.get_text(strip=True)
                    if "·" in p_text and len(p_text) < 200:
                        parts = p_text.split("·", 1)
                        company = parts[0].strip()
                        raw_loc = parts[1].strip() if len(parts) > 1 else "Unknown"
                        location = re.sub(r",?\s*United States\s*$", "", raw_loc, flags=re.I).strip()
                        location = re.sub(r"\s*\((?:On-site|Hybrid|Remote)\)\s*$", "", location, flags=re.I).strip()
                        if not location:
                            location = "United States"
                        break

                # Extract title from link text
                if company != "Unknown" and company in link_text:
                    title_part = link_text.split(company)[0].strip().rstrip(" |").strip()
                    if title_part and len(title_part) > 3:
                        title = title_part
                elif " | " in link_text:
                    segments = [s.strip() for s in link_text.split(" | ") if s.strip()]
                    if segments and len(segments[0]) > 3:
                        title = segments[0]

                # Backup company from <img alt>
                if company == "Unknown":
                    img = container.find("img", alt=True)
                    if img:
                        alt = img.get("alt", "").strip()
                        skip_alts = {"LinkedIn", "radar icon", "", "Prasad Kanade"}
                        if alt and alt not in skip_alts and len(alt) > 1 and len(alt) < 100:
                            company = alt

                if title == "Unknown" and company == "Unknown":
                    continue

                jobs[url] = {
                    "company": company,
                    "title": title,
                    "location": location,
                    "url": url,
                    "linkedin_job_id": linkedin_job_id,
                }

                break  # One job per container, move to next

        return jobs


class PageFetcher:
    def __init__(self):
        self.session = _SESSION

    def check_url_health(self, url):
        if url in _URL_HEALTH_CACHE:
            cached = _URL_HEALTH_CACHE[url]
            # FIX 3: support both old tuple format and new dict format
            if isinstance(cached, dict):
                return cached["healthy"], cached["status"]
            return cached
        response = retry_request(url, method="HEAD", max_retries=2)
        if response:
            is_healthy = 200 <= response.status_code < 400 or response.status_code in [
                301, 302, 303, 307, 308,
            ]
            _URL_HEALTH_CACHE[url] = {
                "healthy": is_healthy,
                "status": response.status_code,
                "ts": time.time()
            }
            _save_url_health_cache(_URL_HEALTH_CACHE)
            return is_healthy, response.status_code
        _URL_HEALTH_CACHE[url] = {"healthy": False, "status": 0, "ts": time.time()}
        _save_url_health_cache(_URL_HEALTH_CACHE)
        return False, 0

    _failed_urls = None

    @classmethod
    def _load_failed_urls(cls):
        if cls._failed_urls is None:
            try:
                from aggregator.config import FAILED_URLS_FILE
                if os.path.exists(FAILED_URLS_FILE):
                    with open(FAILED_URLS_FILE, "r") as f:
                        cls._failed_urls = json.load(f)
                else:
                    cls._failed_urls = {}
            except Exception as _e:
                logging.debug("suppressed: %s", _e)
                cls._failed_urls = {}
        return cls._failed_urls

    @classmethod
    def _prune_failed_urls(cls):
        try:
            from aggregator.config import FAILED_URLS_FILE
            import datetime as _dt
            if not os.path.exists(FAILED_URLS_FILE):
                return
            data = json.load(open(FAILED_URLS_FILE))
            cutoff = _dt.datetime.now() - _dt.timedelta(days=30)
            pruned = {}
            for k, v in data.items():
                if isinstance(v, str):
                    try:
                        keep = _dt.datetime.fromisoformat(v[:10]) > cutoff
                    except Exception:
                        keep = True
                elif isinstance(v, dict):
                    keep = v.get("ts", time.time()) > time.time() - 30*86400
                else:
                    keep = True
                if keep:
                    pruned[k] = v
            if len(pruned) < len(data):
                json.dump(pruned, open(FAILED_URLS_FILE, "w"), indent=2)
                logging.debug(f"Pruned failed URLs: {len(data)} → {len(pruned)}")
        except Exception as _sw:
            from aggregator.swallowed import swallow as _s; _s('cache.failed_urls_prune', _sw)

    @classmethod
    def _save_failed_url(cls, url):
        failed = cls._load_failed_urls()
        import time as _t
        failed[url] = _t.strftime("%Y-%m-%d")
        try:
            from aggregator.config import FAILED_URLS_FILE
            with open(FAILED_URLS_FILE, "w") as f:
                json.dump(failed, f, indent=2)
        except Exception as _e:
            logging.debug("suppressed: %s", _e)
            pass

    def fetch_page(self, url):
        # Was `if url in _HTTP_RESPONSE_CACHE`, which BYPASSED the 6-hour TTL
        # that _http_cache_get() enforces - a stale page could be served as
        # fresh, and a stale page means a stale posting date.
        cached = _http_cache_get(url)
        if cached is not None:
            return cached["response"], cached["final_url"], cached["page_source"]

        # Check failed URL cache (skip URLs that failed before today)
        failed = self._load_failed_urls()
        import time as _t
        today = _t.strftime("%Y-%m-%d")
        if url in failed and failed[url] == today:
            logging.debug(f"Skipping previously failed URL: {url[:60]}")
            return None, None, None

        # HEAD health check disabled — slow and redundant, failed fetches handled downstream
        # is_healthy, status = self.check_url_health(url)

        # WORKDAY: use the JSON API, not a browser.
        # Workday pages render entirely in JavaScript, so a plain fetch
        # returns 0 characters. The Selenium branch below was meant to cover
        # that, but chromedriver is not installed, so it fails and falls
        # through to the plain fetch. Result: no Workday job has ever been
        # checked for undergraduate-only, clearance, sponsorship, PhD or page
        # age - across 227 discovered tenants.
        #
        # The CXS API returns the same posting as JSON in one request, no
        # browser required. Verified on P&G: 8,622 characters containing
        # "In process of obtaining a Bachelors degree" and "Immigration
        # Sponsorship is not available for this role", neither of which
        # appears in the HTML.
        if url and "myworkdayjobs.com" in url.lower():
            try:
                from aggregator.workday_api import fetch_description
                _wd = fetch_description(url)
                if _wd and len(_wd) > 200:
                    _resp = self._create_mock_response(_wd, url)
                    _HTTP_RESPONSE_CACHE[url] = {
                        "response": _resp, "final_url": url, "page_source": _wd,
                    }
                    logging.debug(f"Workday API: {len(_wd)} chars for {url[:60]}")
                    return _resp, url, _wd
            except Exception as _we:
                logging.debug(f"Workday API failed: {_we}")

        if self._is_js_heavy_platform(url):
            html, final_url, page_source = self._try_selenium(url)
            if html:
                response = self._create_mock_response(html, final_url)
                _HTTP_RESPONSE_CACHE[url] = {
                    "response": response,
                    "final_url": final_url,
                    "page_source": page_source,
                }
                return response, final_url, page_source

        response = retry_request(url)
        if response and 200 <= response.status_code < 400:
            _HTTP_RESPONSE_CACHE[url] = {
                "response": response,
                "final_url": response.url,
                "page_source": response.text,
            }
            return response, response.url, response.text

        # Auto-retry with different user agents before falling back to Selenium
        if not response or response.status_code not in [404, 410]:
            for _alt_ua in USER_AGENTS[1:3]:
                try:
                    import requests as _req
                    _r = _req.get(url, timeout=15, headers={"User-Agent": _alt_ua}, allow_redirects=True)
                    if _r and 200 <= _r.status_code < 400:
                        logging.info(f"Retry with alt UA succeeded: {url[:60]}")
                        _HTTP_RESPONSE_CACHE[url] = {
                            "response": _r,
                            "final_url": _r.url,
                            "page_source": _r.text,
                        }
                        return _r, _r.url, _r.text
                except Exception:
                    continue

        if SELENIUM_AVAILABLE:
            logging.info(f"Standard request failed, trying Selenium for {url}")
            html, final_url, page_source = self._try_selenium(url)
            if html:
                response = self._create_mock_response(html, final_url)
                _HTTP_RESPONSE_CACHE[url] = {
                    "response": response,
                    "final_url": final_url,
                    "page_source": page_source,
                }
                return response, final_url, page_source

        _HTTP_RESPONSE_CACHE[url] = {
            "response": None,
            "final_url": None,
            "page_source": None,
        }
        self._save_failed_url(url)
        return None, None, None

    @staticmethod
    def _is_js_heavy_platform(url):
        if not url:
            return False
        # Strict gating: only use Selenium for platforms that truly need it
        js_platforms = [
            "workday",
            "myworkdayjobs",
            "oracle",
            "oraclecloud",
            "ashbyhq",
        ]
        # Greenhouse rarely needs Selenium — only job-boards subdomain
        url_lower = url.lower()
        if "job-boards.greenhouse.io" in url_lower or "job-boards.eu.greenhouse.io" in url_lower:
            return True
        if "boards.greenhouse.io" in url_lower:
            return False  # Standard greenhouse works without Selenium
        return any(platform in url_lower for platform in js_platforms)

    @staticmethod
    def _try_selenium(url):
        global _SELENIUM_DRIVER, _SELENIUM_LAST_USED

        if not SELENIUM_AVAILABLE:
            return None, None, None

        try:
            if _SELENIUM_DRIVER is None:
                chrome_options = Options()
                chrome_options.add_argument("--headless")
                chrome_options.add_argument(
                    "--disable-blink-features=AutomationControlled"
                )
                chrome_options.add_argument("--no-sandbox")
                chrome_options.add_argument("--disable-dev-shm-usage")
                chrome_options.add_argument(f"user-agent={USER_AGENTS[0]}")
                chrome_options.add_experimental_option(
                    "excludeSwitches", ["enable-logging"]
                )
                service = Service(ChromeDriverManager().install())
                _SELENIUM_DRIVER = webdriver.Chrome(
                    service=service, options=chrome_options
                )
                _SELENIUM_DRIVER.set_page_load_timeout(30)
                logging.info("Selenium driver initialized (will be reused)")

            _SELENIUM_DRIVER.get(url)
            _SELENIUM_LAST_USED = time.time()

            url_lower = url.lower()
            # Adaptive wait: wait for content instead of fixed sleep
            max_wait = 15 if ("oracle" in url_lower or "workday" in url_lower) else (8 if "greenhouse" in url_lower else (6 if "ashby" in url_lower else 5))
            try:
                WebDriverWait(_SELENIUM_DRIVER, max_wait).until(
                    lambda d: d.execute_script("return document.readyState") == "complete"
                )
            except Exception as _e:
                logging.debug("suppressed: %s", _e)
                pass
            # Short extra wait for JS rendering
            extra = 3 if ("oracle" in url_lower or "workday" in url_lower) else 1
            time.sleep(extra)
            # Wait for job content element
            try:
                WebDriverWait(_SELENIUM_DRIVER, 5).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, "h1, h2, [data-automation-id], .job-title, .posting-headline"))
                )
            except Exception as _e:
                logging.debug("suppressed: %s", _e)
                pass

            _SELENIUM_DRIVER.execute_script(
                "window.scrollTo(0, document.body.scrollHeight);"
            )
            time.sleep(1)
            page_source = _SELENIUM_DRIVER.page_source
            current_url = _SELENIUM_DRIVER.current_url

            return page_source, current_url, page_source

        except Exception as e:
            logging.error(f"Selenium failed for {url}: {e}")

            if _SELENIUM_DRIVER:
                try:
                    _SELENIUM_DRIVER.quit()
                except Exception as _e:
                    logging.debug("suppressed: %s", _e)
                    pass
                _SELENIUM_DRIVER = None

            return None, None, None

    @staticmethod
    def _create_mock_response(html, url):
        return type("obj", (object,), {"text": html, "status_code": 200, "url": url})()


class PageParser:
    @staticmethod
    def extract_company(soup, url):
        platform = PlatformDetector.detect(url)
        return CompanyExtractor.extract_all_methods(url, soup)

    @staticmethod
    def extract_title(soup):
        if not soup:
            return "Unknown"

        candidates = []

        try:
            json_ld = soup.find("script", type="application/ld+json")
            if json_ld:
                try:
                    data = json.loads(json_ld.string)
                    if isinstance(data, dict) and data.get("title"):
                        title = data["title"]
                        if 5 < len(title) < 200:
                            candidates.append((title, 100))
                except Exception as _e:
                    logging.debug("suppressed: %s", _e)
                    pass

            meta_title = soup.find("meta", {"property": "og:title"})
            if meta_title and meta_title.get("content"):
                title = meta_title.get("content").strip()
                if 5 < len(title) < 200 and "careers" not in title.lower():
                    candidates.append((title, 95))

            meta_title_name = soup.find("meta", {"name": "title"})
            if meta_title_name and meta_title_name.get("content"):
                title = meta_title_name.get("content").strip()
                if 5 < len(title) < 200:
                    candidates.append((title, 90))

            title_selectors = [
                ("h1.job-title", 95),
                ("h1[class*='job']", 90),
                ("h1[class*='title']", 85),
                (".job-title", 80),
                (".job-details-title", 80),
                ("[class*='job-title']", 75),
                ("[data-automation='job-title']", 90),
                ("[data-test='job-title']", 90),
                ("h1[itemprop='title']", 85),
                ("span[itemprop='title']", 75),
                ("div.job-title", 80),
                ("h2.job-title", 75),
            ]

            for selector, priority in title_selectors:
                try:
                    elem = soup.select_one(selector)
                    if elem:
                        title = elem.get_text().strip()
                        if 5 < len(title) < 200:
                            candidates.append((title, priority))
                except Exception as _e:
                    logging.debug("suppressed: %s", _e)
                    pass

            h1_tags = soup.find_all("h1", limit=3)
            for h1 in h1_tags:
                title = h1.get_text().strip()
                if 5 < len(title) < 200 and len(title.split()) > 1:
                    candidates.append((title, 70))

            if candidates:
                candidates.sort(key=lambda x: x[1], reverse=True)

                for title, priority in candidates:
                    title_lower = title.lower()

                    if any(
                        bad in title_lower
                        for bad in [
                            "careers",
                            "job board",
                            "opportunities",
                            "join our team",
                            "working at",
                            "about us",
                            "company",
                            "apply now",
                            "search jobs",
                            "current openings",
                            "work with us",
                        ]
                    ):
                        continue

                    job_keywords = [
                        "intern",
                        "co-op",
                        "software",
                        "engineer",
                        "developer",
                        "analyst",
                        "data",
                        "scientist",
                        "architect",
                        "designer",
                        "programmer",
                        "manager",
                        "specialist",
                        "coordinator",
                        "associate",
                        "technical",
                        "technology",
                        "ai",
                        "ml",
                    ]
                    if any(kw in title_lower for kw in job_keywords):
                        if len(title.split()) >= 2:
                            return title

                for title, priority in candidates:
                    if len(title.split()) >= 2:
                        return title

        except Exception as e:
            logging.debug(f"Title extraction failed: {e}")

        return "Unknown"

    @staticmethod
    def extract_job_id(soup, url):
        return JobIDExtractor.extract_all_methods(url, soup)

    @staticmethod
    def extract_job_age_days(soup):
        if not soup:
            return None
        try:
            page_text = soup.get_text()[:3000]
            days = DateParser.extract_days_ago(page_text)
            if days is not None:
                if days > MAX_REASONABLE_AGE_DAYS or days < 0:
                    return None
            return days
        except Exception as _e:
            logging.debug("suppressed: %s", _e)
            return None

    @staticmethod
    def extract_jobright_data(soup, url, jobright_auth):
        try:
            script_tag = soup.find("script", {"id": "__NEXT_DATA__"})
            if not script_tag:
                return None
            data = json.loads(script_tag.string)
            job_result = (
                data.get("props", {})
                .get("pageProps", {})
                .get("dataSource", {})
                .get("jobResult", {})
            )
            if not job_result:
                return None
            company = job_result.get("companyResult", {}).get("companyName", "Unknown")
            title = job_result.get("jobTitle", "Unknown")
            location = job_result.get("jobLocation", "Unknown")
            is_remote = job_result.get("isRemote", False)
            work_model = job_result.get("workModel", "").lower()
            if is_remote or work_model == "remote":
                remote = "Remote"
            elif work_model == "hybrid":
                remote = "Hybrid"
            elif work_model == "onsite":
                remote = "On Site"
            else:
                remote = "Unknown"
            recommendation_tags = job_result.get("recommendationTags", [])
            sponsorship = (
                "Yes" if "H1B Sponsor Likely" in recommendation_tags else "Unknown"
            )
            actual_url = (
                job_result.get("applyLink") or job_result.get("originalUrl") or url
            )
            is_company_site = job_result.get("isCompanySiteLink", False)
            return {
                "company": company,
                "title": title,
                "location": location,
                "sponsorship": sponsorship,
                "remote": remote,
                "url": actual_url,
                "is_company_site": is_company_site,
            }
        except Exception as e:
            logging.error(f"Failed to extract Jobright data: {e}")
            return None


class JobTypeExtractor:
    @staticmethod
    def extract_all_methods(soup, url, title):
        if not soup:
            return "Unknown"

        results = []

        results.append(JobTypeExtractor.extract_from_json_ld(soup))
        results.append(JobTypeExtractor.extract_from_meta(soup))
        results.append(JobTypeExtractor.extract_from_selectors(soup))
        results.append(JobTypeExtractor.extract_from_page_text(soup))
        results.append(JobTypeExtractor.extract_from_url(url))

        valid_results = [r for r in results if r and r != "Unknown"]

        if not valid_results:
            return "Unknown"

        from collections import Counter

        counts = Counter(valid_results)
        most_common = counts.most_common(1)[0][0]

        return most_common

    @staticmethod
    def extract_from_json_ld(soup):
        try:
            script = soup.find("script", type="application/ld+json")
            if script:
                data = json.loads(script.string)
                emp_type = data.get("employmentType", "")
                return JobTypeExtractor._normalize_type(emp_type)
        except Exception as _e:
            logging.debug("suppressed: %s", _e)
            pass
        return "Unknown"

    @staticmethod
    def extract_from_meta(soup):
        try:
            for prop in ["og:job:type", "job:type", "employmentType"]:
                meta = soup.find("meta", {"property": prop})
                if meta and meta.get("content"):
                    return JobTypeExtractor._normalize_type(meta.get("content"))

            for name in ["job-type", "employment-type", "jobType"]:
                meta = soup.find("meta", {"name": name})
                if meta and meta.get("content"):
                    return JobTypeExtractor._normalize_type(meta.get("content"))
        except Exception as _e:
            logging.debug("suppressed: %s", _e)
            pass
        return "Unknown"

    @staticmethod
    def extract_from_selectors(soup):
        try:
            selectors = [
                ("span", {"class": "job-type"}),
                ("div", {"class": "employment-type"}),
                ("dd", {"class": "job-classification"}),
                ("span", {"class": re.compile(r"job.*type", re.I)}),
                ("div", {"class": re.compile(r"employment.*type", re.I)}),
            ]

            for tag, attrs in selectors:
                elem = soup.find(tag, attrs)
                if elem:
                    text = elem.get_text().strip()
                    normalized = JobTypeExtractor._normalize_type(text)
                    if normalized != "Unknown":
                        return normalized
        except Exception as _e:
            logging.debug("suppressed: %s", _e)
            pass
        return "Unknown"

    @staticmethod
    def extract_from_page_text(soup):
        try:
            text = soup.get_text()[:2000]

            patterns = [
                (r"(?:job|employment)\s+type:?\s*(intern(?:ship)?|co-?op)", 1),
                (r"time\s+type:?\s*((?:full|part)[\s-]time)", 1),
            ]

            for pattern, group in patterns:
                match = re.search(pattern, text, re.I)
                if match:
                    return JobTypeExtractor._normalize_type(match.group(group))
        except Exception as _e:
            logging.debug("suppressed: %s", _e)
            pass
        return "Unknown"

    @staticmethod
    def extract_from_url(url):
        try:
            url_lower = url.lower()
            if "/internship/" in url_lower or "/intern/" in url_lower:
                return "Internship"
            if "/co-op/" in url_lower or "/coop/" in url_lower:
                return "Co-op"
            if "/fellowship/" in url_lower:
                return "Fellowship"
        except Exception as _e:
            logging.debug("suppressed: %s", _e)
            pass
        return "Unknown"

    @staticmethod
    def _normalize_type(text):
        if not text:
            return "Unknown"

        text_lower = text.lower().strip()

        if text_lower in ["intern", "internship", "intern/co-op", "summer intern"]:
            return "Internship"
        if text_lower in ["co-op", "coop", "cooperative", "co-operative"]:
            return "Co-op"
        if text_lower in ["fellowship", "fellow"]:
            return "Fellowship"
        if text_lower in ["apprentice", "apprenticeship"]:
            return "Apprenticeship"
        if text_lower in ["trainee", "training program"]:
            return "Trainee"
        if text_lower in ["full time", "full-time", "fulltime"]:
            return "Full Time"
        if text_lower in ["part time", "part-time", "parttime"]:
            return "Part Time"

        return "Unknown"


class SourceParsers:
    @staticmethod
    def parse_jobright_email(soup, url, jobright_auth):
        try:
            url_base = url.split("?")[0]
            all_links = soup.find_all("a", href=re.compile(re.escape(url_base)))
            title_link = None
            for link in all_links:
                link_text = link.get_text().strip()
                if len(link_text) > 15 and any(
                    kw in link_text.lower()
                    for kw in ["intern", "engineer", "software", "data", "analyst"]
                ):
                    title_link = link
                    break
            if not title_link:
                title_link = next(
                    (link for link in all_links if len(link.get_text().strip()) > 15),
                    None,
                )
            if not title_link:
                return None
            job_section = title_link.find_parent("table", id="job-container")
            if not job_section:
                current = title_link
                for _ in range(5):
                    current = current.find_parent("table")
                    if current and len(current.get_text()) > 100:
                        job_section = current
                        break
            if not job_section:
                return None
            company = SourceParsers._extract_company_multi_method(job_section, soup)
            title = SourceParsers._extract_title_multi_method(title_link, job_section)
            location, remote = SourceParsers._extract_location_multi_method(
                job_section, soup
            )
            age_days = DateParser.extract_days_ago(job_section.get_text())
            actual_url, is_company_site = jobright_auth.resolve_jobright_url(url)
            return {
                "company": company,
                "title": title,
                "location": location,
                "remote": remote,
                "url": actual_url,
                "sponsorship": "Unknown (Email)",
                "is_company_site": is_company_site,
                "email_age_days": age_days,
            }
        except Exception as e:
            logging.error(f"Failed to parse Jobright email: {e}")
            return None

    @staticmethod
    def _extract_company_multi_method(job_section, soup):
        company_elem = job_section.find("p", id="job-company-name")
        if company_elem:
            company = company_elem.get_text().strip()
            if company and company != "Unknown":
                return company
        company_elem = job_section.find("div", class_="company-name")
        if company_elem:
            company = company_elem.get_text().strip()
            if company:
                return company
        for header in job_section.find_all(["h2", "h3", "h4"]):
            text = header.get_text().strip()
            if (
                len(text) > 3
                and len(text) < 50
                and not any(
                    kw in text.lower()
                    for kw in ["intern", "engineer", "software", "match", "referral"]
                )
            ):
                return text
        all_text = job_section.get_text()
        lines = [line.strip() for line in all_text.split("\n") if line.strip()]
        for line in lines[:10]:
            if len(line) > 3 and len(line) < 50:
                if not any(
                    kw in line.lower()
                    for kw in [
                        "match",
                        "apply",
                        "referral",
                        "ago",
                        "hour",
                        "minute",
                        "/hr",
                    ]
                ):
                    if line[0].isupper() and not line.isupper():
                        return line
        return "Unknown"

    @staticmethod
    def _extract_title_multi_method(title_link, job_section):
        title_text = title_link.get_text(separator="|||", strip=True)
        title_parts = title_text.split("|||")
        internship_kw = {
            "intern",
            "engineer",
            "developer",
            "software",
            "data",
            "ml",
            "ai",
            "analyst",
            "co-op",
            "coop",
        }
        title = next(
            (
                re.sub(r"\s*(APPLY NOW|Apply|View).*$", "", part, flags=re.I).strip()
                for part in title_parts
                if any(kw in part.lower() for kw in internship_kw) and len(part) > 5
            ),
            None,
        )
        if title:
            return title
        for elem in job_section.find_all(["h1", "h2", "h3"]):
            text = elem.get_text().strip()
            if any(kw in text.lower() for kw in internship_kw) and len(text) > 10:
                return re.sub(
                    r"\s*(APPLY NOW|Apply|View).*$", "", text, flags=re.I
                ).strip()
        return "Unknown"

    @staticmethod
    def _extract_location_multi_method(job_section, soup):
        location = "Unknown"
        remote = "Unknown"
        job_tags = job_section.find_all("p", id="job-tag")
        for tag in job_tags:
            text = tag.get_text(separator="|||", strip=True).split("|||")[0]
            if "$" in text or "referral" in text.lower() or "/hr" in text.lower():
                continue
            text = re.sub(
                r"(Team|Department|Division).*$", "", text, flags=re.I
            ).strip()
            if "," in text:
                parts = text.split(",")
                if len(parts) == 2:
                    city, state = parts[0].strip(), parts[1].strip()
                    if validate_us_state_code(state):
                        location = f"{city}, {state.upper()}"
                        remote = "On Site"
                        return location, remote
            if "remote" in text.lower():
                location = "Remote"
                remote = "Remote"
                return location, remote
            elif "hybrid" in text.lower():
                location = "Hybrid"
                remote = "Hybrid"
                return location, remote
        all_text = job_section.get_text()
        city_state_pattern = r"\b([A-Z][a-z]+(?: [A-Z][a-z]+)*),\s*([A-Z]{2})\b"
        matches = re.findall(city_state_pattern, all_text)
        for city, state in matches:
            if validate_us_state_code(state):
                location = f"{city}, {state}"
                remote = "On Site"
                return location, remote
        if re.search(r"\b(remote|100%\s*remote|fully\s*remote)\b", all_text, re.I):
            location = "Remote"
            remote = "Remote"
            return location, remote
        if re.search(r"\bhybrid\b", all_text, re.I):
            location = "Hybrid"
            remote = "Hybrid"
            return location, remote
        all_soup_text = soup.get_text()
        matches = re.findall(city_state_pattern, all_soup_text)
        for city, state in matches[:5]:
            if validate_us_state_code(state):
                location = f"{city}, {state}"
                remote = "On Site"
                return location, remote
        return location, remote

    @staticmethod
    def parse_ziprecruiter_email(soup, url):
        return None





class ZipRecruiterResolver:
    """Resolves ZipRecruiter job pages to actual company URLs."""

    @staticmethod
    def _strip_auth_params(url):
        """FIX 6: Remove expiring auth tokens from ZipRecruiter URLs before storing."""
        if not url:
            return url
        try:
            import urllib.parse
            parsed = urllib.parse.urlparse(url)
            params = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
            # Remove known expiring/tracking params
            _strip = {"auth_token", "expires", "contact_id", "tsid", "utm_source",
                      "utm_medium", "utm_campaign", "utm_content", "utm_term"}
            clean = {k: v for k, v in params.items() if k.lower() not in _strip}
            new_query = urllib.parse.urlencode(clean, doseq=True)
            clean_url = urllib.parse.urlunparse((
                parsed.scheme, parsed.netloc, parsed.path,
                parsed.params, new_query, ""
            ))
            return clean_url
        except Exception:
            return url

    @staticmethod
    def resolve(ziprecruiter_url):
        """Fetch ZipRecruiter page, find Apply button, extract actual company URL."""
        # FIX 6: strip auth tokens from the input URL before resolving
        ziprecruiter_url = ZipRecruiterResolver._strip_auth_params(ziprecruiter_url)
        try:
            response = retry_request(ziprecruiter_url, max_retries=2)
            if not response or response.status_code != 200:
                return None

            soup, _ = safe_parse_html(response.text)
            if not soup:
                return None

            # Method 1: Find Apply button with job-redirect href
            apply_link = soup.find("a", href=re.compile(r"ziprecruiter\.com/job-redirect"))
            if apply_link:
                redirect_url = apply_link.get("href", "")
                actual = ZipRecruiterResolver._extract_from_redirect(redirect_url)
                if actual:
                    logging.info(f"ZipRecruiter Apply button → {actual[:80]}")
                    return actual

            # Method 2: Find any external apply link
            for link in soup.find_all("a", href=True):
                href = link.get("href", "")
                link_text = link.get_text(strip=True).lower()
                if "apply" in link_text and "ziprecruiter" not in href:
                    if href.startswith("http"):
                        logging.info(f"ZipRecruiter external apply → {href[:80]}")
                        return href

            # Method 3: Follow the redirect URL
            actual = ZipRecruiterResolver._follow_redirect(ziprecruiter_url)
            if actual:
                return actual

        except Exception as e:
            logging.debug(f"ZipRecruiter resolve failed: {e}")
        return None

    @staticmethod
    def _extract_from_redirect(redirect_url):
        """Decode the match_token to get ExternalApplyUrl."""
        try:
            import urllib.parse
            parsed = urllib.parse.urlparse(redirect_url)
            params = urllib.parse.parse_qs(parsed.query)
            token = params.get("match_token", [None])[0]
            if token:
                import base64
                # Add padding if needed
                padding = 4 - len(token) % 4
                if padding != 4:
                    token += "=" * padding
                decoded = base64.urlsafe_b64decode(token).decode("utf-8")
                data = json.loads(decoded)
                ext_url = data.get("ExternalApplyUrl", "")
                if ext_url and ext_url.startswith("http"):
                    return ext_url
        except Exception as e:
            logging.debug(f"ZipRecruiter token decode failed: {e}")
        return None

    @staticmethod
    def _follow_redirect(url):
        """Follow HTTP redirects to get final URL."""
        try:
            response = requests.get(url, allow_redirects=True, timeout=15,
                                    headers={"User-Agent": USER_AGENTS[0]})
            if response and response.url != url:
                final = response.url
                if "ziprecruiter.com" not in final:
                    return final
        except Exception as _e:
            logging.debug("suppressed: %s", _e)
            pass
        return None

    @staticmethod
    def parse_email_jobs(email_html):
        """Parse ZipRecruiter email HTML to extract job metadata for pre-filtering."""
        if not email_html:
            return []
        try:
            soup, _ = safe_parse_html(email_html)
            if not soup:
                return []

            jobs = []
            seen_urls = set()

            links = soup.find_all("a", href=re.compile(r"ziprecruiter\.com/(?:jobs/|km/|ekm/|k/)"))

            for link in links:
                try:
                    href = link.get("href", "")
                    if not href or href in seen_urls:
                        continue
                    seen_urls.add(href)

                    container = link
                    for _ in range(6):
                        parent = container.parent
                        if not parent:
                            break
                        parent_len = len(parent.get_text(strip=True))
                        if parent_len > 500:
                            break
                        container = parent

                    card_text = container.get_text(separator="|||", strip=True)
                    parts = [p.strip() for p in card_text.split("|||") if p.strip() and len(p.strip()) > 1]

                    if len(parts) < 2:
                        continue

                    title = ""
                    company = ""
                    location = ""
                    skip_words = {"view details", "apply now", "be seen first", "estimated pay", "show me more", "view more", "not quite right"}

                    badge_words = {"new", "hot", "featured", "urgent", "sponsored", "just posted", "hiring"}
                    clean_parts = []
                    for part in parts:
                        pl = part.lower().strip()
                        if any(sw in pl for sw in skip_words):
                            continue
                        if pl.startswith("$") or "/hr" in pl or "/yr" in pl or "/wk" in pl or "/mo" in pl:
                            continue
                        if len(part) < 3:
                            continue
                        if pl in badge_words:
                            continue
                        clean_parts.append(part)

                    for i, part in enumerate(clean_parts):
                        if "\u2022" in part:
                            sub = [s.strip() for s in part.split("\u2022") if s.strip()]
                            if len(sub) >= 2:
                                if not company:
                                    company = sub[0]
                                if not location:
                                    location = sub[1] if len(sub) > 1 else ""
                            continue
                        if not title and i < 2:
                            title = part
                        elif not company and i < 4:
                            company = part

                    if title and len(title) > 5:

                        skip = any(g in title.lower() for g in ['download the free', 'unsubscribe', 'privacy policy', 'get hired', 'career advisor', 'view more jobs'])
                        skip = skip or any(g in (company or '').lower() for g in ['download the free', 'unsubscribe', 'privacy policy'])
                        if skip:
                            continue
                        jobs.append({
                            "title": title,
                            "company": company or "Unknown",
                            "location": location or "Unknown",
                            "url": href,
                        })
                except Exception:
                    continue

            logging.info(f"ZipRecruiter email parser: extracted {len(jobs)} job cards")
            return jobs
        except Exception as e:
            logging.debug(f"ZipRecruiter email parsing failed: {e}")
            return []
class SimplifyGitHubScraper:
    @staticmethod
    def scrape(url, source_name="GitHub"):
        try:
            logging.info(f"Fetching {source_name} from {url}")
            response = retry_request(url)
            if not response:
                logging.error(f"{source_name}: Failed to fetch URL")
                return []
            if response.status_code != 200:
                logging.error(f"{source_name}: HTTP {response.status_code}")
                return []
            logging.info(f"{source_name}: Fetched, length: {len(response.text)}")
            soup, parser = safe_parse_html(response.text)
            if soup:
                logging.info(f"{source_name}: Parsed with {parser}")
                tables = soup.find_all("table")
                if tables:
                    jobs = SimplifyGitHubScraper._parse_html_tables(soup, source_name)
                    if jobs:
                        logging.info(f"{source_name}: Found {len(jobs)} jobs via HTML")
                        return jobs
            logging.info(f"{source_name}: Trying Markdown")
            jobs = SimplifyGitHubScraper._parse_markdown_text(
                response.text, source_name
            )
            if jobs:
                logging.info(f"{source_name}: Found {len(jobs)} jobs via Markdown")
            return jobs
        except Exception as e:
            logging.error(f"{source_name}: Error: {e}")
            return []

    @staticmethod
    def _parse_markdown_text(text, source_name):
        lines = text.split("\n")
        jobs = []
        header_idx = next(
            (i for i, line in enumerate(lines) if _HEADER_PATTERN.search(line)), -1
        )
        if header_idx == -1:
            return []
        header = lines[header_idx]
        delimiter = "\t" if "\t" in header else "|"
        start = header_idx + 1 if delimiter == "\t" else header_idx + 2
        last_company = ""  # FIX 6: initialize before loop to prevent UnboundLocalError
        for line in lines[start:]:
            if not line.strip():
                continue
            _cells = [p.strip() for p in line.split(delimiter)]
            # Markdown rows are wrapped in the delimiter, so drop the OUTER
            # empties only. Interior blanks must be kept or every column after
            # a blank cell shifts left (company becomes title, title becomes
            # location). That shift produced the junk company names in the sheet.
            if delimiter == "|":
                if _cells and _cells[0] == "":
                    _cells = _cells[1:]
                if _cells and _cells[-1] == "":
                    _cells = _cells[:-1]
            parts = _cells
            if len(parts) < 5:
                continue
            # Strip HTML tags from company cell (SpeedyApply wraps in <a><strong>)
            _co_cell = re.sub(r"<[^>]+>", "", parts[0])
            _co_cell = re.sub(r"\*\*", "", _co_cell)  # Strip markdown bold
            raw_company = _EMOJI_PATTERN.sub("", _co_cell).strip()
            if raw_company and "↳" not in raw_company:
                company = raw_company
                last_company = company
            else:
                company = last_company
                if not company:
                    continue  # FIX 6: skip continuation rows with no known parent company
            title = _EMOJI_PATTERN.sub("", parts[1]).strip()
            location = _EMOJI_PATTERN.sub("", parts[2]).strip()
            # Find the apply link by scanning cells from the end (link is always
            # in the last populated cell; column count varies by source: Visa, Salary, etc.)
            link_cell = ""
            age = ""
            for _cell in reversed(parts[2:]):
                if _HTML_LINK_PATTERN.search(_cell) or _MD_LINK_PATTERN.search(_cell) or _cell.startswith("http"):
                    link_cell = _cell
                    break
            # age = first cell after location that looks like a short duration
            for _cell in parts[3:]:
                _cs = _cell.strip()
                if re.match(r"^\d+[dhmw]", _cs):
                    age = _cs
                    break
                # calendar date column: "Aug 05", "Oct 22", "2026-08-01"
                if re.match(r"^[A-Za-z]{3}\s+\d{1,2}$", _cs) or re.match(r"^\d{4}-\d{2}-\d{2}$", _cs):
                    age = _cs
                    break
            match = _HTML_LINK_PATTERN.search(link_cell) or _MD_LINK_PATTERN.search(
                link_cell
            )
            url = (
                match.group(1)
                if match
                else (link_cell if link_cell.startswith("http") else None)
            )
            if not url or any(marker in line for marker in ["🔒", "❌", "closed"]):
                continue
            jobs.append(
                {
                    "company": company,
                    "title": title,
                    "location": location,
                    "url": url,
                    "age": age,
                    "is_closed": False,
                    "source": source_name,
                    "github_category": "",
                    "sponsorship": _sponsorship_from_row(line),
                }
            )
        return jobs

    @staticmethod
    def _parse_html_tables(soup, source_name):
        jobs = []
        last_company = ""
        for table in soup.find_all("table"):
            github_category = ""
            # Map the "Date Posted"/"Age" column by header name (not fixed index).
            _date_col = None
            _hdr_row = table.find("tr")
            if _hdr_row:
                _hdr_cells = _hdr_row.find_all(["th", "td"])
                for _hi, _hc in enumerate(_hdr_cells):
                    _htxt = _hc.get_text(strip=True).lower()
                    if "date" in _htxt or "posted" in _htxt or _htxt == "age":
                        _date_col = _hi
                        break
            prev = table.find_previous(["h2", "h3"])
            if prev:
                ht = prev.get_text(strip=True).lower()
                if "software" in ht and "internship" in ht:
                    github_category = "Software Engineering Internship"
                elif any(k in ht for k in ["data science", "machine learning", "ai"]):
                    github_category = "Data Science AI ML Internship"
            for row in table.find_all("tr")[1:]:
                cells = row.find_all("td")
                if len(cells) < 5:
                    continue
                company_link = cells[0].find("a")
                if company_link:
                    company = _EMOJI_PATTERN.sub("", company_link.get_text(strip=True))
                    last_company = company
                else:
                    # Sub-listing (↳) — inherit company from parent row
                    cell_text = cells[0].get_text(strip=True)
                    if "↳" in cell_text or not cell_text.strip():
                        company = last_company
                    else:
                        continue
                title = _EMOJI_PATTERN.sub("", cells[1].get_text(strip=True))
                location = _EMOJI_PATTERN.sub("", cells[2].get_text(strip=True))
                if _date_col is not None and _date_col < len(cells):
                    age = cells[_date_col].get_text(strip=True)
                elif len(cells) >= 5:
                    age = cells[4].get_text(strip=True)
                else:
                    age = ""
                apply_link = None
                for _c in cells[2:]:
                    _a = _c.find("a", href=True)
                    if _a and _a.get("href", "").startswith("http"):
                        apply_link = _a
                        break
                if not apply_link:
                    continue
                url = apply_link.get("href", "")
                is_closed = "🔒" in str(row)
                jobs.append(
                    {
                        "company": company,
                        "title": title,
                        "location": location,
                        "url": url,
                        "age": age,
                        "is_closed": is_closed,
                        "source": source_name,
                        "github_category": github_category,
                    }
                )
        return jobs
