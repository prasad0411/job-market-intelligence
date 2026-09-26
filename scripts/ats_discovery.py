#!/usr/bin/env python3
"""
ATS Auto-Discovery Engine — the pipeline grows its own source list.

How it works:
1. After every pipeline run, scans ALL URLs processed (valid + discarded)
2. Extracts company slugs from Greenhouse/Lever/Ashby URLs
3. Checks if those companies are already in our direct_sources.py
4. If NOT, tests the API to see if they have intern/newgrad roles
5. If they DO, adds them to brain.json → next run includes them
6. Also periodically scans for NEW companies on each platform

The system gets bigger every single day without human intervention.
"""

# Socket ceiling, signal handling, stall watchdog. The repo root has to
# go on sys.path first: these run as `python3 scripts/NAME.py`, so
# Python puts scripts/ on the path, not the root, and a plain
# `import scripts._resilient` raises ModuleNotFoundError.
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import scripts._resilient  # noqa: E402,F401


import json
import logging
import os
import re
import time
import urllib.request
import ssl
from typing import Dict, Set

log = logging.getLogger(__name__)
_CTX = ssl.create_default_context()
BRAIN_FILE = ".local/brain.json"


def _fetch_json(url, timeout=5):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        resp = urllib.request.urlopen(req, timeout=timeout, context=_CTX)
        return json.loads(resp.read())
    except Exception:
        return None


def _has_intern_roles(jobs, platform="greenhouse"):
    """Check if job list has relevant tech roles (intern, new grad, OR full-time SWE)."""
    kw = ["intern", "co-op", "coop", "new grad", "entry level",
          "junior", "early career", "apprentice",
          "software engineer", "software developer", "swe", "sde",
          "data engineer", "data scientist", "machine learning",
          "full stack", "backend", "front end", "frontend", "developer"]
    for j in jobs:
        title = ""
        if platform == "greenhouse":
            title = j.get("title", "")
        elif platform == "lever":
            title = j.get("text", "")
        elif platform == "ashby":
            title = j.get("title", "")
        elif platform == "workable":
            title = j.get("title", "")
        elif platform == "recruitee":
            title = j.get("title", "")
        elif platform == "rippling":
            title = j.get("name", "")
        elif platform == "smartrecruiters":
            title = j.get("name", "")
        if any(k in title.lower() for k in kw):
            return True
    return False


