#!/usr/bin/env python3
# cSpell:disable
"""
Automatic cleanup script - moves 'Not Applied' jobs to Reviewed sheet.
ENHANCED: Includes automatic 7-day backup to private GitHub repo.
ENHANCED: Moves expired jobs (blank status, 3+ days old) to Reviewed sheet.
"""

import gspread
from oauth2client.service_account import ServiceAccountCredentials
import datetime
import time
import os
import shutil
import subprocess
from pathlib import Path
import logging
# `log` was used in the capacity check and its except handler without
# ever being defined, so a genuine failure there raised NameError on
# top of the original error - in the script that resizes and deletes
# sheet rows.
log = logging.getLogger(__name__)

SHEET_NAME = "H1B visa"
WORKSHEET_NAME = "Valid Entries"
REVIEWED_WORKSHEET = "Reviewed - Not Applied"
CREDS_FILE = os.path.join(".local", "credentials.json")

BACKUP_FOLDER = "../job-tracker-secrets"
BACKUP_TRACKING_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    ".local",
    "last_backup_date.txt",
)
BACKUP_INTERVAL_DAYS = 7

EXPIRY_DAYS = 2  # Jobs older than this with no status get moved

FILES_TO_BACKUP = [
    "credentials.json",
    "gmail_credentials.json",
    "gmail_token.pickle",
    "jobright_cookies.json",
    "nu_cookies.json",
    "processed_emails.json",
    "workday_mapping.json",
    ".env",
    "Prasad Kanade SWE Resume.pdf",
    "Prasad Kanade ML Resume.pdf",
    "brain.json",
]


