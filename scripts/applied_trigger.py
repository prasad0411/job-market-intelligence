#!/usr/bin/env python3
"""
Applied Trigger — when a job is marked Applied in Valid Entries,
immediately set Extract=yes in Outreach Tracker and trigger email discovery.

Runs as part of send_scheduled (4x daily) for near-real-time response.
Can also be run standalone: python3 scripts/applied_trigger.py
"""

# Socket ceiling, signal handling, stall watchdog. The repo root has to
# go on sys.path first: these run as `python3 scripts/NAME.py`, so
# Python puts scripts/ on the path, not the root.
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import scripts._resilient  # noqa: E402,F401

import os
import sys
import re
import time
import logging

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import gspread
from oauth2client.service_account import ServiceAccountCredentials

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [applied_trigger] %(message)s")

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CREDS = os.path.join(BASE, ".local", "credentials.json")


def get_sheets():
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    creds = ServiceAccountCredentials.from_json_keyfile_name(CREDS, scope)
    return gspread.authorize(creds).open('H1B visa')


def run():
    """
    Scan Valid Entries for Applied jobs.
    For each, ensure the matching Outreach row has Extract=yes.
    Returns count of rows updated.
    """
    ss = get_sheets()
    valid_ws = ss.worksheet('Valid Entries')
    outreach_ws = ss.worksheet('Outreach Tracker')

    valid_rows = valid_ws.get_all_values()
    outreach_rows = outreach_ws.get_all_values()

    # Build set of Applied companies+titles from Valid Entries
    applied_jobs = set()
    for r in valid_rows[1:]:
        if len(r) > 3:
            status = r[1].strip().lower()
            if status == "applied":
                co = re.sub(r"[^a-z0-9]", "", r[2].strip().lower())
                ti = re.sub(r"[^a-z0-9]", "", r[3].strip().lower())
                applied_jobs.add((co, ti))

    if not applied_jobs:
        log.info("No Applied jobs found")
        return 0

    # Find Outreach rows that match Applied jobs but don't have Extract=yes
    # Column mapping for Outreach Tracker
    updates = []
    updated_companies = []

    for i, r in enumerate(outreach_rows[1:], start=2):
        if len(r) < 5:
            continue
        co_raw = r[1].strip()  # Column B = Company
        ti_raw = r[2].strip()  # Column C = Job Title
        extract = r[3].strip().lower() if len(r) > 3 else ""  # Column D = Extract

        co_key = re.sub(r"[^a-z0-9]", "", co_raw.lower())
        ti_key = re.sub(r"[^a-z0-9]", "", ti_raw.lower())

        if (co_key, ti_key) in applied_jobs and extract != "yes":
            updates.append({"range": f"D{i}", "values": [["yes"]]})
            updated_companies.append(co_raw)

    if updates:
        for chunk in range(0, len(updates), 20):
            outreach_ws.batch_update(updates[chunk:chunk+20], value_input_option="USER_ENTERED")
            time.sleep(1)
        log.info(f"Applied trigger: set Extract=yes for {len(updates)} rows: {', '.join(updated_companies[:5])}")

        # Now try to auto-fill from Brain contacts
        try:
            from outreach.outreach_data import Sheets
            sheets = Sheets()
            filled = sheets.auto_fill_from_brain()
            if filled:
                log.info(f"Brain auto-fill: {filled} contacts populated")
        except Exception as e:
            log.debug(f"Brain auto-fill skipped: {e}")

    else:
        log.info("All Applied jobs already have Extract=yes in Outreach")

    return len(updates)


if __name__ == "__main__":
    count = run()
    print(f"\n✓ Applied trigger: {count} rows updated")

