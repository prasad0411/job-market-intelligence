#!/usr/bin/env python3
"""
Workday subdomain -> company name, DERIVED from what the pipeline has learned.

WHY THIS EXISTS
Two hardcoded maps did this job before: _WORKDAY_COMPANY_MAP in url_validator
(48 entries) and _known_workday_companies inside a function in run_aggregator
(40 entries). They shared 3 slugs, disagreed on 1, and neither contained
"disney" - which is why a Kodiak row kept Disney's title and Disney's URL and
nothing caught it.

Meanwhile brain.json already holds 227 workday tenants that ats_discovery
found on its own, disney among them. The hand maintained lists were a smaller,
staler copy of data the system was already collecting.

So this module derives the map at runtime and keeps the old entries only as a
seed. It gets better every night discovery runs, with nothing to maintain.

    from aggregator.workday_map import company_for_slug
    company_for_slug("disney")   -> "The Walt Disney Company"
"""
import json
import os
import re
import threading

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BRAIN = os.path.join(BASE, ".local", "brain.json")

# Seed. These are the entries the two hardcoded maps carried that discovery
# has not learned yet, plus the ones where a human knew better than the slug
# (haier -> GE Appliances, edel -> Oracle). Discovery overrides nothing here;
# these only fill gaps.
_SEED = {
    "vst": "Vistra", "msigna": "MSIG USA", "haier": "GE Appliances",
    "edel": "Oracle", "ulse": "UL Solutions", "kbr": "KBR",
}

_lock = threading.Lock()
_cache = {"map": None, "mtime": 0.0}


def _slug_from_key(key):
    """discovered_ats keys look like 'disney|wd5|disneycareer'."""
    return str(key).split("|")[0].strip().lower()


def _norm(s):
    return re.sub(r"[^a-z0-9]", "", str(s or "").lower())


def _load():
    """Build the map, refreshing when brain.json changes."""
    try:
        mtime = os.path.getmtime(BRAIN)
    except OSError:
        mtime = 0.0
    with _lock:
        if _cache["map"] is not None and _cache["mtime"] == mtime:
            return _cache["map"]

        out = dict(_SEED)
        try:
            with open(BRAIN, encoding="utf-8") as f:
                data = json.load(f)
            tenants = (data.get("discovered_ats") or {}).get("workday_tenants") or {}
            for key, name in tenants.items():
                slug = _slug_from_key(key)
                if not slug or not name:
                    continue
                # A tenant whose "name" is just the slug carries no information
                # (scgov -> scgov). Keep it only if nothing better is known.
                if _norm(name) == _norm(slug) and slug in out:
                    continue
                # A SEED entry is a human correction and outranks discovery.
                # Discovery learned "vst" -> "Ohio State University" from a
                # page that happened to mention it; the seed says Vistra and
                # the seed is right. Without this, a single bad crawl
                # silently overwrites a known good mapping.
                if slug in _SEED:
                    continue
                out[slug] = name
        except Exception:
            pass

        _cache["map"] = out
        _cache["mtime"] = mtime
        return out


def company_for_slug(slug):
    """Company name for a workday subdomain, or None."""
    if not slug:
        return None
    return _load().get(str(slug).strip().lower())


def known_slugs():
    return set(_load())


def size():
    return len(_load())


def slug_from_url(url):
    """Extract the workday subdomain from a url, or None."""
    if not url or "myworkdayjobs" not in str(url).lower():
        return None
    m = re.search(r"https?://([a-z0-9\-]+)\.wd\d+\.myworkdayjobs\.com",
                  str(url), re.I)
    return m.group(1).lower() if m else None


def company_for_url(url):
    return company_for_slug(slug_from_url(url))


if __name__ == "__main__":
    m = _load()
    print("  workday map entries:", len(m))
    print("  (was 48 in url_validator + 40 in run_aggregator, 3 shared)")
    print()
    for s in ("disney", "philips", "cadence", "micron", "fanniemae",
              "haier", "vst", "kodiak"):
        print("   {:<12} -> {}".format(s, company_for_slug(s)))
    print()
    u = ("https://disney.wd5.myworkdayjobs.com/disneycareer/job/"
         "Lake-Buena-Vista-FL-USA/Environmental-Sustainability-Data-Analytics-Intern")
    print("  slug_from_url  ->", slug_from_url(u))
    print("  company_for_url->", company_for_url(u))