class ManualCleanup:
    # Local copy deleted: it lacked 'Tailor' and rewrote column B
    # validation from its own list, erasing the option every run.
    from aggregator.config import STATUS_COLORS  # noqa: E402
    STATUS_VALUES = list(STATUS_COLORS.keys())

    # Statuses that are PROTECTED - never moved regardless of age
    PROTECTED_STATUSES = {
        "Applied",
        "Rejected",
        "Screening",
        "OA Round 1",
        "OA Round 2",
        "Interview 1",
        "Interview 2",
        "Assessment",
        "Offer accepted",
    }

    def _ensure_sheet_capacity(self, ws, min_buffer=300):
        """Auto-expand sheet if within min_buffer rows of being full."""
        try:
            all_rows = ws.get_all_values()
            data_rows = len([r for r in all_rows if any(c.strip() for c in r[:4])])
            available = ws.row_count - data_rows
            if available < min_buffer:
                needed = min_buffer - available + 1000  # add 1000 extra
                self.ss.batch_update({"requests": [{"appendDimension": {
                    "sheetId": ws.id,
                    "dimension": "ROWS",
                    "length": needed
                }}]})
                import time; time.sleep(2)
                log.info(f"Auto-expanded {ws.title}: +{needed} rows (was {available} buffer)")
                print(f"  ✓ Auto-expanded {ws.title}: +{needed} rows")

            # Always clear column B color+validation on empty buffer rows
            last_data = data_rows + 1  # 1-indexed
            if ws.row_count > last_data:
                self.ss.batch_update({"requests": [
                    {
                        "repeatCell": {
                            "range": {
                                "sheetId": ws.id,
                                "startRowIndex": last_data,
                                "endRowIndex": min(ws.row_count, last_data + 500),
                                "startColumnIndex": 1,
                                "endColumnIndex": 2,
                            },
                            "cell": {"userEnteredFormat": {
                                "backgroundColor": {"red": 1.0, "green": 1.0, "blue": 1.0},
                            }},
                            "fields": "userEnteredFormat.backgroundColor",
                        }
                    },
                    {
                        "setDataValidation": {
                            "range": {
                                "sheetId": ws.id,
                                "startRowIndex": last_data,
                                "endRowIndex": min(ws.row_count, last_data + 500),
                                "startColumnIndex": 1,
                                "endColumnIndex": 2,
                            },
                            "rule": None,
                        }
                    }
                ]})
                import time; time.sleep(1)
        except Exception as e:
            log.debug(f"Sheet capacity check failed: {e}")

    def __init__(self):
        scope = [
            "https://spreadsheets.google.com/feeds",
            "https://www.googleapis.com/auth/drive",
        ]
        creds = ServiceAccountCredentials.from_json_keyfile_name(CREDS_FILE, scope)
        client = gspread.authorize(creds)

        self.spreadsheet = client.open(SHEET_NAME)
        self.sheet = self.spreadsheet.worksheet(WORKSHEET_NAME)
        self._init_reviewed_sheet()
        # Load Outreach Tracker to check Extract/email status before expiring
        self._outreach_map = {}
        try:
            from outreach.outreach_config import C, OUTREACH_TAB
            from outreach.outreach_data import _pad
            ows = self.spreadsheet.worksheet(OUTREACH_TAB)
            time.sleep(1)
            odata = ows.get_all_values()
            for row in odata[1:]:
                row = _pad(row)
                co = row[C["company"]].strip().lower()
                ti = row[C["title"]].strip().lower()
                extract = row[C["extract"]].strip().lower() if len(row) > C["extract"] else ""
                hm_email = row[C["hm_email"]].strip() if len(row) > C["hm_email"] else ""
                rec_email = row[C["rec_email"]].strip() if len(row) > C["rec_email"] else ""
                if co:
                    self._outreach_map[(co, ti)] = {
                        "extract": extract,
                        "hm_email": hm_email,
                        "rec_email": rec_email,
                    }
        except Exception as _oe:
            pass  # Non-fatal — cleanup proceeds without protection check

    def _init_reviewed_sheet(self):
        try:
            self.reviewed_sheet = self.spreadsheet.worksheet(REVIEWED_WORKSHEET)

            current_cols = len(self.reviewed_sheet.row_values(1))
            if current_cols < 12:
                # Widen columns ONLY. resize(rows=1000, cols=12) also SETS
                # rows to 1000, and this tab holds over 5,000 rows of review
                # history - it would have destroyed 4,000+ of them. The row
                # count must be preserved, so pass the current value.
                self.reviewed_sheet.resize(
                    rows=max(self.reviewed_sheet.row_count, 1000), cols=12)
                time.sleep(2)

            headers = self.reviewed_sheet.row_values(1)
            if "Sponsorship" not in headers:
                self.reviewed_sheet.update_cell(1, 12, "Sponsorship")
                time.sleep(1)
                self._format_headers(self.reviewed_sheet)

        except gspread.exceptions.WorksheetNotFound:
            self.reviewed_sheet = self.spreadsheet.add_worksheet(
                title=REVIEWED_WORKSHEET, rows=1000, cols=12
            )
            headers = [
                "Sr. No.",
                "Reason",
                "Company",
                "Title",
                "Job URL",
                "Job ID",
                "Job Type",
                "Location",
                "Remote?",
                "Moved Date",
                "Source",
                "Sponsorship",
            ]
            self.reviewed_sheet.append_row(headers)
            self._format_headers(self.reviewed_sheet)

    def _format_headers(self, sheet):
        try:
            sheet.format(
                "A1:L1",
                {
                    "horizontalAlignment": "CENTER",
                    "verticalAlignment": "MIDDLE",
                    "textFormat": {
                        "fontFamily": "Times New Roman",
                        "fontSize": 14,
                        "bold": True,
                        "foregroundColor": {"red": 0, "green": 0, "blue": 0},
                    },
                    "backgroundColor": {"red": 0.7, "green": 0.9, "blue": 0.7},
                },
            )
        except Exception as _e:
            pass  # suppressed: use log.debug(_e) to investigate

    def _parse_entry_date(self, date_str):
        """Parse Entry Date in any format the pipeline has used."""
        if not date_str:
            return None
        import re as _re
        clean = date_str.strip()
        # Strip "Rescued DD Mon, HH:MM PM" prefix
        _rescued = _re.match(r"Rescued\s+(\d{1,2}\s+\w+,\s+\d{1,2}:\d{2}\s+[AP]M)", clean)
        if _rescued:
            clean = _rescued.group(1)
        clean = _re.sub(r"\s*Rescued.*$", "", clean).strip()
        yr = datetime.datetime.now().year
        # Formats WITH year (don't append year)
        for fmt in ["%d-%b-%Y", "%d %B, %Y"]:
            try:
                return datetime.datetime.strptime(clean, fmt)
            except (ValueError, Exception):
                continue
        # Formats WITHOUT year (append year, handle rollover)
        for fmt in ["%d %B, %I:%M %p", "%d %b, %I:%M %p", "%d %B"]:
            try:
                dt = datetime.datetime.strptime(f"{clean} {yr}", f"{fmt} %Y")
                if dt > datetime.datetime.now():
                    dt = dt.replace(year=yr - 1)
                return dt
            except ValueError:
                continue
        return None

    def _is_expired(self, row):
        """Returns True if row has no protected status AND entry date is 2+ days old."""
        status = self._get_cell(row, 1)

        # Protected statuses — never move
        if status in self.PROTECTED_STATUSES:
            return False

        # Only move blank or "Not Applied" status rows
        if status not in ("", "Not Applied"):
            return False

        # PROTECTION: if Extract=yes AND both emails empty → outreach pipeline
        # is still working on finding emails — do NOT expire
        co = self._get_cell(row, 2).strip().lower()
        ti = self._get_cell(row, 3).strip().lower()
        outreach = self._outreach_map.get((co, ti), {})
        if outreach.get("extract", "").lower() == "yes":
            hm = outreach.get("hm_email", "").strip()
            rec = outreach.get("rec_email", "").strip()
            if not hm and not rec:
                return False  # Still being worked on

        # Check entry date (column index 11 = L = Entry Date)
        entry_date_str = self._get_cell(row, 11)
        if not entry_date_str:
            return False

        entry_date = self._parse_entry_date(entry_date_str)
        if not entry_date:
            return False

        # Set current year since our date format has no year
        now = datetime.datetime.now()
        entry_date = entry_date.replace(year=now.year)

        age_days = (now - entry_date).days
        return age_days >= EXPIRY_DAYS

    def cleanup_expired(self):
        """Move blank-status jobs older than EXPIRY_DAYS days to Reviewed sheet."""
        print("=" * 80)
        print(
            f"EXPIRY CLEANUP: Moving jobs with no status older than {EXPIRY_DAYS} days"
        )
        print("=" * 80)

        try:
            all_data = self.sheet.get_all_values()

            if len(all_data) <= 1:
                print("No jobs to check")
                return

            expired_rows = [row for row in all_data[1:] if self._is_expired(row)]
            remaining_rows = [
                row
                for row in all_data[1:]
                if not self._is_expired(row) and self._get_cell(row, 1)
            ]  # keep rows with any status

            # Also keep blank-status rows that are NOT expired yet
            not_expired_blank = [
                row
                for row in all_data[1:]
                if not self._is_expired(row)
                and self._get_cell(row, 1) not in self.PROTECTED_STATUSES
                and self._get_cell(row, 1) == ""
            ]

            # Keep any row that is NOT expired AND has real job data
            # (a real status OR a URL). Never rely on Sr. No. (col 0), which
            # can be blank after renumbering and would drop good rows.
            remaining_rows = [
                row
                for row in all_data[1:]
                if not self._is_expired(row)
                and (self._get_cell(row, 1).strip() or self._get_cell(row, 5).strip())
            ]

            if expired_rows:
                _total = max(1, len(all_data) - 1)
                if len(expired_rows) > 0.20 * _total:
                    print(
                        f"ABORT: would move {len(expired_rows)} of {_total} rows "
                        f"(>20%). Refusing to run. Investigate before cleaning."
                    )
                    return
                self._snapshot_sheet(all_data, tag="expiry")
                if getattr(self, "_dry_run", False):
                    print(f"DRY RUN: would move {len(expired_rows)} rows, moving nothing.")
                    return
                print(f"Found {len(expired_rows)} expired jobs to move")
                for row in expired_rows:
                    company = self._get_cell(row, 2)
                    entry_date = self._get_cell(row, 11)
                    print(f"  → {company} (added {entry_date})")

                self._move_to_reviewed(expired_rows, reason="Expired: 2+ days")
                self._repopulate_main_sheet(all_data, remaining_rows)

                current = self.sheet.row_count
                used = len(remaining_rows) + 1
                empty_rows = current - used
                if empty_rows < 200:
                    self.sheet.resize(rows=current + 1000)

                print(
                    f"✓ Moved {len(expired_rows)} expired jobs, {len(remaining_rows)} remaining"
                )
            else:
                print(
                    f"No expired jobs found (all blank-status jobs are under {EXPIRY_DAYS} days old)"
                )

            print("=" * 80 + "\n")

        except Exception as e:
            print(f"✗ Expiry cleanup error: {e}")
            import traceback

            traceback.print_exc()

    def cleanup(self):
        print("=" * 80)
        print("CLEANUP: Moving 'Not Applied' jobs")
        print("=" * 80)

        try:
            all_data = self.sheet.get_all_values()

            if len(all_data) <= 1:
                print("No jobs to clean")
                return

            # Only move "Not Applied" jobs older than 48 hours
            now = datetime.datetime.now()
            not_applied_rows = []
            for row in all_data[1:]:
                if self._get_cell(row, 1) != "Not Applied":
                    continue
                entry_date_str = self._get_cell(row, 11)
                entry_date = self._parse_entry_date(entry_date_str)
                if not entry_date:
                    continue  # Skip rows with no parseable date
                entry_date = entry_date.replace(year=now.year)
                age_hours = (now - entry_date).total_seconds() / 3600
                if age_hours >= 48:
                    not_applied_rows.append(row)
                # Jobs < 48 hours old are kept in Valid Entries
            # Keep: all non-"Not Applied" rows + fresh "Not Applied" rows (< 48 hours)
            moved_set = set(id(row) for row in not_applied_rows)
            # Keep any row not being moved that has real data (status OR url),
            # never rely on Sr. No. (col 0) which can be blank after renumbering.
            remaining_rows = [
                row for row in all_data[1:]
                if id(row) not in moved_set
                and (self._get_cell(row, 1).strip() or self._get_cell(row, 5).strip())
            ]

            if not_applied_rows:
                _total = max(1, len(all_data) - 1)
                if len(not_applied_rows) > 0.20 * _total:
                    print(
                        f"ABORT: would move {len(not_applied_rows)} of {_total} rows "
                        f"(>20%). Refusing to run. Investigate before cleaning."
                    )
                    return
                self._snapshot_sheet(all_data, tag="notapplied")
                if getattr(self, "_dry_run", False):
                    print(f"DRY RUN: would move {len(not_applied_rows)} rows, moving nothing.")
                    return
                print(
                    f"Moving {len(not_applied_rows)} jobs, keeping {len(remaining_rows)}"
                )
                self._move_to_reviewed(
                    not_applied_rows, reason="Does not match profile"
                )
                self._repopulate_main_sheet(all_data, remaining_rows)
                current = self.sheet.row_count
                used = len(remaining_rows) + 1
                empty_rows = current - used
                if empty_rows < 200:
                    self.sheet.resize(rows=current + 1000)
                    print(f"  ✓ Added 1000 buffer rows (now {current + 1000} total)")
                else:
                    print(f"  ℹ️  Buffer ok ({empty_rows} empty rows available)")
                print(
                    f"✓ Moved {len(not_applied_rows)} jobs, {len(remaining_rows)} remaining"
                )
            else:
                print("No 'Not Applied' jobs found")

            print("=" * 80 + "\n")

        except Exception as e:
            print(f"✗ Cleanup error: {e}")

    def _snapshot_sheet(self, all_data, tag="cleanup"):
        """Write a timestamped CSV of the sheet to .local/snapshots/ before any move."""
        import os as _os, csv as _csv, datetime as _dt
        d = _os.path.join(_os.path.dirname(__file__), "..", ".local", "snapshots")
        d = _os.path.abspath(d)
        _os.makedirs(d, exist_ok=True)
        ts = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        fp = _os.path.join(d, f"valid_entries_{tag}_{ts}.csv")
        try:
            with open(fp, "w", newline="", encoding="utf-8") as f:
                _csv.writer(f).writerows(all_data)
            print(f"  snapshot saved: {fp} ({len(all_data)} rows)")
        except Exception as e:
            print(f"  snapshot failed ({e}) - ABORTING move for safety")
            raise

    def _move_to_reviewed(self, rows_to_move, reason="Does not match profile"):
        reviewed_data = self.reviewed_sheet.get_all_values()
        used_rows = len(reviewed_data)
        total_rows = self.reviewed_sheet.row_count
        available_rows = total_rows - used_rows

        if available_rows < 250:
            new_total = total_rows + 1000
            print(
                f"  Expanding Reviewed sheet: {total_rows} → {new_total} rows ({available_rows} available < 250 threshold)"
            )
            self.reviewed_sheet.resize(rows=new_total)
            time.sleep(2)
            print(f"  ✓ Reviewed sheet now has {1000 + available_rows} available rows")

        # DEDUP GUARD: skip jobs already in the Reviewed tab.
        _existing = set()
        for _r in reviewed_data[1:]:
            _jid = (_r[5].strip().lower() if len(_r) > 5 else "")
            if _jid and _jid not in ("n/a", ""):
                _existing.add(_jid)
            else:
                _existing.add((
                    (_r[2].strip().lower() if len(_r) > 2 else ""),
                    (_r[3].strip().lower() if len(_r) > 3 else ""),
                    (_r[4].strip().lower() if len(_r) > 4 else ""),
                ))
        _fresh, _skipped = [], 0
        for row in rows_to_move:
            jid = self._get_cell(row, 5).strip().lower()
            key = jid if jid and jid not in ("n/a", "") else (
                self._get_cell(row, 2).strip().lower(),
                self._get_cell(row, 3).strip().lower(),
                self._get_cell(row, 6).strip().lower(),
            )
            if key in _existing:
                _skipped += 1
                continue
            _existing.add(key)
            _fresh.append(row)
        if _skipped:
            print(f"  Dedup guard: skipped {_skipped} already-reviewed jobs")
        rows_to_move = _fresh
        if not rows_to_move:
            print("  All jobs already in Reviewed tab - nothing to move")
            return
        next_row = len(reviewed_data) + 1

        reviewed_rows = [
            [
                next_row - 1 + idx,
                reason,
                self._get_cell(row, 2),
                self._get_cell(row, 3),
                self._get_cell(row, 5),
                self._get_cell(row, 6),
                self._get_cell(row, 7),
                self._get_cell(row, 8),
                self._get_cell(row, 9),
                self._format_date(),
                self._get_cell(row, 11, "GitHub"),
                self._get_cell(row, 12, "Unknown"),
            ]
            for idx, row in enumerate(rows_to_move)
        ]

        range_name = f"A{next_row}:L{next_row + len(reviewed_rows) - 1}"
        self.reviewed_sheet.update(
            values=reviewed_rows, range_name=range_name, value_input_option="RAW"
        )
        time.sleep(2)

        self.reviewed_sheet.format(
            range_name,
            {
                "horizontalAlignment": "CENTER",
                "verticalAlignment": "MIDDLE",
                "textFormat": {"fontFamily": "Times New Roman", "fontSize": 13},
            },
        )

        self._add_hyperlinks(
            self.reviewed_sheet, reviewed_rows, next_row, url_col_idx=4
        )

    def _repopulate_main_sheet(self, all_data, remaining_rows):
        if len(all_data) > 1:
            self.sheet.delete_rows(2, len(all_data))
            time.sleep(2)

        if not remaining_rows:
            return

        renumbered_rows = []
        for idx, row in enumerate(remaining_rows, start=1):
            padded_row = (row + [""] * 16)[:16]
            new_row = [idx] + padded_row[1:16]
            renumbered_rows.append(new_row)

        range_name = f"A2:P{1 + len(renumbered_rows)}"
        self.sheet.update(
            values=renumbered_rows, range_name=range_name, value_input_option="RAW"
        )
        time.sleep(2)

        self.sheet.format(
            range_name,
            {
                "horizontalAlignment": "CENTER",
                "verticalAlignment": "MIDDLE",
                "textFormat": {"fontFamily": "Times New Roman", "fontSize": 13},
            },
        )
        time.sleep(2)

        self._add_hyperlinks(self.sheet, renumbered_rows, 2, url_col_idx=5)
        self._reconstruct_search_links(self.sheet, renumbered_rows, 2)
        self._add_status_dropdowns(self.sheet, 2, len(renumbered_rows))
        self._apply_status_colors(self.sheet, 2, 2 + len(renumbered_rows))

    def _reconstruct_search_links(self, sheet, rows_data, start_row):
        """Reconstruct clickable HYPERLINK formulas for search link entries."""
        import urllib.parse
        search_updates = []
        for idx, row_data in enumerate(rows_data):
            url = self._get_cell(row_data, 5)
            company = self._get_cell(row_data, 2)
            title = self._get_cell(row_data, 3)
            if url and '🔍' in url and company:
                query = urllib.parse.quote(f"{company} {title} careers apply")
                formula = f'https://www.google.com/search?q={query}'
                row_num = start_row + idx
                search_updates.append((row_num, formula))

        if search_updates:
            for row_num, formula in search_updates:
                self.sheet.update(
                    range_name=f'F{row_num}',
                    values=[[formula]],
                    value_input_option='USER_ENTERED'
                )
            time.sleep(2)

    def _add_hyperlinks(self, sheet, rows_data, start_row, url_col_idx):
        url_requests = []

        for idx, row_data in enumerate(rows_data):
            url = self._get_cell(row_data, url_col_idx)

            if url and url.startswith("http"):
                url_requests.append(
                    {
                        "updateCells": {
                            "range": {
                                "sheetId": sheet.id,
                                "startRowIndex": start_row + idx - 1,
                                "endRowIndex": start_row + idx,
                                "startColumnIndex": url_col_idx,
                                "endColumnIndex": url_col_idx + 1,
                            },
                            "rows": [
                                {
                                    "values": [
                                        {
                                            "userEnteredValue": {"stringValue": url},
                                            "textFormatRuns": [
                                                {"format": {"link": {"uri": url}}}
                                            ],
                                        }
                                    ]
                                }
                            ],
                            "fields": "userEnteredValue,textFormatRuns",
                        }
                    }
                )

        if url_requests:
            self.spreadsheet.batch_update({"requests": url_requests})
            time.sleep(2)

    def _add_status_dropdowns(self, sheet, start_row, num_rows):
        dropdown_requests = [
            {
                "setDataValidation": {
                    "range": {
                        "sheetId": sheet.id,
                        "startRowIndex": start_row + idx - 1,
                        "endRowIndex": start_row + idx,
                        "startColumnIndex": 1,
                        "endColumnIndex": 2,
                    },
                    "rule": {
                        "condition": {
                            "type": "ONE_OF_LIST",
                            "values": [
                                {"userEnteredValue": status}
                                for status in self.STATUS_VALUES
                            ],
                        },
                        "showCustomUi": True,
                        "strict": False,
                    },
                }
            }
            for idx in range(num_rows)
        ]

        if dropdown_requests:
            self.spreadsheet.batch_update({"requests": dropdown_requests})
            time.sleep(2)

    def _apply_status_colors(self, sheet, start_row, end_row):
        try:
            all_data = sheet.get_all_values()
            color_requests = []

            # Never color past last row that has actual data
            last_data_idx = len(all_data) - 1
            while last_data_idx > 0 and not any(c.strip() for c in all_data[last_data_idx][:4]):
                last_data_idx -= 1
            safe_end = min(end_row, last_data_idx + 1)

            for row_idx in range(start_row - 1, min(safe_end, len(all_data))):
                if row_idx < 1 or row_idx >= len(all_data):
                    continue

                status = self._get_cell(all_data[row_idx], 1)
                color = self.STATUS_COLORS.get(status)

                if color:
                    text_color = (
                        {"red": 1.0, "green": 1.0, "blue": 1.0}
                        if status == "Offer accepted"
                        else {"red": 0.0, "green": 0.0, "blue": 0.0}
                    )

                    color_requests.append(
                        {
                            "repeatCell": {
                                "range": {
                                    "sheetId": sheet.id,
                                    "startRowIndex": row_idx,
                                    "endRowIndex": row_idx + 1,
                                    "startColumnIndex": 1,
                                    "endColumnIndex": 2,
                                },
                                "cell": {
                                    "userEnteredFormat": {
                                        "backgroundColor": color,
                                        "textFormat": {
                                            "foregroundColor": text_color,
                                            "fontFamily": "Times New Roman",
                                            "fontSize": 13,
                                        },
                                        "horizontalAlignment": "CENTER",
                                        "verticalAlignment": "MIDDLE",
                                    }
                                },
                                "fields": "userEnteredFormat",
                            }
                        }
                    )

            for i in range(0, len(color_requests), 20):
                batch = color_requests[i : i + 20]
                self.spreadsheet.batch_update({"requests": batch})
                time.sleep(1)

        except Exception as _e:
            pass  # suppressed: use log.debug(_e) to investigate

    def _get_cell(self, row, index, default=""):
        try:
            return row[index].strip() if len(row) > index and row[index] else default
        except:
            return default

    def _format_date(self):
        return datetime.datetime.now().strftime("%d %B, %I:%M %p")


