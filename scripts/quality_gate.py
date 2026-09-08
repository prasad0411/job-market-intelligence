#!/usr/bin/env python3
"""
Self-Learning Quality Gate — runs after every pipeline write.
Catches issues that slip past validation and feeds corrections back.

What it checks:
1. URL-Company mismatch (Authorium with Cisco URL)
2. Non-tech titles that slipped through
3. Missing job IDs (extractable from URL)
4. Broken search links (plain text instead of HYPERLINK)
5. Duplicate job IDs across rows
6. Garbage locations
7. Company name slugs (leonardodrs → Leonardo DRS)
8. Clearance companies in valid sheet
9. Staffing agencies
10. Row-shift detection (same title on 3+ consecutive rows)

Writes all corrections to brain.json so pipeline gets smarter.
"""

import gspread
import json
import logging
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import re
import time
import urllib.parse
from datetime import datetime
from google.oauth2.service_account import Credentials

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(message)s")
log = logging.getLogger(__name__)

CREDS_FILE = ".local/credentials.json"
SHEET_NAME = "H1B visa"
BRAIN_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    ".local", "brain.json")
BRAIN_LOCK = BRAIN_FILE + ".lock"
QUALITY_LOG = ".local/quality_log.json"


class Brain:
    """Persistent learning store."""
    def __init__(self):
        self.data = self._load()

    def _load(self):
        try:
            if os.path.exists(BRAIN_FILE):
                with open(BRAIN_FILE) as f:
                    return json.load(f)
        except Exception:
            pass
        return {}

    def save(self):
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

    def add_slug_fix(self, slug, correct):
        if "learned_slugs" not in self.data:
            self.data["learned_slugs"] = {}
        self.data["learned_slugs"][slug.lower()] = correct
        self.save()

    def add_non_tech_title(self, pattern):
        if "learned_non_tech" not in self.data:
            self.data["learned_non_tech"] = []
        if pattern not in self.data["learned_non_tech"]:
            self.data["learned_non_tech"].append(pattern)
            self.save()

    def add_clearance_company(self, company):
        if "learned_clearance" not in self.data:
            self.data["learned_clearance"] = []
        if company.lower() not in self.data["learned_clearance"]:
            self.data["learned_clearance"].append(company.lower())
            self.save()

    def get_learned_slugs(self):
        return self.data.get("learned_slugs", {})

    def get_learned_non_tech(self):
        return self.data.get("learned_non_tech", [])

    def get_learned_clearance(self):
        return self.data.get("learned_clearance", [])

    def log_issue(self, issue_type, details):
        if "issue_log" not in self.data:
            self.data["issue_log"] = []
        self.data["issue_log"].append({
            "type": issue_type, "details": details,
            "date": datetime.now().isoformat()
        })
        self.data["issue_log"] = self.data["issue_log"][-500:]
        self.save()

    def get_issue_stats(self):
        issues = self.data.get("issue_log", [])
        stats = {}
        for i in issues:
            stats[i["type"]] = stats.get(i["type"], 0) + 1
        return stats


