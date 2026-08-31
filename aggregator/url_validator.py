"""
URL-Company Validator — Self-healing job data correction.

Extracts the real company and title from the job URL and corrects
mismatches caused by SWE List row shifts or Simplify redirect bugs.
Never rejects jobs — only corrects metadata.

Usage:
    from aggregator.url_validator import validate_job
    job = validate_job(job_dict)  # corrects company/title in-place
"""
import re
import logging
import json
import os
from urllib.parse import urlparse, unquote

log = logging.getLogger(__name__)

_BRAIN_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".local", "brain.json")

# ATS platforms where company is in the subdomain
_WORKDAY_PATTERN = re.compile(r"^([a-z0-9-]+)\.wd\d+\.myworkday", re.I)
_WORKDAY_SITE_PATTERN = re.compile(r"myworkdaysite\.com/recruiting/([a-z0-9-]+)/", re.I)

# ATS platforms where company is in the URL path
_PATH_ATS = {
    "greenhouse.io": r"/(?:job-boards\.(?:eu\.)?greenhouse\.io|boards\.greenhouse\.io)/([^/]+)/",
    "lever.co": r"jobs\.lever\.co/([^/]+)/",
    "ashbyhq.com": r"jobs\.ashbyhq\.com/([^/]+)/",
    "workable.com": r"apply\.workable\.com/([^/]+)/",
    "icims.com": r"careers-([^.]+)\.icims\.com/",
    "rippling.com": r"ats\.rippling\.com/([^/]+)/",
    "smartrecruiters.com": r"jobs\.smartrecruiters\.com/([^/]+)/",
}

# Known ATS names that are NOT companies
_ATS_NAMES = {
    "greenhouse", "lever", "ashbyhq", "workable", "icims", "rippling",
    "smartrecruiters", "ultipro", "jobvite", "myworkdayjobs", "oraclecloud",
    "successfactors", "bamboohr", "applytojob", "taleo", "recruiting",
}

# Slug cleanup patterns
_SLUG_NOISE = re.compile(
    r"(utm_source|ref|gh_src|gh_jid|mobile|needsRedirect|apply|application|job|jobs|careers|external|en|sites|CX_\d+|hcmUI|CandidateExperience)$",
    re.I,
)


# Workday subdomain -> company. DERIVED from brain.json's
# discovered_ats (227 tenants and growing), not a hand kept list.
# The 48-entry dict that lived here shared only 3 slugs with the
# 40-entry copy in run_aggregator, disagreed on 1, and neither had
# 'disney' - which is why a Kodiak row kept Disney's title and URL.
from aggregator.workday_map import company_for_slug as _wd_company


class _WorkdayMapProxy(dict):
    """Behaves like the old dict so call sites need no change."""
    def __contains__(self, k):
        return _wd_company(k) is not None
    def __getitem__(self, k):
        v = _wd_company(k)
        if v is None:
            raise KeyError(k)
        return v
    def get(self, k, default=None):
        v = _wd_company(k)
        return default if v is None else v


_WORKDAY_COMPANY_MAP = _WorkdayMapProxy()

# Known custom career site domains
_CUSTOM_CAREER_DOMAINS = {
    "mhicareers.com": "Mitsubishi Heavy Industries",
    "fahertybrand.com": "Faherty Brand",
    "amazon.jobs": "Amazon",
    "tesla.com": "Tesla",
}


def _load_url_cache():
    """Load URL-company cache from brain.json."""
    try:
        if os.path.exists(_BRAIN_PATH):
            brain = json.load(open(_BRAIN_PATH))
            return brain.get("url_company_cache", {})
    except Exception:
        pass
    return {}


def _save_url_cache(cache):
    """Save URL-company cache through Brain's locked, atomic writer.

    This used to open brain.json with "w" directly: no lock, no temp file.
    That truncates the file in place, so any concurrent reader saw a partial
    file and any concurrent writer produced interleaved JSON. It is what
    produced brain.json.corrupt_20260827 (one trailing brace too many) and
    the 31 Aug wipe that cost 722 companies and the whole blacklist.
    """
    try:
        from outreach.brain import Brain
        b = Brain.get()
        b._data["url_company_cache"] = cache
        b.save()
    except Exception:
        pass


