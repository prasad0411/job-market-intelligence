#!/usr/bin/env python3
"""
Self-Learning Discarded Sheet Auditor v2
Runs every 3 days. Connected to brain.json for persistent learning.

Intelligence layers:
1. PATTERN LEARNING — tracks WHY jobs get rescued, auto-updates pipeline rules
2. BEHAVIORAL LEARNING — learns from user's apply/skip patterns in Valid Entries
3. CROSS-REFERENCE — checks discarded jobs against user's Applied companies
4. BRAIN FEEDBACK — writes corrections to brain.json so pipeline gets smarter
5. DUPLICATE CLEANUP — removes duplicate discarded entries
6. SELF-AUDIT — checks its own rescue accuracy over time
"""

import gspread
import json
import logging
import os
import re
import time
from datetime import datetime, timedelta
from google.oauth2.service_account import Credentials

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(message)s")
log = logging.getLogger(__name__)

CREDS_FILE = ".local/credentials.json"
SHEET_NAME = "H1B visa"
BRAIN_FILE = ".local/brain.json"
AUDIT_LOG_FILE = ".local/audit_history.json"

# ═══════════════════════════════════════════════════════════════════
# INTELLIGENCE: Companies that should never be rejected for clearance
# This list GROWS automatically when the auditor rescues a company
# ═══════════════════════════════════════════════════════════════════
# Companies that ALWAYS require clearance — NEVER rescue these
_ALWAYS_CLEARANCE = {
    "general dynamics", "general dynamics mission systems", "gdms", "gdit",
    "caci", "northrop grumman", "raytheon", "rtx", "leidos",
    "lockheed martin", "l3harris", "saic", "mantech", "kbr",
    "amentum", "peraton", "sierra space", "parsons",
    "bae systems", "leonardo drs", "booz allen", "booz allen hamilton",
}

_BASE_NO_CLEARANCE = {
    "apple", "google", "meta", "amazon", "microsoft", "netflix",
    "uber", "lyft", "stripe", "airbnb", "spotify", "pinterest",
    "tesla", "nvidia", "tiktok", "bytedance", "salesforce", "slack",
    "snap", "reddit", "dropbox", "coinbase", "robinhood", "doordash",
    "instacart", "databricks", "snowflake", "palantir", "figma",
    "rivian", "lucid", "waymo", "cruise", "nuro", "zoox", "aurora",
    "openai", "anthropic", "cerebras", "groq", "ramp", "brex",
    "notion", "airtable", "asana", "canva", "miro", "vercel",
    "mongodb", "elastic", "confluent", "datadog", "cloudflare",
    "hubspot", "twilio", "okta", "crowdstrike", "sentinelone",
    "discord", "toast", "squarespace", "plaid", "affirm", "chime",
}

US_STATES = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA",
    "HI", "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD",
    "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ",
    "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC",
    "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY", "DC",
}

CS_TITLE_KEYWORDS = {
    "software", "data", "machine learning", "ml", "ai", "backend",
    "frontend", "full stack", "fullstack", "devops", "cloud", "sre",
    "platform", "infrastructure", "security", "cyber", "research",
    "algorithm", "developer", "engineer", "analyst", "scientist",
}