class QualityGate:
    def __init__(self):
        scopes = ["https://www.googleapis.com/auth/spreadsheets",
                  "https://www.googleapis.com/auth/drive"]
        creds = Credentials.from_service_account_file(CREDS_FILE, scopes=scopes)
        gc = gspread.authorize(creds)
        ss = gc.open(SHEET_NAME)
        self.valid = ss.worksheet("Valid Entries")
        self.discarded = ss.worksheet("Discarded Entries")
        self.brain = Brain()
        self.fixes = 0
        self.deletes = 0

    def run(self, check_last_n=100):
        """Run all quality checks on recent rows."""
        log.info("=" * 60)
        log.info("QUALITY GATE — Post-Write Audit")
        log.info("=" * 60)

        data = self.valid.get_all_values()
        formulas = self.valid.get(
            f'F1:F{len(data)}', value_render_option='FORMULA'
        )
        
        start = max(1, len(data) - check_last_n)
        rows_to_delete = set()

        _applied_taught = 0
        for i in range(start, len(data)):
            row = data[i]
            formula = formulas[i][0] if i < len(formulas) and formulas[i] else ""
            row_num = i + 1

            if len(row) < 6:
                continue
            
            status = row[1].strip()
            if status == "Applied":
                # Teach the brain from a manual Applied mark before skipping.
                # PipelineBrain.learn_user_applied marks the company trusted,
                # records the title as desirable and learns the city, and
                # get_title_preference_score reads those titles to rank future
                # jobs. The hook existed fully implemented and was never fired,
                # so none of that learning had ever happened.
                try:
                    import sys as _s_qg, os as _o_qg
                    _s_qg.path.insert(0, _o_qg.path.dirname(
                        _o_qg.path.dirname(_o_qg.path.abspath(__file__))))
                    from scripts.pipeline_brain import PipelineBrain as _PB
                    _pb = _PB.get()
                    _co = row[2].strip()
                    _ti = row[3].strip()
                    _loc = row[8].strip() if len(row) > 8 else ""
                    if _co and _ti:
                        _pb.on_job_applied(_co, _ti, _loc)
                        _applied_taught += 1
                except Exception as _abe:
                    log.debug(f"on_job_applied failed: {_abe}")
                continue
            if status == "Rejected":
                continue

            company = row[2].strip()
            title = row[3].strip()
            url = row[5].strip()
            job_id = row[6].strip() if len(row) > 6 else ""
            location = row[8].strip() if len(row) > 8 else ""

            # ── CHECK 1: URL-Company mismatch ──
            if url and 'http' in url and '🔍' not in url:
                url_domain = self._extract_domain(url)
                co_norm = re.sub(r"[^a-z0-9]", "", company.lower())
                if url_domain and len(url_domain) > 3:
                    domain_norm = re.sub(r"[^a-z0-9]", "", url_domain)
                    if (domain_norm not in co_norm and co_norm not in domain_norm
                        and not self._is_known_ats(url_domain)):
                        log.info(f"  ⚠ URL MISMATCH R{row_num}: {company} vs domain '{url_domain}'")
                        self.brain.log_issue("url_mismatch", f"{company} vs {url_domain}")

            # ── CHECK 2: Non-tech titles ──
            non_tech = [
                "business development", "venture capital", "staffing",
                "recruitment", "sales engineer", "field sales",
                "account executive", "customer success",
                "real estate", "property", "interior design",
                "mechanical engineer", "civil engineer",
                "nurse", "physician", "pharmacist",
                "truck driver", "warehouse", "forklift",
            ] + self.brain.get_learned_non_tech()
            
            title_lower = title.lower()
            for nt in non_tech:
                if nt in title_lower:
                    rows_to_delete.add(row_num)
                    log.info(f"  DEL R{row_num}: Non-tech '{nt}' in '{title[:35]}'")
                    try:
                        self.brain.add_non_tech_title(nt)
                    except Exception as _le:
                        log.debug(f"add_non_tech_title failed: {_le}")
                    self.brain.log_issue("non_tech_slip", f"{company}: {title[:40]}")
                    break

            if row_num in rows_to_delete:
                continue

            # ── CHECK 3: Missing job ID from URL ──
            if (not job_id or job_id == "N/A") and url and 'http' in url:
                extracted = self._extract_job_id_from_url(url)
                if extracted:
                    self.valid.update_cell(row_num, 7, extracted)
                    log.info(f"  FIX R{row_num}: {company} job_id → {extracted}")
                    self.fixes += 1
                    time.sleep(0.3)

            # ── CHECK 4: Broken search links ──
            if '🔍' in url and 'HYPERLINK' not in formula:
                query = urllib.parse.quote(f"{company} {title} careers apply")
                new_formula = f'=HYPERLINK("https://www.google.com/search?q={query}", "🔍 {company} - Search")'
                self.valid.update(
                    range_name=f'F{row_num}', values=[[new_formula]],
                    value_input_option='USER_ENTERED'
                )
                log.info(f"  FIX R{row_num}: {company} search link → clickable")
                self.fixes += 1
                time.sleep(0.3)

            # ── CHECK 5: Search link has wrong company name ──
            if '🔍' in url and company:
                # Check if search link text matches current company name
                display = row[5].strip()
                if 'HYPERLINK' in formula:
                    # Extract display text from formula
                    display_match = re.search(r'"🔍\s*([^"]+)\s*-\s*Search"', formula)
                    if display_match:
                        link_company = display_match.group(1).strip()
                        if link_company.lower() != company.lower() and link_company not in company:
                            query = urllib.parse.quote(f"{company} {title} careers apply")
                            new_formula = f'=HYPERLINK("https://www.google.com/search?q={query}", "🔍 {company} - Search")'
                            self.valid.update(
                                range_name=f'F{row_num}', values=[[new_formula]],
                                value_input_option='USER_ENTERED'
                            )
                            log.info(f"  FIX R{row_num}: Search link '{link_company}' → '{company}'")
                            self.fixes += 1
                            time.sleep(0.3)

            # ── CHECK 6: Clearance companies ──
            from aggregator.config import CLEARANCE_COMPANIES
            learned_clearance = self.brain.get_learned_clearance()
            all_clearance = [c.lower() for c in CLEARANCE_COMPANIES] + learned_clearance
            if any(c in company.lower() for c in all_clearance):
                rows_to_delete.add(row_num)
                log.info(f"  DEL R{row_num}: Clearance company: {company}")
                try:
                    self.brain.add_clearance_company(company)
                except Exception as _le:
                    log.debug(f"add_clearance_company failed: {_le}")
                self.brain.log_issue("clearance_slip", company)

            # ── CHECK 7: Staffing agencies ──
            staffing = ["express employment", "robert half", "adecco",
                       "manpower", "kelly services", "randstad",
                       "staffing agency", "staffing solutions"]
            if any(s in company.lower() for s in staffing):
                rows_to_delete.add(row_num)
                log.info(f"  DEL R{row_num}: Staffing agency: {company}")
                self.brain.log_issue("staffing_slip", company)

            # ── CHECK 8: Company name slugs ──
            from aggregator.config import COMPANY_NAME_FIXES
            co_lower = company.lower()
            learned_slugs = self.brain.get_learned_slugs()
            
            fixed_name = COMPANY_NAME_FIXES.get(co_lower) or learned_slugs.get(co_lower)
            if fixed_name and fixed_name != company:
                self.valid.update_cell(row_num, 3, fixed_name)
                log.info(f"  FIX R{row_num}: {company} → {fixed_name}")
                self.fixes += 1
                # Learn it. The aggregator's apply_learned.fix_company_slug()
                # reads learned_slugs and applies this UPSTREAM, so the bad name
                # never reaches the sheet again instead of being corrected after.
                try:
                    self.brain.add_slug_fix(co_lower, fixed_name)
                except Exception as _le:
                    log.debug(f"add_slug_fix failed: {_le}")
                time.sleep(0.3)

            # ── CHECK 9: Garbage locations ──
            garbage_locs = ["select how often", "opportunity", "where they work",
                          "cover letter", "s as required by law", "unknown"]
            if location.lower() in garbage_locs:
                self.valid.update_cell(row_num, 9, "Unknown")
                log.info(f"  FIX R{row_num}: Garbage location '{location}' → Unknown")
                self.fixes += 1
                time.sleep(0.3)

            # State prefix fix: "NJ Princeton" → "Princeton, NJ"
            state_match = re.match(r'^([A-Z]{2})\s+([A-Z][a-z].+)$', location)
            if state_match:
                us_states = {"AL","AK","AZ","AR","CA","CO","CT","DE","FL","GA","HI","ID","IL",
                    "IN","IA","KS","KY","LA","ME","MD","MA","MI","MN","MS","MO","MT",
                    "NE","NV","NH","NJ","NM","NY","NC","ND","OH","OK","OR","PA","RI",
                    "SC","SD","TN","TX","UT","VT","VA","WA","WV","WI","WY","DC"}
                if state_match.group(1) in us_states:
                    fixed_loc = f"{state_match.group(2)}, {state_match.group(1)}"
                    self.valid.update_cell(row_num, 9, fixed_loc)
                    log.info(f"  FIX R{row_num}: {location} → {fixed_loc}")
                    self.fixes += 1
                    time.sleep(0.3)

        # ── CHECK 10: Row-shift detection ──
        # Same title appearing 3+ times in a row = row shift
        for i in range(start, len(data) - 2):
            if i + 1 >= len(data) or i + 2 >= len(data):
                break
            t1 = data[i][3].strip() if len(data[i]) > 3 else ""
            t2 = data[i+1][3].strip() if len(data[i+1]) > 3 else ""
            t3 = data[i+2][3].strip() if len(data[i+2]) > 3 else ""
            if t1 == t2 == t3 and t1:
                # Three consecutive rows with same title = row shift
                for j in [i+1, i+2]:  # Keep first, flag rest
                    rn = j + 1
                    if data[j][1].strip() not in ("Applied", "Rejected"):
                        rows_to_delete.add(rn)
                        log.info(f"  DEL R{rn}: Row-shift detected (3x '{t1[:30]}')")
                        self.brain.log_issue("row_shift", t1[:40])

        # ── CHECK 11: Duplicate job IDs ──
        job_id_map = {}  # job_id → first row
        for i in range(1, len(data)):
            if len(data[i]) < 7:
                continue
            co = data[i][2].strip()
            jid = data[i][6].strip()
            status = data[i][1].strip()
            if not jid or jid == "N/A":
                continue
            key = f"{co.lower()}_{jid}"
            if key in job_id_map:
                if status not in ("Applied", "Rejected"):
                    rn = i + 1
                    rows_to_delete.add(rn)
                    log.info(f"  DEL R{rn}: Duplicate job_id {jid} ({co})")
            else:
                job_id_map[key] = i + 1

        # Delete bad rows
        if rows_to_delete:
            for r in sorted(rows_to_delete, reverse=True):
                try:
                    self.valid.delete_rows(r)
                    time.sleep(0.4)
                    self.deletes += 1
                except Exception:
                    pass

        # Renumber if we deleted anything
        if self.deletes > 0:
            time.sleep(3)
            data = self.valid.get_all_values()
            total = len(data) - 1
            sr_values = [[str(i)] for i in range(1, total + 1)]
            self.valid.update(
                range_name=f'A2:A{total+1}', values=sr_values,
                value_input_option='USER_ENTERED'
            )

        # Report
        log.info(f"\n{'='*60}")
        log.info(f"QUALITY GATE COMPLETE")
        log.info(f"  Fixes: {self.fixes}")
        log.info(f"  Deletes: {self.deletes}")
        log.info(f"  Brain issue stats: {self.brain.get_issue_stats()}")
        log.info(f"{'='*60}")

    def _extract_domain(self, url):
        try:
            from urllib.parse import urlparse
            netloc = urlparse(url).netloc.lower()
            parts = netloc.split(".")
            # Skip common prefixes
            skip = {"www", "jobs", "careers", "job-boards", "apply", "fa-exjq-saasfaprod1"}
            for p in parts:
                if p not in skip and len(p) > 2:
                    return p
        except Exception:
            pass
        return ""

    def _is_known_ats(self, domain):
        ats = {"greenhouse", "lever", "ashbyhq", "myworkdayjobs", "smartrecruiters",
               "icims", "jobvite", "taleo", "workable", "rippling", "pinpointhq",
               "oraclecloud", "successfactors", "ziprecruiter", "wd1", "wd3", "wd5",
               "wd108", "wd501", "wd12", "myworkdaysite"}
        return domain in ats

    def _extract_job_id_from_url(self, url):
        patterns = [
            r"/jobs?/(\d{5,})",
            r"gh_jid=(\d{7,})",
            r"_([A-Z]R?-?\d{5,})(?:-\d+)?(?:\?|$)",
            r"lever\.co/[^/]+/([a-f0-9-]{36})",
            r"ashbyhq\.com/[^/]+/([a-f0-9-]{36})",
            r"/(\d{6,})(?:\?|$)",
        ]
        for p in patterns:
            m = re.search(p, url, re.I)
            if m:
                val = m.group(1)
                if len(val) >= 5 and not val.startswith("0000"):
                    return val
        return None


def main():
    gate = QualityGate()
    gate.run()


if __name__ == "__main__":
    main()