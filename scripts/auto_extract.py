#!/usr/bin/env python3
"""
Auto-set Extract=yes in Outreach Tracker based on smart signals.
Signals that set Extract=yes:
  - Sponsorship = Yes (explicit)
  - Location in a major US tech hub
  - Company in PatternCache (we've successfully emailed them before)
Signals that set Extract=Skip:
  - Sponsorship = No (never target)
  - Extract already set (never overwrite)
"""

# Socket ceiling, signal handling, stall watchdog. The repo root has to
# go on sys.path first: these run as `python3 scripts/NAME.py`, so
# Python puts scripts/ on the path, not the root.
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import scripts._resilient  # noqa: E402,F401

import sys, os, time, logging, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from outreach.outreach_config import C, SHEETS_CREDS, SPREADSHEET, OUTREACH_TAB, PATTERNS_FILE
from outreach.outreach_data import _pad, _cl
import gspread
from oauth2client.service_account import ServiceAccountCredentials

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(message)s")

# Major US tech hub cities and states
TECH_HUB_KEYWORDS = {
    # California
    "san francisco", "sf", "bay area", "silicon valley", "san jose",
    "santa clara", "sunnyvale", "mountain view", "palo alto", "menlo park",
    "redwood city", "foster city", "burlingame", "san mateo", "fremont",
    "oakland", "berkeley", "cupertino", "los angeles", "la", "santa monica",
    "venice", "culver city", "san diego", "irvine", "los gatos",
    # Washington
    "seattle", "bellevue", "redmond", "kirkland", "bothell",
    # Texas
    "austin", "dallas", "houston", "san antonio", "round rock",
    # Massachusetts
    "boston", "cambridge", "waltham", "burlington", "lexington",
    "woburn", "somerville", "watertown", "andover",
    # New York
    "new york", "nyc", "brooklyn", "manhattan", "new york city",
    # Illinois
    "chicago",
    # Colorado
    "denver", "boulder",
    # Georgia
    "atlanta",
    # Virginia
    "reston", "mclean", "arlington", "herndon",
    # Remote is always worth targeting
    "remote",
    # State abbreviations
    ", ca", ", wa", ", tx", ", ma", ", ny", ", il", ", co",
}

def _load_pattern_cache():
    """Load learned company domains from PatternCache."""
    try:
        if os.path.exists(PATTERNS_FILE):
            return set(json.load(open(PATTERNS_FILE)).keys())
    except Exception:
        pass
    return set()

def _is_tech_hub(location):
    if not location or location == "Unknown":
        return False
    loc = location.lower()
    return any(kw in loc for kw in TECH_HUB_KEYWORDS)

def _should_extract(row, pattern_domains):
    """Return 'yes', 'skip', or None (don't change)."""
    sponsorship = row[14].strip() if len(row) > 14 else ""  # column O in Valid Entries
    # We read from Outreach Tracker which has company/title but not sponsorship directly
    # So we use the signals available in the outreach row
    extract = row[C["extract"]].strip().lower() if len(row) > C["extract"] else ""

    # Never overwrite existing yes/skip
    if extract in ("yes", "skip"):
        return None

    company = row[C["company"]].strip().lower()
    notes   = row[C["notes"]].strip().lower() if len(row) > C["notes"] else ""
    hm_email = row[C["hm_email"]].strip() if len(row) > C["hm_email"] else ""
    rec_email = row[C["rec_email"]].strip() if len(row) > C["rec_email"] else ""

    # If we already have emails, mark yes automatically
    if hm_email or rec_email:
        return "yes"

    # Company in pattern cache = we've emailed them before = high value
    for domain in pattern_domains:
        domain_co = domain.split(".")[0].lower()
        if domain_co and len(domain_co) > 3 and domain_co in company:
            return "yes"

    return None  # Can't determine without location/sponsorship — leave blank


def _get_valid_entries_signals():
    """Read location and sponsorship from Valid Entries sheet."""
    try:
        from outreach.outreach_config import VALID_TAB
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        creds = ServiceAccountCredentials.from_json_keyfile_name(SHEETS_CREDS, scope)
        gc = gspread.authorize(creds)
        ss = gc.open(SPREADSHEET)
        valid = ss.worksheet("Valid Entries")
        rows = valid.get_all_values()[1:]
        # Build lookup: (company.lower(), title.lower()) -> (location, sponsorship)
        signals = {}
        for row in rows:
            if len(row) < 14:
                continue
            co    = row[2].strip().lower()
            title = row[3].strip().lower()
            loc   = row[8].strip() if len(row) > 8 else ""
            spon  = row[13].strip() if len(row) > 13 else ""
            signals[(co, title)] = {"location": loc, "sponsorship": spon}
        return signals, ss
    except Exception as e:
        log.error(f"Valid Entries read failed: {e}")
        return {}, None