def check_if_backup_needed():
    """Check if 7 days have passed since last backup."""
    if not os.path.exists(BACKUP_TRACKING_FILE):
        return True

    try:
        with open(BACKUP_TRACKING_FILE, "r") as f:
            last_backup_str = f.read().strip()

        last_backup = datetime.datetime.strptime(last_backup_str, "%Y-%m-%d")
        days_since = (datetime.datetime.now() - last_backup).days

        if days_since >= BACKUP_INTERVAL_DAYS:
            return True
        else:
            print(f"\nℹ️  Backup not needed (last backup {days_since} days ago)")
            return False
    except:
        return True


def backup_to_private_repo():
    """Automated backup of secret files to private GitHub repo."""
    print("\n" + "=" * 80)
    print("AUTOMATED BACKUP TO PRIVATE REPO")
    print("=" * 80)

    project_dir = Path(__file__).parent.parent
    backup_dir = project_dir.parent / "job-tracker-secrets"

    if not backup_dir.exists():
        print(f"⚠️  Backup directory not found: {backup_dir}")
        print(f"   Run setup: See BACKUP_AUTOMATION_COMPLETE_GUIDE.md")
        return False

    backed_up = []
    missing = []

    for filename in FILES_TO_BACKUP:
        source = project_dir / ".local" / filename
        if not source.exists():
            source = project_dir / filename
        destination = backup_dir / filename

        if source.exists():
            try:
                shutil.copy2(source, destination)
                backed_up.append(filename)
                print(f"  ✓ Backed up: {filename}")
            except Exception as e:
                print(f"  ✗ Failed: {filename} - {e}")
        else:
            missing.append(filename)

    timestamp_file = backup_dir / "last_backup.txt"
    with open(timestamp_file, "w") as f:
        f.write(
            f"Last backup: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        )
        f.write(f"Files backed up: {len(backed_up)}\n")
        f.write(f"Files: {', '.join(backed_up)}\n")

    print(f"\n  Backed up {len(backed_up)} files")
    if missing:
        print(f"  Skipped {len(missing)} missing: {', '.join(missing)}")

    try:
        original_dir = os.getcwd()
        os.chdir(backup_dir)

        subprocess.run(["git", "add", "."], check=True, capture_output=True)

        commit_msg = f"Auto-backup {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}"
        result = subprocess.run(
            ["git", "commit", "-m", commit_msg], capture_output=True, text=True
        )

        if "nothing to commit" in result.stdout:
            print(f"\n  ℹ️  No changes since last backup")
            os.chdir(original_dir)
            with open(BACKUP_TRACKING_FILE, "w") as f:
                f.write(datetime.datetime.now().strftime("%Y-%m-%d"))
            print("=" * 80)
            return True

        push_result = subprocess.run(
            ["git", "push", "origin", "main"],
            check=True,
            capture_output=True,
            text=True,
        )

        os.chdir(original_dir)

        with open(BACKUP_TRACKING_FILE, "w") as f:
            f.write(datetime.datetime.now().strftime("%Y-%m-%d"))

        print(f"\n  ✅ Pushed to private GitHub repo")
        print(
            f"  Next backup: {(datetime.datetime.now() + datetime.timedelta(days=BACKUP_INTERVAL_DAYS)).strftime('%Y-%m-%d')}"
        )
        print("=" * 80)
        return True

    except subprocess.CalledProcessError as e:
        os.chdir(original_dir)
        print(f"\n  ✗ Git error: {e}")
        print("=" * 80)
        return False
    except Exception as e:
        os.chdir(original_dir)
        print(f"\n  ✗ Backup failed: {e}")
        print("=" * 80)
        return False


if __name__ == "__main__":
    import sys as _sys
    cleaner = ManualCleanup()
    cleaner._dry_run = "--dry-run" in _sys.argv
    # Auto-expand sheets if getting full
    for _ws_name in ["Discarded Entries", "Reviewed - Not Applied", "Valid Entries"]:
        try:
            _ws = cleaner.ss.worksheet(_ws_name)
            cleaner._ensure_sheet_capacity(_ws)
        except Exception:
            pass

    # Run expiry cleanup FIRST (blank status, 3+ days old)
    cleaner.cleanup_expired()

    # Then run standard Not Applied cleanup
    cleaner.cleanup()

    if check_if_backup_needed():
        backup_to_private_repo()

    print()


# ── Self-healing log rotation (runs daily via cleanup cron) ──
def rotate_logs():
    """Rotate logs, prune unbounded files, merge legacy bounce files."""
    import json as _json, os as _os, datetime as _dt
    LOCAL = _os.path.join(_os.path.dirname(__file__), "..", ".local")
    LOCAL = _os.path.abspath(LOCAL)

    # sent_log.json: prune entries older than 90 days
    sl_f = _os.path.join(LOCAL, "sent_log.json")
    if _os.path.exists(sl_f):
        try:
            sl = _json.load(open(sl_f))
            cutoff = (_dt.date.today() - _dt.timedelta(days=90)).isoformat()
            pruned = {k: v for k, v in sl.items() if isinstance(v, str) and v[:10] >= cutoff}
            if len(pruned) < len(sl):
                _json.dump(pruned, open(sl_f+".tmp","w"), indent=2)
                _os.replace(sl_f+".tmp", sl_f)
                print(f"  [rotate] sent_log.json: {len(sl)} -> {len(pruned)} entries")
        except Exception: pass

    # mx_cache.json: expire entries older than 30 days
    mx_f = _os.path.join(LOCAL, "mx_cache.json")
    if _os.path.exists(mx_f):
        try:
            mx = _json.load(open(mx_f))
            cutoff_ts = (_dt.datetime.now() - _dt.timedelta(days=30)).timestamp()
            pruned_mx = {k: v for k, v in mx.items()
                         if not isinstance(v, dict) or v.get("ts", 9e9) > cutoff_ts}
            if len(pruned_mx) < len(mx):
                _json.dump(pruned_mx, open(mx_f+".tmp","w"), indent=2)
                _os.replace(mx_f+".tmp", mx_f)
                print(f"  [rotate] mx_cache.json: {len(mx)} -> {len(pruned_mx)} entries")
        except Exception: pass

    # bounced_emails.json: merge into bounce_log.json then delete
    bl_f = _os.path.join(LOCAL, "bounce_log.json")
    be_f = _os.path.join(LOCAL, "bounced_emails.json")
    if _os.path.exists(bl_f) and _os.path.exists(be_f):
        try:
            bl = _json.load(open(bl_f))
            be = _json.load(open(be_f))
            added = sum(1 for e, d in be.items() if e not in bl and bl.update({e: d if isinstance(d,dict) else {"legacy":True}}) is None)
            if added:
                _json.dump(bl, open(bl_f+".tmp","w"), indent=2)
                _os.replace(bl_f+".tmp", bl_f)
                print(f"  [rotate] Merged {added} entries -> bounce_log.json")
            _os.remove(be_f)
            print(f"  [rotate] Deleted legacy bounced_emails.json")
        except Exception: pass


    # Weekly backup of secrets (every Sunday)
    try:
        import datetime as _dt
        if _dt.datetime.now().weekday() == 6:  # Sunday
            import importlib.util, os
            spec = importlib.util.spec_from_file_location(
                "backup_secrets",
                os.path.join(os.path.dirname(__file__), "backup_secrets.py")
            )
            bs = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(bs)
            bs.backup()
            print("  [backup_secrets] Done")
    except Exception as e:
        print(f"  [backup_secrets] Skipped: {e}")

    # Run auto_extract to set Extract=yes on qualifying outreach entries
    try:
        import importlib.util, os
        spec = importlib.util.spec_from_file_location(
            "auto_extract",
            os.path.join(os.path.dirname(__file__), "auto_extract.py")
        )
        ae = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(ae)
        ae.main()
        print("  [auto_extract] Done")
    except Exception as e:
        print(f"  [auto_extract] Skipped: {e}")
        LOCAL = os.path.join(os.path.dirname(__file__), "..", ".local")
        LOCAL = os.path.abspath(LOCAL)

        # Large logs: keep last 500 lines
        large_logs = [
            "skipped_jobs.log", "outreach.log", "resume_sync.log",
            "watchdog_alerts.log", "failures.log", "simplify_retry.log",
            "watchdog.log", "watchdog_err.log",
        ]
        for log in large_logs:
            f = os.path.join(LOCAL, log)
            if not os.path.exists(f):
                continue
            lines = open(f).readlines()
            if len(lines) > 500:
                open(f, "w").writelines(lines[-500:])
                print(f"  [rotate] {log}: {len(lines)} → 500 lines")

        # Cron logs: delete logs older than 7 days
        cron_dir = os.path.join(LOCAL, "cron_logs")
        if os.path.exists(cron_dir):
            cutoff = datetime.datetime.now() - datetime.timedelta(days=7)
            deleted = 0
            for f in os.listdir(cron_dir):
                fp = os.path.join(cron_dir, f)
                if os.path.getmtime(fp) < cutoff.timestamp():
                    os.remove(fp)
                    deleted += 1
            if deleted:
                print(f"  [rotate] Deleted {deleted} cron logs older than 7 days")

        # .bak files: delete all (git is the backup)
        base = os.path.join(os.path.dirname(__file__), "..")
        deleted_bak = 0
        for root, dirs, files in os.walk(base):
            dirs[:] = [d for d in dirs if d != "venv" and d != ".git"]
            for f in files:
                if ".bak_" in f:
                    os.remove(os.path.join(root, f))
                    deleted_bak += 1
        if deleted_bak:
            print(f"  [rotate] Deleted {deleted_bak} .bak files")

        # Empty launchd logs: delete
        for f in os.listdir(LOCAL):
            if f.startswith("launchd_") and f.endswith(".log"):
                fp = os.path.join(LOCAL, f)
                if os.path.getsize(fp) == 0:
                    os.remove(fp)

        # failed_simplify_urls.json: prune entries older than 3 days
        fsf = os.path.join(LOCAL, "failed_simplify_urls.json")
        if os.path.exists(fsf):
            import json as _json, datetime as _dt
            try:
                _data = _json.load(open(fsf))
                _cutoff = (_dt.datetime.now() - _dt.timedelta(days=3)).strftime("%Y-%m-%d")
                _kept = {k:v for k,v in _data.items() if isinstance(v,str) and v >= _cutoff}
                if len(_kept) < len(_data):
                    _json.dump(_kept, open(fsf,"w"), indent=2)
                    print(f"  [rotate] failed_simplify_urls: pruned {len(_data)-len(_kept)} stale entries")
            except Exception:
                pass

        # Clear .pyc cache files (can cause import errors)
        import glob as _glob
        _pyc = _glob.glob(f"{base}/**/*.pyc", recursive=True)
        _pyc = [f for f in _pyc if "venv" not in f]
        for f in _pyc:
            try: os.remove(f)
            except: pass
        if _pyc:
            print(f"  [rotate] Cleared {len(_pyc)} .pyc files")

        # brain.json: warn if > 2MB
        brain = os.path.join(LOCAL, "brain.json")
        if os.path.exists(brain) and os.path.getsize(brain) > 2_000_000:
            print(f"  [warn] brain.json is {os.path.getsize(brain)//1024}KB — consider pruning old entries")

    # failures.log: keep only last 30 days
    fl = _os.path.join(LOCAL, "failures.log")
    if _os.path.exists(fl):
        try:
            lines = open(fl).readlines()
            cutoff = (_dt.date.today() - _dt.timedelta(days=30)).strftime("%Y-%m")
            kept = [l for l in lines if len(l) < 5 or l[1:8] >= cutoff]
            if len(kept) < len(lines):
                open(fl, "w").writelines(kept)
                print(f"  [rotate] failures.log: {len(lines)} -> {len(kept)} lines")
        except Exception: pass

    # watchdog_alerts.log: cap at 200 lines
    wal = _os.path.join(LOCAL, "watchdog_alerts.log")
    if _os.path.exists(wal):
        try:
            lines = open(wal).readlines()
            if len(lines) > 200:
                open(wal, "w").writelines(lines[-150:])
                print(f"  [rotate] watchdog_alerts.log trimmed to 150 lines")
        except Exception: pass

    # skipped_jobs.log: cap at 1000 lines
    sjl = _os.path.join(LOCAL, "skipped_jobs.log")
    if _os.path.exists(sjl):
        try:
            lines = open(sjl).readlines()
            if len(lines) > 1000:
                open(sjl, "w").writelines(lines[-700:])
                print(f"  [rotate] skipped_jobs.log trimmed to 700 lines")
        except Exception: pass

    # wakeup.log: cap at 300 lines
    wl = _os.path.join(LOCAL, "wakeup.log")
    if _os.path.exists(wl):
        try:
            lines = open(wl).readlines()
            if len(lines) > 300:
                open(wl, "w").writelines(lines[-200:])
                print(f"  [rotate] wakeup.log trimmed to 200 lines")
        except Exception: pass

    # outreach.log: cap at 1000 lines
    ol = _os.path.join(LOCAL, "outreach.log")
    if _os.path.exists(ol):
        try:
            lines = open(ol).readlines()
            if len(lines) > 1000:
                open(ol, "w").writelines(lines[-700:])
                print(f"  [rotate] outreach.log trimmed to 700 lines")
        except Exception: pass

        # failed_simplify_urls.json: keep last 200 entries
        fsf = os.path.join(LOCAL, "failed_simplify_urls.json")
        if os.path.exists(fsf):
            import json
            try:
                data = json.load(open(fsf))
                if isinstance(data, list) and len(data) > 200:
                    json.dump(data[-200:], open(fsf, "w"), indent=2)
                    print(f"  [rotate] failed_simplify_urls.json: trimmed to 200 entries")
            except Exception:
                pass

    rotate_logs()