class BrainConnector:
    """Connects to brain.json for persistent self-learning."""

    def __init__(self):
        self.brain = self._load()

    def _load(self):
        try:
            if os.path.exists(BRAIN_FILE):
                with open(BRAIN_FILE, "r") as f:
                    return json.load(f)
        except Exception:
            pass
        return {}

    def _save(self):
        """Write through Brain's locked, atomic, merging writer.

        This used to dump a whole in-memory copy with open(..., "w"): no
        lock, no temp file, no merge. _load() returns {} on any error, so a
        single bad read wrote an EMPTY brain over everything. It also held
        that copy for the length of a full audit, so any key another process
        wrote meanwhile was silently reverted. Matches the 27 Aug corruption
        and the 31 Aug wipe that cost 722 companies and the blacklist.
        """
        try:
            import sys as _s, os as _o
            _s.path.insert(0, _o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))))
            from outreach.brain import Brain
            b = Brain.get()
            for _k, _v in (self.brain or {}).items():
                b._data[_k] = _v
            b.save()
        except Exception:
            pass

    def get_learned_no_clearance(self):
        """Get companies brain has learned don't need clearance."""
        return set(self.brain.get("no_clearance_learned", []))

    def add_no_clearance_company(self, company):
        """Brain learns a new company doesn't need clearance."""
        if "no_clearance_learned" not in self.brain:
            self.brain["no_clearance_learned"] = []
        co = company.lower().strip()
        if co not in self.brain["no_clearance_learned"]:
            self.brain["no_clearance_learned"].append(co)
            self._save()
            log.info(f"  BRAIN LEARNED: {company} does NOT require clearance")

    def get_learned_us_cities(self):
        """Get cities brain has learned are in the US."""
        return set(self.brain.get("us_cities_learned", []))

    def add_us_city(self, city):
        """Brain learns a new city is in the US."""
        if "us_cities_learned" not in self.brain:
            self.brain["us_cities_learned"] = []
        if city.lower() not in self.brain["us_cities_learned"]:
            self.brain["us_cities_learned"].append(city.lower())
            self._save()

    def get_user_applied_companies(self):
        """Get companies the user has applied to — these are clearly valid."""
        return set(self.brain.get("user_applied_companies", []))

    def update_applied_companies(self, companies):
        """Update the list of companies user has applied to."""
        self.brain["user_applied_companies"] = list(companies)
        self._save()

    def get_rescue_history(self):
        """Get history of rescued jobs for accuracy tracking."""
        return self.brain.get("rescue_history", [])

    def add_rescue(self, company, title, reason):
        """Log a rescue for accuracy tracking."""
        if "rescue_history" not in self.brain:
            self.brain["rescue_history"] = []
        self.brain["rescue_history"].append({
            "company": company, "title": title,
            "reason": reason, "date": datetime.now().isoformat(),
        })
        # Keep last 200 rescues
        self.brain["rescue_history"] = self.brain["rescue_history"][-200:]
        self._save()

    def get_false_positive_patterns(self):
        """Analyze rescue history for recurring patterns."""
        history = self.get_rescue_history()
        patterns = {}
        for h in history:
            reason_type = h["reason"].split(":")[0].strip()
            patterns[reason_type] = patterns.get(reason_type, 0) + 1
        return patterns