def main():
    print(f"AUTO EXTRACT  setting Extract=yes based on smart signals")
    print("-" * 50)

    signals, ss = _get_valid_entries_signals()
    if ss is None:
        print("  ERROR: Could not connect to sheets")
        return

    # Build status lookup from Valid Entries: (company.lower(), title.lower()) -> status
    try:
        valid_ws = ss.worksheet("Valid Entries")
        valid_rows = valid_ws.get_all_values()[1:]
        time.sleep(1)
        status_map = {}
        for row in valid_rows:
            if len(row) < 4:
                continue
            co    = row[2].strip().lower()
            title = row[3].strip().lower()
            status = row[1].strip() if len(row) > 1 else ""
            status_map[(co, title)] = status
    except Exception as e:
        print(f"  ERROR reading Valid Entries status: {e}")
        return

    try:
        ws = ss.worksheet(OUTREACH_TAB)
    except Exception as e:
        print(f"  ERROR: {e}")
        return

    time.sleep(1)
    data = ws.get_all_values()
    time.sleep(1)

    updates = []
    yes_count = skip_count = 0

    for i, row in enumerate(data[1:], start=2):
        row = _pad(row)
        extract = row[C["extract"]].strip().lower() if len(row) > C["extract"] else ""

        # Never overwrite existing
        if extract in ("yes", "skip"):
            continue

        co    = row[C["company"]].strip()
        title = row[C["title"]].strip()
        key   = (co.lower(), title.lower())
        sig   = signals.get(key, {})
        spon  = sig.get("sponsorship", "")
        status = status_map.get(key, "")

        new_val = None

        # Hard reject: sponsorship = No. Now that the aggregator fills the
        # Sponsorship column from the feeds' Visa data, this rule actually has
        # values to act on — previously almost everything was "Unknown" so this
        # branch never fired and outreach targeted non-sponsors.
        if spon.lower() in ("no", "does not sponsor", "no sponsorship"):
            new_val = "Skip"
            skip_count += 1
        # Prioritise confirmed sponsors: tech-hub location is a strong signal
        # and _is_tech_hub was written for exactly this and never called.
        elif spon.lower() == "yes" and _is_tech_hub(sig.get("location", "")):
            new_val = "yes"
            yes_count += 1
        # Set Extract=yes if status is Applied
        elif status.strip().lower() == "applied":
            new_val = "yes"
            yes_count += 1
        # ALSO set Extract=yes if LinkedIn URL exists in HM or Rec column
        # (user manually researched a real person — honor that signal)
        else:
            hm_li = row[C["hm_li"]].strip() if len(row) > C["hm_li"] else ""
            rec_li = row[C["rec_li"]].strip() if len(row) > C["rec_li"] else ""
            hm_name = row[C["hm_name"]].strip() if len(row) > C["hm_name"] else ""
            rec_name = row[C["rec_name"]].strip() if len(row) > C["rec_name"] else ""
            import re as _re
            _li_pat = r"linkedin\.com/in/"
            # LinkedIn URL in li column OR in name column (user pasted URL there)
            has_li = (
                (hm_li and _re.search(_li_pat, hm_li)) or
                (rec_li and _re.search(_li_pat, rec_li)) or
                (hm_name and _re.search(_li_pat, hm_name)) or
                (rec_name and _re.search(_li_pat, rec_name))
            )
            if has_li and spon.lower() != "no":
                new_val = "yes"
                yes_count += 1
            else:
                # Only Skip if sponsorship explicitly No — otherwise leave blank
                # Don't silently skip targets we haven't evaluated yet
                if spon.lower() == "no":
                    new_val = "Skip"
                    skip_count += 1
                else:
                    new_val = None  # leave blank — don't decide yet

        if new_val:
            col = _cl(C["extract"])
            updates.append({"range": f"{col}{i}", "values": [[new_val]]})

    if updates:
        for chunk in range(0, len(updates), 50):
            try:
                ws.batch_update(updates[chunk:chunk+50], value_input_option="USER_ENTERED")
                time.sleep(1)
            except Exception as e:
                log.error(f"Batch update failed: {e}")
        print(f"  Set Extract=yes: {yes_count} rows (Applied status)")
        print(f"  Set Extract=Skip: {skip_count} rows")
    else:
        print("  No rows to update")

    print("-" * 50)


if __name__ == "__main__":
    main()