def extract_company_from_url(url):
    """Extract company name from job URL domain/path."""
    if not url:
        return None

    try:
        parsed = urlparse(url)
        domain = parsed.netloc.lower().replace("www.", "")
        path = unquote(parsed.path)
    except Exception:
        return None

    # Check custom career domains first (highest priority)
    for cd, cn in _CUSTOM_CAREER_DOMAINS.items():
        if cd in domain:
            return cn

    # Workday: check known company map BEFORE cache
    m = _WORKDAY_PATTERN.match(domain)
    if m:
        slug = m.group(1).lower()
        if slug in _WORKDAY_COMPANY_MAP:
            return _WORKDAY_COMPANY_MAP[slug]

    # Check URL-company cache
    cache = _load_url_cache()
    if "myworkdayjobs" in domain or "myworkdaysite" in domain:
        cache_key = domain.split(".")[0]
    elif any(ats in domain for ats in ["greenhouse", "lever", "ashby", "workable", "icims", "smartrecruiters"]):
        path_parts = [p for p in path.split("/") if p and len(p) > 2]
        slug = path_parts[0] if path_parts else ""
        cache_key = f"{domain}/{slug}" if slug else None
    else:
        cache_key = domain
    if cache_key and cache_key in cache:
        return cache[cache_key]
        if slug not in _ATS_NAMES and len(slug) > 2:
            company = slug.replace("-", " ").replace("_", " ").strip().title()
            return company

    # Workday site variant
    m = _WORKDAY_SITE_PATTERN.search(url)
    if m:
        slug = m.group(1).lower()
        if slug not in _ATS_NAMES:
            return slug.replace("-", " ").title()

    # Path-based ATS
    for ats, pattern in _PATH_ATS.items():
        if ats.split(".")[0] in domain:
            m = re.search(pattern, url, re.I)
            if m:
                slug = m.group(1).lower()
                if slug not in _ATS_NAMES and len(slug) > 2:
                    return slug.replace("-", " ").replace("_", " ").strip().title()

    # Oracle Cloud
    if "oraclecloud.com" in domain:
        m = re.match(r"([a-z0-9-]+)\.fa\.", domain)
        if m and m.group(1) not in _ATS_NAMES:
            _oc_slug = m.group(1).lower()
            if _oc_slug in _WORKDAY_COMPANY_MAP:
                return _WORKDAY_COMPANY_MAP[_oc_slug]
            return _oc_slug.replace("-", " ").title()

    # (Amazon, Tesla, NVIDIA handled by _CUSTOM_CAREER_DOMAINS above)

    return None


def _extract_location_from_url(url):
    """Extract location from URL path (Workday URLs often have city-state in path)."""
    if not url:
        return None
    try:
        from urllib.parse import urlparse, unquote
        path = unquote(urlparse(url).path)
        # Workday pattern: /job/City-State/Title_ID
        import re
        m = re.search(r"/job/([A-Z][a-z]+-[A-Z][a-z]+(?:-[A-Z][a-z]+)*(?:-[A-Z]{2})?)(?:/|-)", path)
        if m:
            loc = m.group(1)
            parts = loc.split("-")
            if len(parts) >= 2:
                state = parts[-1]
                if len(state) == 2 and state.isalpha():
                    city = " ".join(parts[:-1])
                    return f"{city}, {state.upper()}"
        # Workday pattern with state code: /City-State-Zipcode/
        m2 = re.search(r"/([A-Z][a-z]+-(?:[A-Z][a-z]+-)?[A-Z]{2})-\d{5}", path)
        if m2:
            loc = m2.group(1)
            parts = loc.rsplit("-", 1)
            if len(parts) == 2:
                return f"{parts[0].replace('-', ' ')}, {parts[1]}"
    except Exception:
        pass
    return None


def extract_title_from_url(url):
    """Extract job title from URL path slug."""
    if not url:
        return None

    try:
        path = unquote(urlparse(url).path)
        segments = [s for s in path.split("/") if s and len(s) > 5]
        if not segments:
            return None

        # Take last meaningful segment (usually the job title slug)
        slug = segments[-1]

        # Remove job IDs (hex, numeric, mixed)
        slug = re.sub(r"^[a-f0-9-]{8,}[-_]?", "", slug)
        slug = re.sub(r"[-_][A-Z0-9]{5,}$", "", slug)  # trailing IDs like _REQ-4462
        slug = re.sub(r"[-_]R\d{5,}$", "", slug)
        slug = re.sub(r"[-_]JR\d{4,}$", "", slug)
        slug = re.sub(r"[-_]\d{6,}$", "", slug)

        # Convert slug to title
        title = slug.replace("-", " ").replace("_", " ").strip()

        # Remove noise words
        title = re.sub(r"\b(utm source|ref|simplify|apply|application)\b", "", title, flags=re.I).strip()

        if len(title) >= 8:
            return title.title()
    except Exception:
        pass

    return None