class DiscardedAuditor:
    def __init__(self):
        scopes = ["https://www.googleapis.com/auth/spreadsheets",
                  "https://www.googleapis.com/auth/drive"]
        creds = Credentials.from_service_account_file(CREDS_FILE, scopes=scopes)
        gc = gspread.authorize(creds)
        self.ss = gc.open(SHEET_NAME)
        self.discarded = self.ss.worksheet("Discarded Entries")
        self.valid = self.ss.worksheet("Valid Entries")
        self.brain = BrainConnector()

    def audit(self):
        log.info("=" * 60)
        log.info("SELF-LEARNING DISCARDED AUDITOR v2")
        log.info("=" * 60)

        # Step 1: Learn from user behavior in Valid Entries
        self._learn_from_user_behavior()

        # Step 2: Scan discarded for false positives
        rescued, duplicates = self._scan_discarded()

        # Step 3: Clean duplicates
        if duplicates:
            # _delete_rows swallows failures with bare except:pass. A failure
            # mid-sweep leaves every remaining row index shifted. The _safe
            # variant right below it retries instead.
            self._delete_rows_safe(self.discarded, duplicates)
            log.info(f"Deleted {len(duplicates)} duplicate discarded entries")

        # Step 4: Rescue false positives
        if rescued:
            self._rescue_jobs(rescued)

        # Step 5: Report brain stats
        self._report_brain_stats()

        log.info("Audit complete")

    def _learn_from_user_behavior(self):
        """Learn from what the user applies to."""
        log.info("\n--- Learning from user behavior ---")
        valid_data = self.valid.get_all_values()

        applied_companies = set()
        applied_titles = []
        skip_count = 0
        apply_count = 0

        for row in valid_data[1:]:
            if len(row) < 4:
                continue
            status = row[1].strip()
            company = row[2].strip().lower()
            title = row[3].strip().lower()

            if status == "Applied":
                applied_companies.add(company)
                applied_titles.append(title)
                apply_count += 1
            elif status == "Not Applied":
                skip_count += 1

        self.brain.update_applied_companies(applied_companies)
        self._applied_companies = applied_companies
        self._applied_titles = applied_titles

        log.info(f"  Applied: {apply_count} jobs at {len(applied_companies)} companies")
        log.info(f"  Not Applied: {skip_count} jobs")

        # Learn title preferences
        title_words = {}
        for t in applied_titles:
            for word in re.findall(r'\b\w+\b', t):
                if len(word) > 2:
                    title_words[word] = title_words.get(word, 0) + 1
        self._preferred_title_words = {w for w, c in title_words.items() if c >= 3}

    def _scan_discarded(self):
        """Scan discarded entries for false positives."""
        log.info("\n--- Scanning discarded entries ---")
        data = self.discarded.get_all_values()

        no_clearance = _BASE_NO_CLEARANCE | self.brain.get_learned_no_clearance()
        applied_cos = self._applied_companies
        us_cities = self.brain.get_learned_us_cities()

        rescued = []
        duplicates = set()
        seen_keys = set()

        for i, row in enumerate(data[1:], start=2):
            if len(row) < 4:
                continue
            reason = row[1].strip() if len(row) > 1 else ""
            company = row[2].strip() if len(row) > 2 else ""
            title = row[3].strip() if len(row) > 3 else ""
            location = row[8].strip() if len(row) > 8 else ""
            co_lower = company.lower().strip()

            # Dedup
            key = re.sub(r"[^a-z0-9]", "", f"{company}_{title}".lower())
            if key in seen_keys:
                duplicates.add(i)
                continue
            seen_keys.add(key)

            # ── CHECK 0: Age limit — don't rescue jobs older than 60 days ──
            entry_date_str = row[10].strip() if len(row) > 10 else ""
            try:
                _age_date = None
                # Formats with year (don't override year)
                for _fmt in ["%d-%b-%Y", "%d %B, %Y"]:
                    try:
                        _age_date = datetime.strptime(entry_date_str, _fmt)
                        break
                    except ValueError:
                        continue
                # Formats without year (set current year)
                if not _age_date:
                    for _fmt in ["%d %B, %I:%M %p", "%d %b, %I:%M %p", "%d %B"]:
                        try:
                            _age_date = datetime.strptime(entry_date_str, _fmt).replace(year=datetime.now().year)
                            break
                        except ValueError:
                            continue
                if _age_date:
                    # Handle year rollover: if parsed date is in the future, it's from last year
                    if _age_date > datetime.now():
                        _age_date = _age_date.replace(year=_age_date.year - 1)
                    if (datetime.now() - _age_date).days > 60:
                        continue  # Too old to rescue — skip silently
            except Exception as _e:
                log.debug(f"Date parse failed for rescue candidate: {entry_date_str} — {_e}")

            # ── CHECK 0b: Never rescue defense/clearance companies ──
            if any(dc in co_lower for dc in _ALWAYS_CLEARANCE):
                continue  # Known clearance company — do NOT rescue

            # ── CHECK 1: Clearance false positive ──
            if "clearance" in reason.lower() or "security" in reason.lower():
                if any(tc in co_lower or co_lower in tc for tc in no_clearance):
                    rescued.append((i, row, f"False clearance: {company} is trusted"))
                    self.brain.add_no_clearance_company(company)
                    continue
                # NEW: If user applied to this company before, it's clearly valid
                if co_lower in applied_cos:
                    rescued.append((i, row, f"False clearance: user applied to {company} before"))
                    self.brain.add_no_clearance_company(company)
                    continue

            # ── CHECK 2: International false positive ──
            if "international" in reason.lower() or "canada" in reason.lower() or "location" in reason.lower():
                if location:
                    loc_lower = location.lower()
                    # SAFETY: never rescue jobs with explicit non-US indicators
                    _intl_markers = ["canada", "uk", "united kingdom", "germany", "france",
                                     "india", "china", "japan", "australia", "netherlands",
                                     "ireland", "singapore", "brazil", "israel", "sweden",
                                     "switzerland", "korea", "taiwan", "hong kong"]
                    if any(m in loc_lower for m in _intl_markers):
                        continue  # Genuinely international — do NOT rescue
                    # Check US state code (last comma-separated part must be exactly 2 chars)
                    loc_parts = location.split(",")
                    if len(loc_parts) >= 2:
                        state_raw = loc_parts[-1].strip().upper()
                        # Must be exactly a 2-letter state code, not a truncated country name
                        if len(state_raw) == 2 and state_raw in US_STATES:
                            rescued.append((i, row, f"False international: {location} is US"))
                            self.brain.add_us_city(location.split(",")[0].strip())
                            continue
                    # Check learned US cities
                    if any(city in location.lower() for city in us_cities):
                        rescued.append((i, row, f"False international: {location} learned as US"))
                        continue
                    # Indiana/Milwaukee patterns
                    us_city_patterns = [
                        "indianapolis", "milwaukee", "fort wayne", "noblesville",
                        "waukesha", "crane", "bloomington", "columbus", "evansville",
                    ]
                    if any(city in location.lower() for city in us_city_patterns):
                        rescued.append((i, row, f"False international: {location} is US city"))
                        self.brain.add_us_city(location.split(",")[0].strip())
                        continue

            # ── CHECK 3: Non-tech false positive ──
            if "not a cs" in reason.lower() or "non-tech" in reason.lower() or "not cs" in reason.lower():
                title_lower = title.lower()
                if any(kw in title_lower for kw in CS_TITLE_KEYWORDS):
                    rescued.append((i, row, f"False non-tech: '{title[:30]}' has CS keywords"))
                    continue

            # ── CHECK 4: User applied to same company ──
            if co_lower in applied_cos:
                # User applied to other jobs at this company — check if this title is also CS
                title_lower = title.lower()
                if any(kw in title_lower for kw in CS_TITLE_KEYWORDS):
                    # Only rescue if it has CS keywords AND user applies to this company
                    if "clearance" in reason.lower() or "international" in reason.lower():
                        rescued.append((i, row, f"User applies to {company}: likely valid"))
                        continue

            # ── CHECK 5: HTTP fetch failed for valid companies ──
            if "http fetch failed" in reason.lower():
                if co_lower in applied_cos or any(tc in co_lower for tc in no_clearance):
                    rescued.append((i, row, f"HTTP failed but {company} is trusted"))
                    continue

        log.info(f"  Rescued: {len(rescued)} false positives")
        log.info(f"  Duplicates: {len(duplicates)}")
        return rescued, duplicates

    def _rescue_jobs(self, rescued):
        """Move rescued jobs back to Valid Entries."""
        log.info("\n--- Rescuing false positives ---")
        valid_data = self.valid.get_all_values()
        existing_keys = set()
        for row in valid_data[1:]:
            if len(row) > 3:
                existing_keys.add(re.sub(r"[^a-z0-9]", "", f"{row[2]}_{row[3]}".lower()))

        next_sr = len(valid_data)
        rows_to_add = []

        for row_num, row_data, rescue_reason in rescued:
            company = row_data[2] if len(row_data) > 2 else ""
            title = row_data[3] if len(row_data) > 3 else ""

            # Don't rescue if already in valid
            key = re.sub(r"[^a-z0-9]", "", f"{company}_{title}".lower())
            if key in existing_keys:
                log.info(f"  SKIP (already in valid): {company} | {title[:35]}")
                continue

            log.info(f"  RESCUED: {company:25s} | {title[:35]:35s} | {rescue_reason}")
            self.brain.add_rescue(company, title, rescue_reason)

            next_sr += 1
            url = row_data[5] if len(row_data) > 5 else ""
            job_id = row_data[6] if len(row_data) > 6 else "N/A"
            job_type = row_data[7] if len(row_data) > 7 else "Internship"
            location = row_data[8] if len(row_data) > 8 else "Unknown"
            resume = row_data[9] if len(row_data) > 9 else "SDE"
            remote = row_data[10] if len(row_data) > 10 else "Unknown"
            source = row_data[12] if len(row_data) > 12 else "Rescued"

            # Entry Date gets a PROPER date (DD-Mon-YYYY, matching other rows);
            # the rescue marker moves to Notes (col 14) so it never corrupts the date.
            _entry_date = datetime.now().strftime("%d-%b-%Y")
            _rescue_note = f"Rescued {self._format_date()}"
            new_row = [str(next_sr), "Not Applied", company, title, "N/A",
                      url, job_id, job_type, location, resume, remote,
                      _entry_date, source, "Unknown", _rescue_note]
            rows_to_add.append(new_row)
            existing_keys.add(key)

        if rows_to_add:
            self.valid.append_rows(rows_to_add, value_input_option="USER_ENTERED")
            log.info(f"Added {len(rows_to_add)} rescued jobs to Valid Entries")

        # Delete rescued rows from discarded
        rescued_rows = sorted([r[0] for r in rescued], reverse=True)
        self._delete_rows_safe(self.discarded, rescued_rows)

    def _delete_rows(self, sheet, row_nums):
        for r in sorted(row_nums, reverse=True):
            try:
                sheet.delete_rows(r)
                time.sleep(0.4)
            except Exception:
                pass

    def _delete_rows_safe(self, sheet, row_nums):
        """Delete rows with retry."""
        for r in sorted(row_nums, reverse=True):
            for attempt in range(3):
                try:
                    sheet.delete_rows(r)
                    time.sleep(0.5)
                    break
                except Exception:
                    time.sleep(2)

    def _report_brain_stats(self):
        """Report what brain has learned."""
        log.info("\n--- Brain Stats ---")
        patterns = self.brain.get_false_positive_patterns()
        if patterns:
            log.info("  Rescue patterns (recurring issues):")
            for pattern, count in sorted(patterns.items(), key=lambda x: -x[1]):
                log.info(f"    {pattern}: {count} times")

        learned_cos = self.brain.get_learned_no_clearance()
        if learned_cos:
            log.info(f"  Learned no-clearance companies: {len(learned_cos)}")

        learned_cities = self.brain.get_learned_us_cities()
        if learned_cities:
            log.info(f"  Learned US cities: {len(learned_cities)}")

        applied = self.brain.get_user_applied_companies()
        log.info(f"  User applied companies: {len(applied)}")

    def _format_date(self):
        return datetime.now().strftime("%d %b, %I:%M %p")


def main():
    auditor = DiscardedAuditor()
    auditor.audit()


if __name__ == "__main__":
    main()
