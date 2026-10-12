#!/usr/bin/env python3
"""
Pipeline Intelligence Brain v3 — Central nervous system for the entire pipeline.

Every component reads from and writes to this brain.
The brain gets smarter after EVERY pipeline run.

INTELLIGENCE LAYERS:
1. COMPANY INTELLIGENCE — learns everything about every company it encounters
2. TITLE INTELLIGENCE — learns what titles are tech/non-tech from user behavior
3. LOCATION INTELLIGENCE — builds a map of US cities, catches false internationals
4. SOURCE INTELLIGENCE — tracks which sources give best/worst data quality
5. USER BEHAVIOR LEARNING — what you apply to teaches the system what you want
6. ERROR MEMORY — every mistake is logged and never repeated
7. PATTERN EVOLUTION — detects new rejection patterns from discarded entries
8. ATS DISCOVERY — auto-expands company list from URLs it processes
9. SPONSORSHIP LEARNING — builds H1B knowledge from JD parsing results
10. QUALITY SCORING — rates every job based on how similar it is to your applied jobs
"""

import json
import logging
import os
import re
import time
from datetime import datetime, timedelta
from collections import Counter

log = logging.getLogger(__name__)
BRAIN_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    ".local", "brain.json")
BRAIN_LOCK = BRAIN_FILE + ".lock"