def _is_authoritative_match(url):
    """Check if URL company comes from a KNOWN mapping (high confidence only).
    Only returns True for Workday known map and custom career domains.
    Path-based ATS (Greenhouse, Lever, Ashby) use fuzzy matching instead."""
    try:
        from urllib.parse import urlparse
        parsed = urlparse(url)
        domain = parsed.netloc.lower().replace("www.", "")
        # Custom career domains — always authoritative
        for cd in _CUSTOM_CAREER_DOMAINS:
            if cd in domain:
                return True
        # Workday known map — always authoritative
        m = _WORKDAY_PATTERN.match(domain)
        if m and m.group(1).lower() in _WORKDAY_COMPANY_MAP:
            return True
    except Exception:
        pass
    return False


def _normalize(s):
    """Normalize string for comparison."""
    return re.sub(r"[^a-z0-9]", "", s.lower()) if s else ""


def _fuzzy_match(a, b):
    """Smart fuzzy match for company names.
    
    Key insight: URL slugs often append/prepend words to company names.
    'gelbergroup' contains 'gelber', so they match.
    'astranis' does NOT contain 'sieve', so they don't match.
    """
    na, nb = _normalize(a), _normalize(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    # Substring check: if one is fully contained in the other, it's a match
    if na in nb or nb in na:
        return 0.9
    # Check if the shorter string is a prefix of the longer
    shorter, longer = (na, nb) if len(na) <= len(nb) else (nb, na)
    if longer.startswith(shorter):
        return 0.85
    # Levenshtein-based: for similar-length strings
    if abs(len(na) - len(nb)) <= 2:
        dist = _edit_distance(na, nb)
        max_len = max(len(na), len(nb))
        if max_len > 0:
            ratio = 1.0 - (dist / max_len)
            return ratio
    # Low similarity for completely different strings
    # Only count if significant character sequences overlap
    common_len = _longest_common_substring_len(na, nb)
    return common_len / max(len(na), len(nb))


def _edit_distance(s1, s2):
    """Levenshtein distance."""
    if len(s1) < len(s2):
        return _edit_distance(s2, s1)
    if len(s2) == 0:
        return len(s1)
    prev = list(range(len(s2) + 1))
    for i, c1 in enumerate(s1):
        curr = [i + 1]
        for j, c2 in enumerate(s2):
            curr.append(min(prev[j + 1] + 1, curr[j] + 1, prev[j] + (c1 != c2)))
        prev = curr
    return prev[len(s2)]


def _longest_common_substring_len(a, b):
    """Length of longest common substring."""
    if not a or not b:
        return 0
    m, n = len(a), len(b)
    prev = [0] * (n + 1)
    best = 0
    for i in range(1, m + 1):
        curr = [0] * (n + 1)
        for j in range(1, n + 1):
            if a[i-1] == b[j-1]:
                curr[j] = prev[j-1] + 1
                best = max(best, curr[j])
        prev = curr
    return best


def validate_job(job):
    """
    Validate and fix a job dict. Corrects company/title from URL if mismatched.
    Never rejects — only corrects. Returns the (possibly modified) job dict.
    
    Expected keys: company, title, url
    """
    url = job.get("url", "")
    hint_company = job.get("company", "Unknown")

    if not url or not url.startswith("http"):
        return job  # Can't validate without URL

    # Extract real company from URL
    url_company = extract_company_from_url(url)

    if url_company:
        hint_norm = _normalize(hint_company)
        url_norm = _normalize(url_company)

        # Check if URL company is from an authoritative source (known map)
        is_authoritative = _is_authoritative_match(url)

        # Check if they match
        similarity = _fuzzy_match(hint_company, url_company)

        if (is_authoritative and hint_norm != url_norm) or (similarity < 0.4 and url_norm not in _ATS_NAMES):
            # Clear mismatch — URL company wins
            log.info(
                f"URL-COMPANY FIX: '{hint_company}' -> '{url_company}' "
                f"(similarity={similarity:.2f}, url={url[:60]})"
            )
            job["company"] = url_company
            job["_company_source"] = "url_validator"
            job["_was_mismatched"] = True

            # When company mismatches, ALL hint data is suspect (row-shift bug)
            # Re-extract title from URL
            url_title = extract_title_from_url(url)
            if url_title and len(url_title) > 8:
                old_title = job.get("title", "")
                title_sim = _fuzzy_match(old_title, url_title)
                if title_sim < 0.3:
                    log.info(f"URL-TITLE FIX: '{old_title}' -> '{url_title}'")
                    job["title"] = url_title
                    job["_title_source"] = "url_validator"

            # Extract location from URL if possible (Workday URLs have city in path)
            url_location = _extract_location_from_url(url)
            if url_location:
                old_loc = job.get("location", "Unknown")
                if old_loc != url_location:
                    log.info(f"URL-LOCATION FIX: '{old_loc}' -> '{url_location}'")
                    job["location"] = url_location

            # Clear job_id if it came from a shifted row
            job_id = job.get("job_id", "N/A")
            if job_id and job_id != "N/A":
                # Check if job_id appears in the URL — if not, it's from wrong row
                if job_id not in url:
                    log.info(f"URL-JOBID CLEAR: '{job_id}' not in URL, clearing")
                    job["job_id"] = "N/A"

        # Self-learning: save correction to log (append-only, crash-safe)
        if job.get("_was_mismatched"):
            try:
                from aggregator.correction_log_patch import append_correction
                append_correction({
                    "from": hint_company,
                    "to": url_company,
                    "url": url[:100],
                })
            except Exception:
                pass

        # Self-learning: cache this URL pattern -> company mapping
        try:
            cache = _load_url_cache()
            parsed = urlparse(url)
            domain = parsed.netloc.lower().replace("www.", "")
            # For Workday: use subdomain as key (unique per company)
            if "myworkdayjobs" in domain or "myworkdaysite" in domain:
                cache_key = domain.split(".")[0]
            # For path-based ATS: use domain + company slug as key
            elif any(ats in domain for ats in ["greenhouse", "lever", "ashby", "workable", "icims", "smartrecruiters"]):
                path_parts = [p for p in parsed.path.split("/") if p and len(p) > 2]
                slug = path_parts[0] if path_parts else ""
                cache_key = f"{domain}/{slug}" if slug else None
            else:
                cache_key = domain
            if url_company and cache_key and cache_key not in cache:
                cache[cache_key] = url_company
                _save_url_cache(cache)
        except Exception:
            pass

    return job


def validate_job_integrity(job):
    """
    Final integrity check before writing to sheets.
    Returns (is_valid, reason) tuple.
    """
    company = job.get("company", "")
    title = job.get("title", "")
    url = job.get("url", "")

    if not company or company == "Unknown":
        return False, "Empty company name"
    if not title or title == "Unknown":
        return False, "Empty title"
    if not url or not url.startswith("http"):
        return False, "Invalid URL"
    if len(company) < 2:
        return False, f"Company too short: {company}"
    if len(title) < 3:
        return False, f"Title too short: {title}"
    # Check for obviously wrong data
    if company.lower() in _ATS_NAMES:
        return False, f"Company is ATS name: {company}"

    # ── Column-shift guards (folded in from the unused contracts.py) ──
    # A shifted row puts a job title in the company field and a location in
    # the title field. Absurd lengths are the cheapest reliable signal, and
    # this runs at all 4 pre-write call sites.
    # Caps set from the real distribution across 2,202 sheet rows:
    #   company  max 49, p99 36, median 8   -> 70 leaves comfortable headroom
    #   title    max 91, p99 71, median 31  -> 130 likewise
    # The generic 100/200 from contracts.py was so loose it missed a 96-char
    # job title sitting in the company column.
    if len(company) > 70:
        return False, f"Company too long ({len(company)} chars) - likely a shifted row"
    if len(title) > 130:
        return False, f"Title too long ({len(title)} chars) - likely a shifted row"

    # Enum fields must hold one of their allowed values. A shifted row lands
    # a source name or a date here instead.
    # "Unknown" and "Manual" are values the pipeline legitimately writes, so
    # they belong in the allow-list. Verified against 2,202 live sheet rows -
    # a stricter list produced false rejections on real jobs.
    _rt = str(job.get("resume_type", "SDE") or "SDE")
    if _rt not in ("SDE", "ML", "DA", "Tailored", "Unknown", "Manual", ""):
        return False, f"Invalid resume_type: {_rt!r} - likely a shifted row"
    _oc = str(job.get("outcome", "valid") or "valid")
    if _oc not in ("valid", "discarded", "reviewed", "duplicate"):
        return False, f"Invalid outcome: {_oc!r} - likely a shifted row"
    _sp = str(job.get("sponsorship", "Unknown") or "Unknown")
    if _sp not in ("Yes", "No", "Unknown"):
        return False, f"Invalid sponsorship: {_sp!r} - likely a shifted row"

    return True, "OK"
