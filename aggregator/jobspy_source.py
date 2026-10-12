"""
Indeed via JobSpy.

Scope is deliberately narrow. JobSpy also scrapes LinkedIn, Glassdoor and
ZipRecruiter, but LinkedIn rate-limits hard without paid proxies, Glassdoor
blocks aggressively, and ZipRecruiter is already covered. Indeed is the one
its own docs describe as having no rate limiting — and it is the one source
category this pipeline has zero coverage of, since Indeed employers often
never touch Greenhouse/Lever or the GitHub lists.

Returns the same dict shape as direct_sources so the rest of the pipeline
needs no special handling. Every failure is swallowed: a missing package or
a bad response must never break a run.
"""
import logging
import datetime as _dt
from typing import List, Dict

log = logging.getLogger(__name__)

# Match the pipeline's freshness window (config.MAX_JOB_AGE_DAYS)
HOURS_OLD = 72
RESULTS_PER_QUERY = 60

# Separate queries beat one broad query: Indeed searches descriptions too,
# so a single query returns a lot of noise.
QUERIES = [
    '"software engineer" ("new grad" OR "entry level" OR "university graduate") -senior -staff -principal -manager',
    '"software engineer i" OR "software engineer 1" -senior -staff -principal',
    '"associate software engineer" OR "junior software engineer" -senior -contract',
    '"software engineer intern" OR "software engineering intern" 2027',
    '"data engineer" ("new grad" OR "entry level") -senior -staff -manager',
    '"machine learning engineer" ("new grad" OR "entry level") -senior -staff',
]


def _age_string(date_posted) -> str:
    """Convert JobSpy's date_posted to the '<N>d' form the age gate expects."""
    if date_posted is None:
        return "unknown"
    try:
        if hasattr(date_posted, "date"):
            d = date_posted.date()
        elif isinstance(date_posted, _dt.date):
            d = date_posted
        else:
            d = _dt.date.fromisoformat(str(date_posted)[:10])
        days = (_dt.date.today() - d).days
        if days < 0 or days > 3650:
            return "unknown"
        return "{}d".format(days)
    except Exception:
        return "unknown"


def scrape_indeed() -> List[Dict]:
    """Fetch fresh Indeed postings. Returns [] on any failure."""
    try:
        from jobspy import scrape_jobs
    except ImportError:
        log.info("JobSpy not installed - skipping Indeed (pip install python-jobspy)")
        return []

    jobs, seen = [], set()

    for q in QUERIES:
        try:
            df = scrape_jobs(
                site_name=["indeed"],
                search_term=q,
                location="United States",
                results_wanted=RESULTS_PER_QUERY,
                hours_old=HOURS_OLD,
                country_indeed="USA",
                verbose=0,
            )
        except Exception as e:
            log.warning("Indeed query failed (%s): %s", q[:40], str(e)[:90])
            continue

        if df is None or len(df) == 0:
            continue

        for _, r in df.iterrows():
            try:
                url = str(r.get("job_url") or "").strip()
                company = str(r.get("company") or "").strip()
                title = str(r.get("title") or "").strip()
                if not url or not company or not title:
                    continue
                if url in seen:
                    continue
                seen.add(url)

                loc = r.get("location")
                location = str(loc).replace(", US", "").strip() if loc else "Unknown"

                jobs.append({
                    "company": company,
                    "title": title,
                    "location": location or "Unknown",
                    "url": url,
                    "job_id": str(r.get("id") or "N/A"),
                    "source": "indeed_direct",
                    "age": _age_string(r.get("date_posted")),
                    "is_closed": False,
                    "sponsorship": "Unknown",
                })
            except Exception:
                continue

    log.info("Indeed (JobSpy): %d unique jobs from %d queries", len(jobs), len(QUERIES))
    return jobs