class ATSDiscoveryEngine:
    def __init__(self):
        self.brain = self._load_brain()
        self.discovered = self.brain.get("discovered_ats", {
            "greenhouse": {}, "lever": {}, "ashby": {}
        })
        self.known_slugs = self.brain.get("known_ats_slugs", {
            "greenhouse": set(), "lever": set(), "ashby": set()
        })
        # Convert lists back to sets (JSON doesn't support sets)
        for platform in self.known_slugs:
            if isinstance(self.known_slugs[platform], list):
                self.known_slugs[platform] = set(self.known_slugs[platform])

    def _load_brain(self):
        try:
            if os.path.exists(BRAIN_FILE):
                with open(BRAIN_FILE) as f:
                    return json.load(f)
        except Exception:
            pass
        return {}

    def _save_brain(self):
        # Re-read current brain so we never clobber writes made by other
        # processes (outreach Brain) while discovery was running.
        current = self._load_brain() or {}
        current["discovered_ats"] = self.discovered
        current["known_ats_slugs"] = {
            k: list(v) for k, v in self.known_slugs.items()
        }
        self.brain = current
        tmp = BRAIN_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(current, f, indent=2)
        os.replace(tmp, BRAIN_FILE)

    def extract_slugs_from_urls(self, urls):
        """Extract ATS company slugs from a list of URLs."""
        new_slugs = {"greenhouse": set(), "lever": set(), "ashby": set(),
                     "workable": set(), "recruitee": set(), "smartrecruiters": set(),
                     "rippling": set()}

        for url in urls:
            url_lower = url.lower()

            # Greenhouse: boards.greenhouse.io/{slug}/ or job-boards.greenhouse.io/{slug}/
            gh_match = re.search(r"(?:boards|job-boards|boards-api)\.greenhouse\.io/(?:v1/boards/)?([a-z0-9_-]+)", url_lower)
            if gh_match:
                slug = gh_match.group(1)
                if slug not in ("embed", "internal", "api", "v1", "boards"):
                    new_slugs["greenhouse"].add(slug)

            # Lever: jobs.lever.co/{slug}/
            lv_match = re.search(r"(?:jobs|api)\.lever\.co/(?:v0/postings/)?([a-z0-9_-]+)", url_lower)
            if lv_match:
                new_slugs["lever"].add(lv_match.group(1))

            # Ashby: jobs.ashbyhq.com/{slug}/
            ab_match = re.search(r"ashbyhq\.com/(?:posting-api/job-board/)?([a-z0-9._-]+)", url_lower)
            if ab_match:
                slug = ab_match.group(1)
                if slug not in ("api", "embed", "posting-api", "job-board"):
                    new_slugs["ashby"].add(slug)

            # Workable: {slug}.workable.com or apply.workable.com/{slug}
            wk_match = re.search(r"(?:apply\.workable\.com/|([a-z0-9_-]+)\.workable\.com)", url_lower)
            if wk_match:
                slug = wk_match.group(1) or re.search(r"apply\.workable\.com/([a-z0-9_-]+)", url_lower)
                slug = slug if isinstance(slug, str) else (slug.group(1) if slug else None)
                if slug and slug not in ("www", "apply", "api"):
                    new_slugs["workable"].add(slug)

            # Rippling: ats.rippling.com/{slug}/jobs/... (skip locale prefixes)
            rp_match = re.search(r"ats\.rippling\.com/(?:[a-z]{2}-[a-z0-9]+/)?([a-z0-9_-]+)", url_lower)
            if rp_match and rp_match.group(1) not in ("jobs", "api", "rippling"):
                new_slugs["rippling"].add(rp_match.group(1))

            # Recruitee: {slug}.recruitee.com
            rc_match = re.search(r"([a-z0-9_-]+)\.recruitee\.com", url_lower)
            if rc_match and rc_match.group(1) not in ("www", "api"):
                new_slugs["recruitee"].add(rc_match.group(1))

            # SmartRecruiters: careers.smartrecruiters.com/{slug} or jobs.smartrecruiters.com/{slug}
            sr_match = re.search(r"(?:careers|jobs|api)\.smartrecruiters\.com/(?:v1/companies/)?([a-z0-9_-]+)", url_lower)
            if sr_match and sr_match.group(1) not in ("v1", "companies", "api"):
                new_slugs["smartrecruiters"].add(sr_match.group(1))

        return new_slugs

    def check_and_add_new_companies(self, new_slugs):
        """Test new slugs against APIs and add if they have intern roles."""
        added = 0

        for platform, slugs in new_slugs.items():
            for slug in slugs:
                # Skip if already known
                if slug in self.known_slugs.get(platform, set()):
                    continue
                if slug in self.discovered.get(platform, {}):
                    continue

                # Test the API
                has_roles = False
                company_name = slug.replace("-", " ").title()

                if platform == "greenhouse":
                    data = _fetch_json(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs", timeout=3)
                    if data and data.get("jobs"):
                        has_roles = _has_intern_roles(data["jobs"], "greenhouse")
                        # Try to get proper company name
                        if data["jobs"]:
                            loc = data["jobs"][0].get("location", {})
                            # Company name from board info
                            board = _fetch_json(f"https://boards-api.greenhouse.io/v1/boards/{slug}", timeout=3)
                            if board:
                                company_name = board.get("name", company_name)

                elif platform == "lever":
                    data = _fetch_json(f"https://api.lever.co/v0/postings/{slug}?mode=json", timeout=3)
                    if data and isinstance(data, list):
                        has_roles = _has_intern_roles(data, "lever")

                elif platform == "ashby":
                    data = _fetch_json(f"https://api.ashbyhq.com/posting-api/job-board/{slug}", timeout=3)
                    if data and data.get("jobs"):
                        has_roles = _has_intern_roles(data["jobs"], "ashby")

                elif platform == "workable":
                    data = _fetch_json(f"https://apply.workable.com/api/v1/widget/accounts/{slug}", timeout=3)
                    if data and data.get("jobs"):
                        has_roles = _has_intern_roles(data["jobs"], "workable")

                elif platform == "recruitee":
                    data = _fetch_json(f"https://{slug}.recruitee.com/api/offers/", timeout=3)
                    if data and data.get("offers"):
                        has_roles = _has_intern_roles(data["offers"], "recruitee")

                elif platform == "rippling":
                    data = _fetch_json(f"https://api.rippling.com/platform/api/ats/v1/board/{slug}/jobs", timeout=5)
                    if isinstance(data, list) and data:
                        has_roles = _has_intern_roles(data, "rippling")

                elif platform == "smartrecruiters":
                    data = _fetch_json(f"https://api.smartrecruiters.com/v1/companies/{slug}/postings", timeout=3)
                    if data and data.get("content"):
                        has_roles = _has_intern_roles(data["content"], "smartrecruiters")

                # Track this slug as known (even if no intern roles — don't recheck)
                self.known_slugs.setdefault(platform, set()).add(slug)

                if has_roles:
                    self.discovered.setdefault(platform, {})[slug] = company_name
                    added += 1
                    log.info(f"DISCOVERED: {platform}/{slug} → {company_name} (has intern/newgrad roles)")

                time.sleep(0.2)  # Rate limit

        self._save_brain()
        return added

    def get_all_discovered(self):
        """Return all discovered companies for each platform."""
        return self.discovered

    def run_discovery_from_sheets(self):
        """Extract URLs from Google Sheets and discover new companies."""
        try:
            import gspread
            from google.oauth2.service_account import Credentials

            scopes = ["https://www.googleapis.com/auth/spreadsheets",
                      "https://www.googleapis.com/auth/drive"]
            creds = Credentials.from_service_account_file(
                ".local/credentials.json", scopes=scopes)
            gc = gspread.authorize(creds)
            ss = gc.open("H1B visa")

            all_urls = []

            # Scan valid entries
            valid = ss.worksheet("Valid Entries").get_all_values()
            for row in valid[1:]:
                if len(row) > 5 and row[5].strip().startswith("http"):
                    all_urls.append(row[5].strip())

            # Scan discarded entries
            disc = ss.worksheet("Discarded Entries").get_all_values()
            for row in disc[1:]:
                if len(row) > 5 and row[5].strip().startswith("http"):
                    all_urls.append(row[5].strip())

            # Reviewed - Not Applied sheet (URLs that never reached Valid)
            try:
                rev = ss.worksheet("Reviewed - Not Applied").get_all_values()
                for row in rev[1:]:
                    if len(row) > 5 and row[5].strip().startswith("http"):
                        all_urls.append(row[5].strip())
            except Exception:
                pass

            # Raw skipped-jobs log: thousands of ATS URLs filtered out pre-sheet
            try:
                import re as _rre, os as _ros
                _log_path = ".local/skipped_jobs.log"
                if _ros.path.exists(_log_path):
                    with open(_log_path, "r", errors="ignore") as _lf:
                        _txt = _lf.read()
                    _found = _rre.findall(r"https?://[^\s\"']+", _txt)
                    _ats = [u for u in _found if any(d in u.lower() for d in
                            ("greenhouse", "lever.co", "ashbyhq", "workable",
                             "recruitee", "smartrecruiters"))]
                    all_urls.extend(_ats)
                    log.info(f"Added {len(_ats)} ATS URLs from skipped_jobs.log")
            except Exception as _e:
                log.debug(f"skipped_jobs.log scan failed: {_e}")

            log.info(f"Scanning {len(all_urls)} URLs for ATS company slugs")
            new_slugs = self.extract_slugs_from_urls(all_urls)

            total_new = sum(len(v) for v in new_slugs.values())
            total_known = sum(len(v) for v in self.known_slugs.values())
            log.info(f"Found {total_new} slugs ({total_known} already known)")

            added = self.check_and_add_new_companies(new_slugs)
            log.info(f"Added {added} new companies to discovery cache")

            return added

        except Exception as e:
            log.error(f"Discovery from sheets failed: {e}")
            return 0



def main():
    import logging
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(message)s")
    engine = ATSDiscoveryEngine()
    added = engine.run_discovery_from_sheets()
    logging.info(f"Discovery complete: {added} new companies added")
    
    # Report totals
    discovered = engine.get_all_discovered()
    for platform, companies in discovered.items():
        if companies:
            logging.info(f"  {platform}: {len(companies)} discovered companies")


if __name__ == "__main__":
    main()