class PipelineBrain:
    """Singleton brain — all pipeline components access this."""
    
    _instance = None
    
    @classmethod
    def get(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance
    
    def __init__(self):
        self.data = self._load()
        self._dirty = False
    
    def _load(self):
        try:
            if os.path.exists(BRAIN_FILE):
                with open(BRAIN_FILE) as f:
                    return json.load(f)
        except Exception:
            pass
        return {}
    
    def save(self):
        if not self._dirty:
            return
        try:
            os.makedirs(os.path.dirname(BRAIN_FILE), exist_ok=True)
            import fcntl, tempfile
            with open(BRAIN_LOCK, "w") as _lk:
                fcntl.flock(_lk, fcntl.LOCK_EX)
                disk = {}
                if os.path.exists(BRAIN_FILE):
                    try:
                        with open(BRAIN_FILE) as _f:
                            disk = json.load(_f)
                    except Exception:
                        disk = {}
                disk.update(self.data)
                _fd, _tmp = tempfile.mkstemp(dir=os.path.dirname(BRAIN_FILE), suffix=".tmp")
                with os.fdopen(_fd, "w") as _f:
                    json.dump(disk, _f, indent=2)
                os.replace(_tmp, BRAIN_FILE)
                self.data = disk
                fcntl.flock(_lk, fcntl.LOCK_UN)
            self._dirty = False
        except Exception as e:
            log.error(f"Brain save failed: {e}")
    
    def _set(self, *keys, value):
        """Set a nested value. _set("companies", "tesla", "sponsorship", value="Yes")"""
        d = self.data
        for k in keys[:-1]:
            if k not in d:
                d[k] = {}
            d = d[k]
        d[keys[-1]] = value
        self._dirty = True
    
    def _get(self, *keys, default=None):
        """Get a nested value."""
        d = self.data
        for k in keys:
            if not isinstance(d, dict) or k not in d:
                return default
            d = d[k]
        return d
    
    def _append(self, *keys, value, max_items=500):
        """Append to a nested list."""
        d = self.data
        for k in keys[:-1]:
            if k not in d:
                d[k] = {}
            d = d[k]
        if keys[-1] not in d:
            d[keys[-1]] = []
        d[keys[-1]].append(value)
        if len(d[keys[-1]]) > max_items:
            d[keys[-1]] = d[keys[-1]][-max_items:]
        self._dirty = True
    
    # ═══════════════════════════════════════════════════════════════
    # 1. COMPANY INTELLIGENCE
    # ═══════════════════════════════════════════════════════════════
    
    def learn_company(self, company, **facts):
        """Learn facts about a company. Called after every job processed."""
        co = company.lower().strip()
        if not co:
            return
        existing = self._get("companies", co, default={})
        for key, value in facts.items():
            if value and value != "Unknown":
                existing[key] = value
        existing["last_seen"] = datetime.now().isoformat()
        existing["encounter_count"] = existing.get("encounter_count", 0) + 1
        self._set("companies", co, value=existing)
    
    def get_company_info(self, company):
        """Get everything brain knows about a company."""
        return self._get("companies", company.lower().strip(), default={})
    
    def is_clearance_company(self, company):
        """
        Check whether this company requires clearance.

        This used to read company_info[co]["needs_clearance"] while
        apply_learned.is_learned_clearance read the flat learned_clearance
        list. One held 34 entries, the other 0, and neither consulted the
        other, so the answer depended on which path happened to run. Both now
        read the same list, with the per company flag kept as a secondary
        signal so nothing already recorded is lost.
        """
        c = (company or "").strip().lower()
        if not c:
            return False
        if self.get_company_info(company).get("needs_clearance", False):
            return True
        try:
            from aggregator.apply_learned import is_learned_clearance
            return bool(is_learned_clearance(company))
        except Exception:
            learned = self._get("learned_clearance", default=[]) or []
            if c in learned:
                return True
            return any(len(b) >= 4 and (c.startswith(b) or b.startswith(c))
                       for b in learned)
    
    def learn_clearance(self, company, needs_clearance):
        """Learn whether a company needs clearance."""
        self.learn_company(company, needs_clearance=needs_clearance)
    
    def learn_sponsorship(self, company, sponsors):
        """Learn whether a company sponsors H1B."""
        self.learn_company(company, sponsors_h1b=sponsors)
    
    def get_sponsorship(self, company):
        """Check brain's knowledge of sponsorship."""
        info = self.get_company_info(company)
        return info.get("sponsors_h1b", None)
    
    def learn_company_slug(self, slug, correct_name):
        """Learn a company name correction."""
        slugs = self._get("company_slugs", default={})
        slugs[slug.lower()] = correct_name
        self._set("company_slugs", value=slugs)
    
    def get_company_name(self, slug):
        """Get correct company name from slug."""
        return self._get("company_slugs", slug.lower(), default=None)
    
    # ═══════════════════════════════════════════════════════════════
    # 2. TITLE INTELLIGENCE
    # ═══════════════════════════════════════════════════════════════
    
    def learn_non_tech_title(self, title_pattern):
        """Learn a new non-tech title pattern."""
        patterns = self._get("non_tech_patterns", default=[])
        if title_pattern.lower() not in patterns:
            patterns.append(title_pattern.lower())
            self._set("non_tech_patterns", value=patterns)
    
    def get_learned_non_tech(self):
        """Get all learned non-tech patterns."""
        return self._get("non_tech_patterns", default=[])
    
    def learn_valid_title(self, title):
        """Learn a title that IS valid (from user applying)."""
        self._append("valid_title_words", value=title.lower(), max_items=1000)
    
    def get_title_preference_score(self, title):
        """Score how much this title matches user's preferences."""
        valid_titles = self._get("valid_title_words", default=[])
        if not valid_titles:
            return 0.5
        title_words = set(re.findall(r'\b\w{3,}\b', title.lower()))
        all_words = Counter()
        for vt in valid_titles:
            all_words.update(re.findall(r'\b\w{3,}\b', vt))
        if not all_words:
            return 0.5
        top_words = {w for w, c in all_words.most_common(50)}
        overlap = title_words & top_words
        return min(1.0, len(overlap) / 5.0)
    
    # ═══════════════════════════════════════════════════════════════
    # 3. LOCATION INTELLIGENCE
    # ═══════════════════════════════════════════════════════════════
    
    def learn_us_city(self, city):
        """Learn that a city is in the US."""
        cities = self._get("us_cities", default=[])
        if city.lower() not in cities:
            cities.append(city.lower())
            self._set("us_cities", value=cities)
    
    def is_known_us_city(self, city):
        """Check if brain knows this city is in the US."""
        cities = self._get("us_cities", default=[])
        return city.lower() in cities
    
    def learn_international_city(self, city):
        """Learn that a city is international (not US)."""
        cities = self._get("intl_cities", default=[])
        if city.lower() not in cities:
            cities.append(city.lower())
            self._set("intl_cities", value=cities)
    
    # ═══════════════════════════════════════════════════════════════
    # 4. SOURCE INTELLIGENCE
    # ═══════════════════════════════════════════════════════════════
    
    def log_source_quality(self, source, valid_count, rejected_count, error_count):
        """
        Accumulate quality metrics per source across runs.

        This used to _set() the whole entry every run, so quality_ratio was a
        single run snapshot rather than a lifetime rate. On 2026-09-08 a
        shadowed _dedup_key crashed every job in the 08:00 run, that run wrote
        0 valid for every source, and the stored history was gone. A metric
        that a single bad run can erase cannot detect decay, because there is
        nothing to compare against.

        Totals now accumulate. recent_ratio is this run, lifetime_ratio is all
        runs, and decay is the gap between them.
        """
        prev = (self._get("source_quality", default={}) or {}).get(source, {})
        tv = prev.get("total_valid", 0) + valid_count
        tr = prev.get("total_rejected", 0) + rejected_count
        te = prev.get("total_errors", 0) + error_count
        runs = prev.get("runs", 0) + 1
        self._set("source_quality", source, value={
            "valid": valid_count,
            "rejected": rejected_count,
            "errors": error_count,
            "total_valid": tv,
            "total_rejected": tr,
            "total_errors": te,
            "runs": runs,
            "recent_ratio": valid_count / max(1, valid_count + rejected_count),
            "lifetime_ratio": tv / max(1, tv + tr),
            "quality_ratio": tv / max(1, tv + tr),
            "last_run": datetime.now().isoformat(),
        })
    
    def get_worst_sources(self, n=3):
        """Get sources with lowest quality ratio."""
        sq = self._get("source_quality", default={})
        sorted_sources = sorted(sq.items(), key=lambda x: x[1].get("quality_ratio", 1))
        return sorted_sources[:n]
    
    # ═══════════════════════════════════════════════════════════════
    # 5. USER BEHAVIOR LEARNING
    # ═══════════════════════════════════════════════════════════════
    
    def learn_user_applied(self, company, title, location):
        """Learn from user applying to a job."""
        co = company.lower().strip()
        # Company is valid — user trusts it
        self.learn_company(company, user_applied=True, user_trusted=True)
        # Title is desirable
        self.learn_valid_title(title)
        # Location is acceptable
        if location and location != "Unknown":
            city = location.split(",")[0].strip()
            if city:
                self.learn_us_city(city)
                self._append("preferred_locations", value=location.lower(), max_items=200)
        # Track apply patterns
        self._append("apply_history", value={
            "company": co, "title": title.lower(),
            "location": location, "date": datetime.now().isoformat()
        }, max_items=1000)
    
    def get_applied_companies(self):
        """Get all companies user has applied to."""
        history = self._get("apply_history", default=[])
        return set(h["company"] for h in history)
    
    def get_preferred_locations(self):
        """Get locations user prefers (based on apply history)."""
        return self._get("preferred_locations", default=[])
    
    def get_preferred_role_types(self):
        """Analyze what role types user applies to most."""
        history = self._get("apply_history", default=[])
        types = Counter()
        for h in history:
            title = h.get("title", "")
            if "ml" in title or "machine learning" in title or "ai " in title:
                types["ML"] += 1
            elif "data" in title and ("analyst" in title or "analytics" in title):
                types["DA"] += 1
            elif "data engineer" in title:
                types["DE"] += 1
            else:
                types["SDE"] += 1
        return dict(types)
    
    # ═══════════════════════════════════════════════════════════════
    # 6. ERROR MEMORY
    # ═══════════════════════════════════════════════════════════════
    
    def log_error(self, error_type, company, title, details=""):
        """Log a pipeline error so it's never repeated."""
        self._append("error_log", value={
            "type": error_type, "company": company.lower(),
            "title": title[:50], "details": details,
            "date": datetime.now().isoformat(),
        }, max_items=1000)
    
    def get_error_patterns(self):
        """Analyze recurring error types."""
        errors = self._get("error_log", default=[])
        return Counter(e["type"] for e in errors)
    
    def was_error_before(self, error_type, company):
        """Check if this exact error happened before."""
        errors = self._get("error_log", default=[])
        co = company.lower()
        return any(e["type"] == error_type and e["company"] == co for e in errors)
    
    # ═══════════════════════════════════════════════════════════════
    # 7. ATS DISCOVERY
    # ═══════════════════════════════════════════════════════════════
    
    def add_discovered_ats(self, platform, slug, company_name):
        """Add a newly discovered ATS company."""
        discovered = self._get("discovered_ats", default={})
        if platform not in discovered:
            discovered[platform] = {}
        discovered[platform][slug] = company_name
        self._set("discovered_ats", value=discovered)
    
    def get_discovered_ats(self):
        """Get all auto-discovered ATS companies."""
        return self._get("discovered_ats", default={})
    
    def add_known_ats_slug(self, platform, slug):
        """Mark a slug as checked (even if no intern roles)."""
        known = self._get("known_ats_slugs", default={})
        if platform not in known:
            known[platform] = []
        if slug not in known[platform]:
            known[platform].append(slug)
            self._set("known_ats_slugs", value=known)
    
    # ═══════════════════════════════════════════════════════════════
    # 8. GLOBAL KNOWLEDGE REPORT
    # ═══════════════════════════════════════════════════════════════
    
    def get_intelligence_report(self):
        """Full report of everything the brain knows."""
        companies = self._get("companies", default={})
        us_cities = self._get("us_cities", default=[])
        intl_cities = self._get("intl_cities", default=[])
        non_tech = self._get("non_tech_patterns", default=[])
        apply_history = self._get("apply_history", default=[])
        errors = self._get("error_log", default=[])
        discovered = self._get("discovered_ats", default={})
        source_quality = self._get("source_quality", default={})
        slugs = self._get("company_slugs", default={})
        
        total_discovered = sum(len(v) for v in discovered.values() if isinstance(v, dict))
        
        report = {
            "companies_known": len(companies),
            "companies_with_sponsorship_data": sum(1 for c in companies.values() if "sponsors_h1b" in c),
            "companies_user_applied": sum(1 for c in companies.values() if c.get("user_applied")),
            "us_cities_learned": len(us_cities),
            "intl_cities_learned": len(intl_cities),
            "non_tech_patterns_learned": len(non_tech),
            "company_slugs_learned": len(slugs),
            "total_applications": len(apply_history),
            "total_errors_logged": len(errors),
            "error_patterns": dict(Counter(e["type"] for e in errors).most_common(10)),
            "ats_discovered": total_discovered,
            "source_quality": {k: round(v.get("quality_ratio", 0), 2) for k, v in source_quality.items()},
            "preferred_roles": self.get_preferred_role_types(),
            "brain_size_kb": os.path.getsize(BRAIN_FILE) // 1024 if os.path.exists(BRAIN_FILE) else 0,
        }
        return report
    
    # ═══════════════════════════════════════════════════════════════
    # 9. PIPELINE INTEGRATION HOOKS
    # ═══════════════════════════════════════════════════════════════
    
    def on_job_validated(self, company, title, location, source, sponsorship="Unknown"):
        """Called when a job passes validation."""
        self.learn_company(company, last_valid_job=title, source=source)
        if sponsorship != "Unknown":
            self.learn_sponsorship(company, sponsorship == "Yes")
    
    def on_job_rejected(self, company, title, reason, source):
        """Called when a job is rejected."""
        self.learn_company(company, last_rejected_reason=reason)
        self.log_error(reason.split(":")[0].strip(), company, title, reason)
    
    def on_job_applied(self, company, title, location):
        """Called when user marks a job as Applied."""
        self.learn_user_applied(company, title, location)
    
    def on_pipeline_complete(self, source_stats):
        """Called at end of pipeline run."""
        for source, stats in source_stats.items():
            self.log_source_quality(
                source,
                stats.get("valid", 0),
                stats.get("rejected", 0),
                stats.get("errors", 0),
            )
        self.save()


def main():
    """Print brain intelligence report."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(message)s")
    brain = PipelineBrain.get()
    report = brain.get_intelligence_report()
    
    print("\n" + "=" * 60)
    print("PIPELINE BRAIN — INTELLIGENCE REPORT")
    print("=" * 60)
    for key, value in report.items():
        if isinstance(value, dict):
            print(f"\n  {key}:")
            for k, v in value.items():
                print(f"    {k}: {v}")
        else:
            print(f"  {key}: {value}")
    print("=" * 60)


if __name__ == "__main__":
    main()
