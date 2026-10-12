"""
ETL: Backfill analytics database from Google Sheets.

Run once to populate historical data, then incrementally on each aggregator run.

Usage:
    python3 -m analytics.etl          # full backfill
    python3 -m analytics.etl --incremental  # only new rows
"""
import os
import sys
import logging
from datetime import datetime

import gspread
from oauth2client.service_account import ServiceAccountCredentials

from analytics.store import AnalyticsStore
from analytics.models import JobRecord

log = logging.getLogger(__name__)

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CREDS_FILE = os.path.join(BASE, ".local", "credentials.json")


def _get_sheets():
    """Connect to Google Sheets."""
    scope = ['https://spreadsheets.google.com/feeds',
             'https://www.googleapis.com/auth/drive']
    creds = ServiceAccountCredentials.from_json_keyfile_name(CREDS_FILE, scope)
    return gspread.authorize(creds).open('H1B visa')


def _parse_row(row, outcome: str, headers: list = None) -> JobRecord:
    """Parse a sheet row into a JobRecord."""
    def g(idx, default=""):
        return row[idx].strip() if idx < len(row) and row[idx] else default

    if outcome == "valid":
        # [0]Sr [1]Status [2]Company [3]Title [4]DateApplied [5]URL [6]JobID
        # [7]JobType [8]Location [9]Resume [10]Remote [11]EntryDate [12]Source [13]Sponsorship
        return JobRecord(
            url=g(5), company=g(2), title=g(3), location=g(8),
            source=g(12), outcome="valid", resume_type=g(9, "SDE"),
            job_type=g(7, "Internship"), job_id=g(6, "N/A"),
            remote=g(10, "Unknown"), sponsorship=g(13, "Unknown"),
            entry_date=g(11), processed_at=g(11) or datetime.now().isoformat(),
        )
    elif outcome == "discarded":
        # [0]Sr [1]DiscardReason [2]Company [3]Title [4]DateApplied [5]URL [6]JobID
        # [7]JobType [8]Location [9]Remote [10]EntryDate [11]Source [12]Sponsorship
        return JobRecord(
            url=g(5), company=g(2), title=g(3), location=g(8),
            source=g(11) if g(11) in {"SimplifyJobs", "Jobright", "SWE List", "speedyapply_swe",
                         "vanshb03", "ZipRecruiter", "Email", "GitHub", "Manual",
                         "LinkedIn", "SWE List Email", "NU Works", "Discord"} else "Unknown",
            outcome="discarded", rejection_reason=g(1),
            job_type=g(7, "Internship"), job_id=g(6, "N/A"),
            remote=g(9, "Unknown"),
            entry_date=g(4), processed_at=g(10) or datetime.now().isoformat(),
        )
    elif outcome == "reviewed":
        # [0]Sr [1]Reason [2]Company [3]Title [4]URL [5]JobID
        # [6]JobType [7]Location [8]Remote [9]MovedDate [10]Source [11]Sponsorship
        # Smart source detection: if index 10 looks like a timestamp, it's not the source
        raw_source = g(10)
        known_sources = {"SimplifyJobs", "Jobright", "SWE List", "speedyapply_swe",
                         "vanshb03", "ZipRecruiter", "Email", "GitHub", "Manual",
                         "LinkedIn", "SWE List Email", "NU Works", "Discord"}
        if raw_source not in known_sources:
            raw_source = "Unknown"
        return JobRecord(
            url=g(4), company=g(2), title=g(3), location=g(7),
            source=raw_source, outcome="reviewed", rejection_reason=g(1),
            job_type=g(6, "Internship"), job_id=g(5, "N/A"),
            remote=g(8, "Unknown"),
            entry_date="", processed_at=g(9) or datetime.now().isoformat(),
        )
    return None


def backfill(incremental: bool = False):
    """Backfill analytics DB from all sheets."""
    store = AnalyticsStore()
    ss = _get_sheets()

    existing_count = store.total_jobs()
    _known_urls = set()
    if incremental and existing_count > 0:
        log.info(f"Incremental mode: {existing_count} existing jobs in analytics DB")
        # The flag previously ONLY printed this line - every row was inserted
        # again on each run, silently doubling the dataset. AnomalyDetector and
        # DataQualityScorer read this table, so the nightly digest was wrong.
        try:
            _known_urls = {r[0] for r in store.conn.execute(
                "SELECT DISTINCT url FROM jobs").fetchall()}
            log.info(f"Skipping {len(_known_urls)} URLs already recorded")
        except Exception as _e:
            log.warning(f"Could not load existing URLs: {_e}")

    sheets_config = [
        ("Valid Entries", "valid"),
        ("Discarded Entries", "discarded"),
        ("Reviewed - Not Applied", "reviewed"),
    ]

    total = 0
    for sheet_name, outcome in sheets_config:
        try:
            ws = ss.worksheet(sheet_name)
            rows = ws.get_all_values()
            if not rows:
                continue

            jobs = []
            for row in rows[1:]:  # skip header
                if not any(c.strip() for c in row[:4]):
                    continue
                try:
                    job = _parse_row(row, outcome)
                    if job and job.url not in _known_urls:
                        jobs.append(job)
                except Exception as e:
                    log.debug(f"Failed to parse row: {e}")

            if jobs:
                store.record_jobs_batch(jobs)
                total += len(jobs)
                print(f"  ✓ {sheet_name}: {len(jobs)} jobs")

        except Exception as e:
            log.warning(f"Failed to process {sheet_name}: {e}")

    print(f"\n✅ Backfill complete: {total} total jobs in analytics DB")
    store.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    incremental = "--incremental" in sys.argv
    backfill(incremental=incremental)

