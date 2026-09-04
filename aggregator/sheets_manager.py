#!/usr/bin/env python3

import gspread
import time as _time
import functools

def _sheets_retry(func):
    """Retry Google Sheets API calls on quota/rate limit errors."""
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        for attempt in range(5):
            try:
                return func(*args, **kwargs)
            except Exception as e:
                msg = str(e).lower()
                if any(x in msg for x in ("429", "quota", "rate limit", "resource exhausted", "service unavailable")):
                    wait = (2 ** attempt) * 5  # 5, 10, 20, 40, 80s
                    import logging
                    logging.warning(f"Sheets quota hit, retrying in {wait}s (attempt {attempt+1}/5)")
                    _time.sleep(wait)
                else:
                    raise
        return func(*args, **kwargs)
    return wrapper
def _retry_call(fn, *a, **kw):
    """Run a gspread call under _sheets_retry.

    _sheets_retry was defined and then never applied to a single method: a
    dead safety net. The 29 Aug run died on an unretried values_update 429
    mid-write. Wrapping the raw call is safer than decorating 38 methods.
    """
    return _sheets_retry(fn)(*a, **kw)


import time
import re
from functools import lru_cache
from oauth2client.service_account import ServiceAccountCredentials

from aggregator.config import (
    SHEET_NAME,
    WORKSHEET_NAME,
    DISCARDED_WORKSHEET,
    REVIEWED_WORKSHEET,
    SHEETS_CREDS_FILE,
    STATUS_COLORS,
)


# ── Google Sheets circuit breaker ──────────────────────────────────────
# Sheets has hard rate limits. Without a breaker the pipeline keeps hammering
# a 429-ing API for the rest of the run. aggregator/circuit_breaker.py was
# written for exactly this and had zero callers.
try:
    from aggregator.circuit_breaker import CircuitBreakerRegistry as _CBR
    _SHEETS_CB = _CBR.get("google_sheets", failure_threshold=5, reset_timeout=60)
except Exception:
    _SHEETS_CB = None


def _cb_allows():
    return _SHEETS_CB.allow_request() if _SHEETS_CB else True


def _cb_ok():
    if _SHEETS_CB:
        _SHEETS_CB.record_success()


def _cb_fail():
    if _SHEETS_CB:
        _SHEETS_CB.record_failure()


class SheetsManager:
    def __init__(self):
        scope = [
            "https://spreadsheets.google.com/feeds",
            "https://www.googleapis.com/auth/drive",
        ]
        creds = ServiceAccountCredentials.from_json_keyfile_name(
            SHEETS_CREDS_FILE, scope
        )
        client = gspread.authorize(creds)
        self.spreadsheet = client.open(SHEET_NAME)
        self.valid_sheet = self.spreadsheet.worksheet(WORKSHEET_NAME)
        self._initialize_sheets()
        self._auto_expand_all_sheets()

    def _auto_expand_all_sheets(self):
        """If any sheet has < 200 empty rows available, add 1000 more rows.
        FIX 7: use row_count property (no API call) instead of col_values(1).
        Also cache last check timestamp — only re-check every 24 hours.
        """
        import os, json, time as _time
        _cache_file = os.path.join(".local", "expand_check_cache.json")
        _cache_ttl = 86400  # 24 hours in seconds
        try:
            _now = _time.time()
            _last = 0
            if os.path.exists(_cache_file):
                try:
                    _last = json.load(open(_cache_file)).get("last_check", 0)
                except Exception as _sw:
                    from aggregator.swallowed import swallow as _s; _s('cache.last_check_read', _sw)
            if _now - _last < _cache_ttl:
                return  # checked recently, skip
        except Exception:
            pass

        try:
            for ws in self.spreadsheet.worksheets():
                # FIX 7: row_count is a property — no API call needed
                # We still need used_rows but fetch only col A values count
                # Use row_count directly as upper bound and only resize if needed
                try:
                    all_vals = ws.col_values(1)
                    used_rows = len(all_vals)
                except Exception:
                    used_rows = ws.row_count // 2  # safe fallback
                empty_rows = ws.row_count - used_rows
                if empty_rows < 200:
                    new_count = ws.row_count + 1000
                    ws.resize(rows=new_count)
                    time.sleep(1)
        except Exception:
            pass

        # Save timestamp
        try:
            import json as _json
            os.makedirs(".local", exist_ok=True)
            _json.dump({"last_check": _time.time()}, open(_cache_file, "w"))
        except Exception as _sw:
            from aggregator.swallowed import swallow as _s; _s('cache.last_check_write', _sw)

    def _initialize_sheets(self):
        sheet_configs = {
            DISCARDED_WORKSHEET: [
                "Sr. No.",
                "Discard Reason",
                "Company",
                "Title",
                "Date Applied",
                "Job URL",
                "Job ID",
                "Job Type",
                "Location",
                "Remote?",
                "Entry Date",
                "Source",
                "Sponsorship",
            ],
            REVIEWED_WORKSHEET: [
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
            ],
        }

        from gspread.exceptions import WorksheetNotFound as _WNF, APIError as _APIErr
        for sheet_name, headers in sheet_configs.items():
            sheet = None
            for _attempt in range(3):
                try:
                    sheet = self.spreadsheet.worksheet(sheet_name)
                    break
                except _WNF:
                    try:
                        sheet = self.spreadsheet.add_worksheet(
                            title=sheet_name, rows=1000, cols=len(headers)
                        )
                        sheet.append_row(headers)
                        self._format_headers(sheet, len(headers))
                        break
                    except _APIErr as _e:
                        if "already exists" in str(_e):
                            sheet = self.spreadsheet.worksheet(sheet_name)
                            break
                        raise
                except Exception:
                    import time as _t
                    _t.sleep(2)
            if sheet is None:
                sheet = self.spreadsheet.worksheet(sheet_name)

            setattr(self, sheet_name.lower().replace(" ", "_").replace("-", "_"), sheet)

    def _apply_not_applied_colors(self, start_row, count):
        """Apply blue color to 'Not Applied' cells in new rows."""
        try:
            _NOT_APPLIED_BG = {"red": 0.6, "green": 0.76, "blue": 1.0}
            requests = []
            for i in range(count):
                row_idx = start_row - 1 + i  # 0-indexed
                requests.append({
                    "repeatCell": {
                        "range": {
                            "sheetId": self.valid_sheet.id,
                            "startRowIndex": row_idx,
                            "endRowIndex": row_idx + 1,
                            "startColumnIndex": 1,
                            "endColumnIndex": 2,
                        },
                        "cell": {"userEnteredFormat": {
                            "backgroundColor": _NOT_APPLIED_BG,
                            "textFormat": {
                                "foregroundColor": {"red": 0, "green": 0, "blue": 0},
                                "fontFamily": "Times New Roman", "fontSize": 13,
                            },
                            "horizontalAlignment": "CENTER",
                            "verticalAlignment": "MIDDLE",
                        }},
                        "fields": "userEnteredFormat",
                    }
                })
            if requests:
                for i in range(0, len(requests), 50):
                    self.spreadsheet.batch_update({"requests": requests[i:i+50]})
                import time; time.sleep(1)
        except Exception as _sw:
            from aggregator.swallowed import swallow as _s; _s('sheet.batch_format', _sw)

    def _fix_broken_search_links(self):
        """Fix any search links written as plain text instead of HYPERLINK formula."""
        try:
            import urllib.parse
            data = self.valid_sheet.get_all_values()
            formulas = self.valid_sheet.get('F1:F' + str(len(data)), value_render_option='FORMULA')
            
            for i, (row, formula_row) in enumerate(zip(data[1:], formulas[1:]), start=2):
                if len(row) < 6:
                    continue
                display = row[5].strip()
                formula = formula_row[0] if formula_row else ""
                company = row[2].strip()
                title = row[3].strip()
                
                if '🔍' in display and 'HYPERLINK' not in formula and company:
                    query = urllib.parse.quote(f"{company} {title} careers apply")
                    new_formula = f"https://www.google.com/search?q={query}"
                    self.valid_sheet.update(
                        range_name=f'F{i}', values=[[new_formula]],
                        value_input_option='USER_ENTERED'
                    )
            import time; time.sleep(1)
        except Exception as _sw:
            from aggregator.swallowed import swallow as _s; _s('sheet.url_formula_update', _sw)

    def _ensure_status_dropdowns(self):
        """One-time: set dropdown validation on entire Status column."""
        try:
            # Single source of truth: config.STATUS_COLORS. A local copy here

            # would silently drop any status added to config - exactly how

            # COMPANY_NAME_FIXES and GARBAGE_COMPANY_NAMES went stale.

            _STATUS_VALUES = list(STATUS_COLORS.keys())  # single source
            self.spreadsheet.batch_update({"requests": [{
                "setDataValidation": {
                    "range": {
                        "sheetId": self.valid_sheet.id,
                        "startRowIndex": 1, "endRowIndex": 5000,
                        "startColumnIndex": 1, "endColumnIndex": 2,
                    },
                    "rule": {
                        "condition": {
                            "type": "ONE_OF_LIST",
                            "values": [{"userEnteredValue": s} for s in _STATUS_VALUES],
                        },
                        "showCustomUi": True, "strict": False,
                    },
                }
            }]})
        except Exception:
            pass

    def load_existing_jobs(self):
        existing = {"jobs": set(), "urls": set(), "job_ids": set(), "cache": {}}
        try:
            from outreach.brain import Brain
            b = Brain.get()
            for nid in b._data.get("job_id_registry", {}):
                existing["job_ids"].add(nid)
        except Exception:
            pass

        for sheet in [
            self.valid_sheet,
            self.discarded_entries,
            self.reviewed___not_applied,
        ]:
            for row in sheet.get_all_values()[1:]:
                if len(row) <= 5:
                    continue

                company, title = row[2].strip(), row[3].strip()
                url = row[5].strip() if len(row) > 5 else ""
                job_id = row[6].strip() if len(row) > 6 else ""

                if company and title:
                    # Strip LLC/Inc/Corp/(...) to MATCH _is_duplicate key (dedup hole fix)
                    import re as _cc
                    _co_clean = _cc.sub(r",?\s*(Inc\.?|LLC|Ltd\.?|Corp\.?|L\.?P\.?)\s*$", "", company, flags=_cc.I).strip()
                    _co_clean = _cc.sub(r"\s*\([^)]+\)\s*$", "", _co_clean).strip()
                    key = self._normalize(f"{_co_clean}_{title}")
                    existing["jobs"].add(key)
                    existing["cache"][key] = {
                        "company": company,
                        "title": title,
                        "job_id": job_id,
                        "url": url,
                    }

                if url and "http" in url:
                    # Only seed URLs that identify ONE posting. A google
                    # search URL is shared by 306 rows; seeding it would make
                    # every future fallback job look like a duplicate.
                    try:
                        from aggregator.utils import is_identifying_url
                        if is_identifying_url(url):
                            existing["urls"].add(self._clean_url(url))
                    except Exception:
                        existing["urls"].add(self._clean_url(url))

                if (
                    job_id
                    and job_id not in ["N/A", ""]
                    and not job_id.startswith("HASH_")
                ):
                    existing["job_ids"].add(job_id.lower())
                    try:
                        from outreach.brain import Brain
                        nid = Brain.get().normalize_job_id(job_id)
                        if nid:
                            existing["job_ids"].add(nid)
                    except Exception:
                        pass

        from aggregator.config import SHOW_LOADING_STATS

        if SHOW_LOADING_STATS:
            print(
                f"Loaded: {len(existing['jobs'])} jobs, {len(existing['urls'])} URLs, {len(existing['job_ids'])} IDs"
            )
        return existing

    def load_urls_only(self):
        urls = set()
        for sheet in [
            self.valid_sheet,
            self.discarded_entries,
            self.reviewed___not_applied,
        ]:
            for row in sheet.get_all_values()[1:]:
                if len(row) > 5:
                    url = row[5].strip()
                    if url and "http" in url:
                        urls.add(self._clean_url(url))
        return urls

    def load_company_titles_only(self):
        company_titles = set()
        for sheet in [
            self.valid_sheet,
            self.discarded_entries,
            self.reviewed___not_applied,
        ]:
            for row in sheet.get_all_values()[1:]:
                if len(row) > 3:
                    company = row[2].strip()
                    title = row[3].strip()
                    if company and title:
                        key = self._normalize(f"{company}|{title}")
                        company_titles.add(key)
        return company_titles

    def load_job_ids_only(self):
        job_ids = set()
        for sheet in [
            self.valid_sheet,
            self.discarded_entries,
            self.reviewed___not_applied,
        ]:
            for row in sheet.get_all_values()[1:]:
                if len(row) > 6:
                    job_id = row[6].strip()
                    if (
                        job_id
                        and job_id not in ["N/A", ""]
                        and not job_id.startswith("HASH_")
                    ):
                        job_ids.add(job_id.lower())
        return job_ids

    def get_next_row_numbers(self):
        return {
            "valid": self._find_next_row(self.valid_sheet)["row"],
            "valid_sr_no": self._find_next_row(self.valid_sheet)["sr_no"],
            "discarded": self._find_next_row(self.discarded_entries)["row"],
            "discarded_sr_no": self._find_next_row(self.discarded_entries)["sr_no"],
        }

    def _find_next_row(self, sheet):
        data = sheet.get_all_values()
        for idx, row in enumerate(data[1:], start=2):
            if len(row) <= 2 or not (row[2].strip() or row[3].strip()):
                return {"row": idx, "sr_no": idx - 1}
        return {"row": len(data) + 1, "sr_no": len(data)}

    def ensure_capacity(self, sheet, start_row, n_rows, headroom=200):
        """Guarantee the sheet can hold rows start_row..start_row+n_rows-1.

        Returns True only when the capacity actually exists. Callers MUST
        skip the write on False: a deferred batch is retried next run, a
        batch written into a full sheet is gone.

        The previous version read the whole sheet with get_all_values() to
        count used rows, then swallowed any exception from that read AND from
        the resize, and its caller swallowed the result again. When either
        hit a rate limit - which a full-sheet read invites - the guard said
        nothing, the write went into a sheet with 0 free rows, and the jobs
        vanished. The sheet sat at exactly 1410 of 1410.

        This needs no read: start_row and n_rows are known exactly, and
        row_count is a cached property with no API cost. Free to run before
        every write, and it cannot be skipped by a TTL.
        """
        try:
            needed = start_row + max(n_rows, 0) - 1 + headroom
            if sheet.row_count >= needed:
                return True
            import time as _t
            import logging as _l
            sheet.resize(rows=needed + 1000)
            _t.sleep(1)
            _l.info("Expanded %s to %d rows", sheet.title, sheet.row_count)
            return True
        except Exception as e:
            import logging as _l
            _l.error("CAPACITY: cannot expand %s (%s) - skipping this write, "
                     "it will be retried next run", sheet.title, e)
            return False

    def ensure_sufficient_rows(self, sheet, min_available=250, add_count=1000):
        """Kept for older call sites. Delegates to ensure_capacity."""
        try:
            return self.ensure_capacity(
                sheet, sheet.row_count + 1, 0, headroom=min_available)
        except Exception:
            return False

    # Known H1B sponsors — auto-set sponsorship=Yes for these companies
    _KNOWN_SPONSORS = {
        'google','alphabet','microsoft','amazon','apple','meta','netflix',
        'nvidia','intel','amd','qualcomm','broadcom','cisco','oracle',
        'salesforce','adobe','servicenow','workday','splunk','palo alto',
        'crowdstrike','fortinet','vmware','dell','hp','ibm','accenture',
        'jpmorgan','goldman sachs','morgan stanley','bank of america',
        'wells fargo','citibank','citi','capital one','american express',
        'visa','mastercard','paypal','stripe','square','block','bloomberg',
        'blackrock','fidelity','vanguard','charles schwab','td bank',
        'barclays','deutsche bank','johnson & johnson','pfizer','merck',
        'abbott','medtronic','boston scientific','becton dickinson','baxter',
        'stryker','deloitte','mckinsey','bcg','bain','kpmg','pwc','ey',
        'texas instruments','applied materials','lam research','kla','asml',
        'marvell','micron','western digital','seagate','uber','lyft','airbnb',
        'doordash','instacart','linkedin','spotify','snap','pinterest',
        'dropbox','box','twilio','zendesk','hubspot','datadog','snowflake',
        'databricks','confluent','mongodb','elastic','hashicorp','gitlab',
        'github','atlassian','slack','verizon','at&t','t-mobile','comcast',
        'disney','tesla','ford','gm','general motors','boeing','lockheed',
        'raytheon','northrop','spacex','waymo','zoox','cruise','rivian',
        'lucid','walmart','target','costco','home depot','best buy',
        'two sigma','de shaw','jane street','citadel','jump trading',
        'optiver','akuna','susquehanna','imc','hudson river',
    }

    @staticmethod
    def _enrich_sponsorship(company: str, current: str) -> str:
        """Auto-set sponsorship=Yes for known H1B sponsors if currently Unknown."""
        if current and current.lower() not in ('unknown', ''):
            return current
        co = company.lower().strip()
        for sponsor in SheetsManager._KNOWN_SPONSORS:
            if sponsor in co:
                return 'Yes'
        return current or 'Unknown'

    def add_valid_jobs(self, jobs, start_row, start_sr_no):
        if not jobs:
            return 0

        # Deduplicate within the batch itself before writing
        # (aggregator may produce same job from two sources in one run)
        seen_keys = set()
        unique_jobs = []
        for job in jobs:
            key = self._normalize(f"{job.get('company','').strip()}|{job.get('title','').strip()}")
            if key and key not in seen_keys:
                seen_keys.add(key)
                unique_jobs.append(job)
        if len(unique_jobs) < len(jobs):
            import logging as _l
            _l.info(f"Deduped {len(jobs)-len(unique_jobs)} within-batch duplicates before write")
        jobs = unique_jobs
        if not jobs:
            return 0

        # ------------------------------------------------------------------
        # PRE-WRITE SHEET REFRESH
        # The batch dedup above only compares jobs against each other. The
        # aggregator's existing_jobs snapshot is taken at startup and the
        # write happens up to three hours later, so a row another run added
        # in between is invisible and gets written twice. That produced 50
        # duplicate rows across 46 company+title pairs.
        #
        # Re-read the sheet here, immediately before writing, using the
        # canonical _dedup_key so read and write keys are identical. This
        # holds even if two runs overlap, so it does not depend on the lock.
        # ------------------------------------------------------------------
        try:
            from aggregator.run_aggregator import _dedup_key as _dk
            _live = set()
            for _r in _retry_call(self.valid_sheet.get_all_values)[1:]:
                if len(_r) > 3 and _r[2].strip() and _r[3].strip():
                    _live.add(_dk(_r[2].strip(), _r[3].strip()))
            _before = len(jobs)
            jobs = [j for j in jobs
                    if _dk(j.get("company", ""), j.get("title", "")) not in _live]
            _dropped = _before - len(jobs)
            if _dropped:
                import logging as _l2
                msg = (f"Pre-write refresh dropped {_dropped} rows already in "
                       f"the sheet (concurrent run or stale snapshot)")
                _l2.warning(msg); print(f"  {msg}")
            if not jobs:
                print("  All rows already present, nothing to write")
                return 0
        except Exception as _pwe:
            import logging as _l3
            _l3.error(f"Pre-write refresh FAILED, duplicates possible: {_pwe}")
            print(f"  WARNING pre-write refresh failed: {_pwe}")

        self.ensure_sufficient_rows(self.valid_sheet)

        from aggregator.utils import DataSanitizer

        sanitized_jobs = [DataSanitizer.sanitize_all_fields(job) for job in jobs]

        rows = [
            [
                start_sr_no + idx,
                "Not Applied",
                self._clean_company(job["company"]),
                job["title"],
                "N/A",
                self._smart_url(job),
                self._clean_job_id(job.get("job_id", "N/A")),
                job["job_type"],
                self._clean_location(job["location"]),
                self._classify_resume(job["title"]),
                job["remote"],
                job["entry_date"],
                job["source"],
                self._enrich_sponsorship(job["company"], job.get("sponsorship", "Unknown")),
            ]
            for idx, job in enumerate(sanitized_jobs)
        ]

        self._batch_write(self.valid_sheet, start_row, rows, is_valid_sheet=True)
        self._apply_not_applied_colors(start_row, len(rows))
        self._fix_broken_search_links()
        self._ensure_status_dropdowns()
        self._auto_resize_columns(self.valid_sheet, 14)
        return len(jobs)

    def add_discarded_jobs(self, jobs, start_row, start_sr_no):
        if not jobs:
            return 0

        self.ensure_sufficient_rows(self.discarded_entries)

        from aggregator.utils import DataSanitizer

        sanitized_jobs = [DataSanitizer.sanitize_all_fields(job) for job in jobs]

        rows = [
            [
                start_sr_no + idx,
                job.get("reason", "Filtered"),
                self._clean_company(job["company"]),
                job["title"],
                "N/A",
                job["url"],
                job.get("job_id", "N/A"),
                job["job_type"],
                job["location"],
                job["remote"],
                job["entry_date"],
                job["source"],
                job.get("sponsorship", "Unknown"),
            ]
            for idx, job in enumerate(sanitized_jobs)
        ]

        end_row = start_row + len(rows) - 1
        _retry_call(
            self.discarded_entries.update,
            values=rows,
            range_name=f"A{start_row}:M{end_row}",
            value_input_option="USER_ENTERED",
        )
        import time; time.sleep(1)
        self.discarded_entries.format(
            f"A{start_row}:M{end_row}",
            {
                "horizontalAlignment": "CENTER",
                "verticalAlignment": "MIDDLE",
                "textFormat": {"fontFamily": "Times New Roman", "fontSize": 13},
            },
        )
        import time; time.sleep(1)
        # Add clickable hyperlinks for Job URL column (F, index 5)
        url_requests = [
            {
                "updateCells": {
                    "range": {
                        "sheetId": self.discarded_entries.id,
                        "startRowIndex": start_row + idx - 1,
                        "endRowIndex": start_row + idx,
                        "startColumnIndex": 5,
                        "endColumnIndex": 6,
                    },
                    "rows": [
                        {
                            "values": [
                                {
                                    "userEnteredValue": {"stringValue": row[5]},
                                    "textFormatRuns": [
                                        {"startIndex": 0, "format": {"link": {"uri": row[5]}}}
                                    ],
                                }
                            ]
                        }
                    ],
                    "fields": "userEnteredValue,textFormatRuns",
                }
            }
            for idx, row in enumerate(rows)
            if row[5] and row[5].startswith("http")
        ]
        if url_requests:
            for i in range(0, len(url_requests), 100):
                self.spreadsheet.batch_update({"requests": url_requests[i : i + 100]})
                time.sleep(1)

        self._auto_resize_columns(self.discarded_entries, 13)
        return len(jobs)

    def _batch_write(self, sheet, start_row, rows_data, is_valid_sheet):
        if not rows_data:
            return

        # CAPACITY: guaranteed here, immediately before the write, because
        # this is the only place that knows exactly which rows are about to
        # be written. Previously this called ensure_sufficient_rows and
        # swallowed the result, so a failed expand was invisible and the
        # write went into a full sheet - 1410 of 1410, jobs silently lost.
        # On failure we skip: a deferred batch is retried next run.
        if not self.ensure_capacity(sheet, start_row, len(rows_data)):
            import logging as _cl
            _cl.error("Skipping write of %d rows to %s - no capacity",
                      len(rows_data), sheet.title)
            return

        # CIRCUIT BREAKER: Sheets has hard rate limits. After 5 consecutive
        # failures the breaker opens and we stop hammering a 429-ing API for
        # the rest of the run, then retry once after 60s.
        if not _cb_allows():
            import logging as _cl
            _cl.warning(
                "Google Sheets circuit OPEN - skipping write of %d rows "
                "(will retry next run)", len(rows_data)
            )
            return

        end_row = start_row + len(rows_data) - 1
        try:
            _retry_call(
                sheet.update,
                values=rows_data,
                range_name=f"A{start_row}:N{end_row}",
                value_input_option="USER_ENTERED",
            )
            _cb_ok()
        except Exception:
            _cb_fail()
            raise
        time.sleep(1)

        sheet.format(
            f"A{start_row}:N{end_row}",
            {
                "horizontalAlignment": "CENTER",
                "verticalAlignment": "MIDDLE",
                "textFormat": {"fontFamily": "Times New Roman", "fontSize": 13},
            },
        )
        time.sleep(1)

        url_requests = [
            {
                "updateCells": {
                    "range": {
                        "sheetId": sheet.id,
                        "startRowIndex": start_row + idx - 1,
                        "endRowIndex": start_row + idx,
                        "startColumnIndex": 5,
                        "endColumnIndex": 6,
                    },
                    "rows": [
                        {
                            "values": [
                                {
                                    "userEnteredValue": {"stringValue": row[5]},
                                    "textFormatRuns": [
                                        {"startIndex": 0, "format": {"link": {"uri": row[5]}}}
                                    ],
                                }
                            ]
                        }
                    ],
                    "fields": "userEnteredValue,textFormatRuns",
                }
            }
            for idx, row in enumerate(rows_data)
            if row[5] and row[5].startswith("http")
        ]

        if url_requests:
            for i in range(0, len(url_requests), 100):
                self.spreadsheet.batch_update({"requests": url_requests[i : i + 100]})
                time.sleep(1)

        if is_valid_sheet:
            self._add_status_dropdowns(sheet, start_row, len(rows_data))
            # Per-row _add_resume_dropdowns removed: the column-wide call
            # below sets the identical rule in ONE request instead of one
            # per row, so the per-row version was pure API overhead.
            self.ensure_resume_column_validation(sheet)
            self.ensure_status_column_validation(sheet)
            self._apply_status_colors(sheet, start_row, end_row)
            # Clear column B color+validation on buffer rows after last written row
            try:
                self.spreadsheet.batch_update({"requests": [
                    {
                        "repeatCell": {
                            "range": {
                                "sheetId": sheet.id,
                                "startRowIndex": end_row,
                                "endRowIndex": end_row + 500,
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
                                "sheetId": sheet.id,
                                "startRowIndex": end_row,
                                "endRowIndex": end_row + 500,
                                "startColumnIndex": 1,
                                "endColumnIndex": 2,
                            },
                            "rule": None,
                        }
                    }
                ]})
                time.sleep(0.5)
            except Exception as _be:
                pass

    def ensure_resume_column_validation(self, sheet):
        """Set the resume dropdown once across the WHOLE column, not per row.

        Per-row validation only covered rows in the current write batch, so
        every row written by any other path had no dropdown at all and the
        'Tailored' option looked like it kept disappearing. A column-wide
        rule survives every run because it is reapplied to the full range.
        """
        try:
            self.spreadsheet.batch_update({"requests": [{
                "setDataValidation": {
                    "range": {"sheetId": sheet.id, "startRowIndex": 1,
                              "startColumnIndex": 9, "endColumnIndex": 10},
                    "rule": {"condition": {"type": "ONE_OF_LIST",
                             "values": [{"userEnteredValue": "SDE"},
                                        {"userEnteredValue": "ML"},
                                        {"userEnteredValue": "DA"},
                                        {"userEnteredValue": "Tailored"}]},
                             "showCustomUi": True, "strict": False},
                }
            }]})
        except Exception as _rve:
            import logging
            logging.debug(f"resume column validation failed: {_rve}")

    def _add_resume_dropdowns(self, sheet, start_row, num_rows):
        reqs = [{
            "setDataValidation": {
                "range": {"sheetId": sheet.id, "startRowIndex": start_row+i-1, "endRowIndex": start_row+i,
                          "startColumnIndex": 9, "endColumnIndex": 10},
                "rule": {"condition": {"type": "ONE_OF_LIST",
                         "values": [{"userEnteredValue": "SDE"}, {"userEnteredValue": "ML"}, {"userEnteredValue": "DA"}, {"userEnteredValue": "Tailored"}]},
                         "showCustomUi": True, "strict": False},
            }
        } for i in range(num_rows)]
        if reqs:
            for i in range(0, len(reqs), 100):
                self.spreadsheet.batch_update({"requests": reqs[i:i+100]})
                import time; time.sleep(1)

    def ensure_status_column_validation(self, sheet):
        """Set the status dropdown once across the WHOLE column.

        _add_status_dropdowns only covers rows in the current write batch,
        so rows written on earlier runs kept whatever rule they were born
        with. Adding 'Tailor' to STATUS_COLORS therefore only reached new
        rows, and the option looked like it vanished the next day.
        """
        try:
            from aggregator.config import STATUS_COLORS
            self.spreadsheet.batch_update({"requests": [{
                "setDataValidation": {
                    "range": {"sheetId": sheet.id, "startRowIndex": 1,
                              "startColumnIndex": 1, "endColumnIndex": 2},
                    "rule": {"condition": {"type": "ONE_OF_LIST",
                             "values": [{"userEnteredValue": st}
                                        for st in STATUS_COLORS.keys()]},
                             "showCustomUi": True, "strict": False},
                }
            }]})
        except Exception as _sve:
            import logging
            logging.debug(f"status column validation failed: {_sve}")

    def _add_status_dropdowns(self, sheet, start_row, num_rows):
        requests = [
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
                                for status in STATUS_COLORS.keys()
                            ],
                        },
                        "showCustomUi": True,
                        "strict": False,
                    },
                }
            }
            for idx in range(num_rows)
        ]

        if requests:
            for i in range(0, len(requests), 100):
                self.spreadsheet.batch_update({"requests": requests[i : i + 100]})
                time.sleep(1)

    def _apply_status_colors(self, sheet, start_row, end_row):
        """Color ALL status cells in the sheet, not just new rows.
        Also clears color on empty rows."""
        try:
            all_data = sheet.get_all_values()
            color_requests = []

            # Color ALL rows from row 2 to last data row
            for row_idx in range(1, len(all_data)):
                if len(all_data[row_idx]) < 2:
                    continue

                status = all_data[row_idx][1].strip()
                color = STATUS_COLORS.get(status)

                # If row is empty (no company in col C), clear the color
                company = all_data[row_idx][2].strip() if len(all_data[row_idx]) > 2 else ""
                if not company and not status:
                    color_requests.append({
                        "repeatCell": {
                            "range": {
                                "sheetId": sheet.id,
                                "startRowIndex": row_idx,
                                "endRowIndex": row_idx + 1,
                                "startColumnIndex": 1,
                                "endColumnIndex": 2,
                            },
                            "cell": {"userEnteredFormat": {
                                "backgroundColor": {"red": 1.0, "green": 1.0, "blue": 1.0},
                            }},
                            "fields": "userEnteredFormat.backgroundColor",
                        }
                    })
                    continue

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

            if color_requests:
                for i in range(0, len(color_requests), 100):
                    self.spreadsheet.batch_update(
                        {"requests": color_requests[i : i + 100]}
                    )
                    time.sleep(1)

        except Exception as e:
            print(f"Color application error: {e}")

    def _auto_resize_columns(self, sheet, total_columns):
        try:
            all_data = sheet.get_all_values()
            if len(all_data) < 2:
                return

            column_limits = {
                0: 80,
                1: 400,
                2: 230,   # Company  -5 chars
                3: 300,   # Title    -10 chars
                4: 134,   # Date Applied -5 +3
                5: 91,    # Job URL  -3
                6: 120,
                7: 96,    # Job Type -3
                8: 196,   # Location -3
                9: 96,    # Resume +2
                10: 120,  # Remote   -7 -3
                11: 126,  # Entry Date -5 -3
                12: 130,
                13: 126,  # Sponsorship -3
            }

            widths = []
            for col_idx in range(total_columns):
                max_width = 50

                if len(all_data[0]) > col_idx:
                    max_width = max(max_width, len(str(all_data[0][col_idx])) * 10 + 40)

                for row in all_data[1:]:
                    if len(row) > col_idx and row[col_idx]:
                        max_width = max(
                            max_width, len(str(row[col_idx]).strip()) * 8 + 25
                        )

                if col_idx in column_limits:
                    max_width = (
                        max(95, min(max_width, column_limits[col_idx]))
                        if col_idx == 5
                        else min(max_width, column_limits[col_idx])
                    )

                widths.append(int(max_width))

            requests = [
                {
                    "updateDimensionProperties": {
                        "range": {
                            "sheetId": sheet.id,
                            "dimension": "COLUMNS",
                            "startIndex": col_idx,
                            "endIndex": col_idx + 1,
                        },
                        "properties": {"pixelSize": width},
                        "fields": "pixelSize",
                    }
                }
                for col_idx, width in enumerate(widths)
            ]

            for i in range(0, len(requests), 100):
                self.spreadsheet.batch_update({"requests": requests[i : i + 100]})
                time.sleep(1)

        except Exception as e:
            print(f"Column resize error: {e}")

    def _format_headers(self, sheet, num_cols):
        try:
            col_letter = chr(ord("A") + num_cols - 1)
            sheet.format(
                f"A1:{col_letter}1",
                {
                    "horizontalAlignment": "CENTER",
                    "verticalAlignment": "MIDDLE",
                    "textFormat": {
                        "fontFamily": "Times New Roman",
                        "fontSize": 14,
                        "bold": True,
                    },
                    "backgroundColor": {"red": 0.7, "green": 0.9, "blue": 0.7},
                },
            )
        except Exception as _e:
            import logging as _lg  # was bare `logging`, undefined here
            _lg.debug("header format suppressed: %s", _e)


    @staticmethod
    def _clean_job_id(job_id):
        """Clean garbage job IDs like A66668Apply, N/A etc."""
        if not job_id or job_id in ("N/A", ""):
            return "N/A"
        import re
        # Reject IDs that contain non-ID text
        if re.search(r'(?:Apply|Submit|Click|Here|Login)', job_id, re.I):
            return "N/A"
        return job_id

    @staticmethod
    def _smart_url(job):
        """Convert URL_CONFLICT/URL_SHIFTED to clickable Google Search link."""
        url = job.get("url", "")
        if url in ("URL_CONFLICT", "URL_SHIFTED"):
            company = job.get("company", "")
            title = job.get("title", "")
            search_query = f"{company} {title} careers apply"
            # Return raw Google search URL (auto-links in Sheets)
            import urllib.parse
            encoded = urllib.parse.quote(search_query)
            return f"https://www.google.com/search?q={encoded}"
        return url

    @staticmethod
    def _clean_company(company):
        """Clean and normalize company names."""
        if not company:
            return "Unknown"
        c = company.strip()

        # Known acronym companies (should be ALL CAPS)
        _ACRONYMS = {
            "cmt": "CMT", "abb": "ABB", "bmo": "BMO", "ibm": "IBM",
            "sap": "SAP", "hpe": "HPE", "rtx": "RTX", "nxp": "NXP",
            "kbr": "KBR", "dxc": "DXC", "sgs": "SGS", "uhg": "UHG",
            "ey": "EY", "pwc": "PwC", "kpmg": "KPMG", "att": "AT&T",
            "ge": "GE", "gm": "GM", "hp": "HP", "lg": "LG",
            "lmi": "LMI", "ias": "IAS", "mhi": "MHI", "idex": "IDEX",
            "caci": "CACI", "aecom": "AECOM", "nvidia": "NVIDIA",
            "amd": "AMD", "tsmc": "TSMC", "asml": "ASML",
        }
        if c.lower() in _ACRONYMS:
            return _ACRONYMS[c.lower()]

        # Strip markdown bold markers
        import re as _co_re
        c = _co_re.sub(r'\*\*', '', c).strip()

        # Check COMPANY_NAME_FIXES map first
        try:
            from aggregator.config import COMPANY_NAME_FIXES
            _fixed = COMPANY_NAME_FIXES.get(c.lower().strip())
            if _fixed and _fixed != 'Unknown':
                return _fixed
        except ImportError:
            pass

        # Fix Greenhouse/Lever slugs: "premierautomation" → "Premier Automation"
        # If company is all lowercase and > 10 chars, probably a slug
        if c.islower() and len(c) > 8:
            # Try splitting on common word boundaries
            # camelCase or concatenated: "energyhub" → "Energy Hub"
            spaced = _co_re.sub(r'([a-z])([A-Z])', r'\1 \2', c)
            if spaced == c:
                # Still no spaces — just title case it
                c = c.replace('-', ' ').replace('_', ' ').title()
            else:
                c = spaced.title()

        return c

    @staticmethod
    def _clean_location(location):
        """Clean garbage locations before writing to sheet."""
        if not location:
            return "Unknown"
        loc = location.strip()

        # ── Smart Location Corrector ──
        # Static shortcuts (abbreviations)
        # Strip HTML tags from location
        import re as _loc_re
        loc = _loc_re.sub(r'<[^>]+>', ' ', loc).strip()
        loc = _loc_re.sub(r'\s+', ' ', loc).strip()

        # If location has multiple cities separated by space (from HTML), take the first
        if ' Remote' in loc and ',' in loc:
            loc = loc.split(' Remote')[0].strip()

        # Garbage location strings from page parsing
        _GARBAGE_LOCS = {"Assistance To Interns", "Business, Economics", "And Role",
            "and role", "N/A", "Unknown", ""}
        # Fix "US, Remote" → "Remote"
        if loc.startswith("US,") or loc.startswith("USA,"):
            loc = "Remote"
        # Fix lone state prefix: "CA New York" → "New York, NY"
        _lone_state = _loc_re.match(r'^[A-Z]{2}\s+(.+)$', loc)
        if _lone_state and ',' not in loc:
            loc = _lone_state.group(1)
        if loc in _GARBAGE_LOCS:
            return "Unknown"

        # Strip country name prefix concatenated with city (e.g. "CanadaSanta Clara, CA")
        import re as _cp_re
        _country_prefixes = ["Canada", "United States", "USA", "US", "BGR"]
        for _cp in _country_prefixes:
            if loc.startswith(_cp) and len(loc) > len(_cp) and loc[len(_cp):][0].isupper():
                loc = loc[len(_cp):].strip()
                break

        # Detect non-geographic text (programming languages, names, UI text)
        _NON_GEO_EXACT = {"python", "rust", "java", "javascript", "golang", "ruby",
            "react", "node", "sql", "html", "css", "docker", "kubernetes",
            "colin", "devine", "smith", "opportunity", "select", "click",
            "apply", "upload", "resume", "often"}
        _loc_words = set(w.strip().lower() for w in _loc_re.split(r"[,/\s]+", loc) if w.strip())
        if _loc_words & _NON_GEO_EXACT:
            return "Unknown"
        # Reject long locations with no US state code (e.g. "North America Latin America Europe")
        if len(loc) > 30 and ", " not in loc and "remote" not in loc.lower():
            return "Unknown"

        # Fix state code garbage: "Seattle, WASF" → "Seattle, WA", "CTSt Paul, MN" → "St Paul, MN"
        _state_fix = _loc_re.match(r'^(.+),\s*([A-Z]{2})[A-Z]+$', loc)
        if _state_fix:
            loc = f"{_state_fix.group(1)}, {_state_fix.group(2)}"
        # Fix prefix garbage: "CTSt Paul, MN" → "St. Paul, MN"
        _prefix_fix = _loc_re.match(r'^[A-Z]{2,3}([A-Z][a-z].+)$', loc)
        if _prefix_fix:
            loc = _prefix_fix.group(1)

        _ABBREV = {
            "NYC": "New York, NY", "York, NY": "New York, NY", "SF": "San Francisco, CA",
            "LA": "Los Angeles, CA", "NY, NY": "New York, NY",
            "DC": "Washington, DC", "ATL": "Atlanta, GA",
            "BOS": "Boston, MA", "CHI": "Chicago, IL",
        }
        if loc in _ABBREV:
            return _ABBREV[loc]

        # Known US cities for fuzzy matching (top 500 tech hub cities)
        _KNOWN_CITIES = [
            "Farmington Hills, MI", "Foster City, CA", "Mountain View, CA",
            "San Francisco, CA", "San Jose, CA", "Sunnyvale, CA",
            "Santa Clara, CA", "Palo Alto, CA", "Menlo Park, CA",
            "Redwood City, CA", "San Mateo, CA", "Burlingame, CA",
            "San Carlos, CA", "Berkeley, CA", "Oakland, CA",
            "Cupertino, CA", "Milpitas, CA", "Fremont, CA",
            "Irvine, CA", "Los Angeles, CA", "San Diego, CA",
            "Seattle, WA", "Bellevue, WA", "Redmond, WA",
            "New York, NY", "Brooklyn, NY", "Manhattan, NY",
            "Boston, MA", "Cambridge, MA", "Somerville, MA",
            "Waltham, MA", "Burlington, MA", "Framingham, MA",
            "Chicago, IL", "Austin, TX", "Dallas, TX", "Houston, TX",
            "Denver, CO", "Boulder, CO", "Atlanta, GA", "Raleigh, NC",
            "Charlotte, NC", "Durham, NC", "Pittsburgh, PA",
            "Philadelphia, PA", "Washington, DC", "Arlington, VA",
            "Reston, VA", "McLean, VA", "Portland, OR", "Phoenix, AZ",
            "Scottsdale, AZ", "Salt Lake City, UT", "Minneapolis, MN",
            "Detroit, MI", "Ann Arbor, MI", "Columbus, OH",
            "Indianapolis, IN", "Nashville, TN", "Miami, FL",
            "Tampa, FL", "Orlando, FL", "Jacksonville, FL",
            "Longwood, FL", "Altamonte Springs, FL",
            "St. Louis, MO", "Kansas City, MO", "Milwaukee, WI",
            "La Crosse, WI", "Madison, WI", "Boise, ID",
            "Richmond, VA", "Baltimore, MD", "Springfield, IL",
            "Celebration, FL", "Long Beach, CA", "Plymouth, MI",
            "Grand Rapids, MI", "El Segundo, CA", "Hawthorne, CA",
            "Monroeville, PA", "Alpharetta, GA", "Suwanee, GA",
        ]

        # Fuzzy match: find closest city using Levenshtein distance
        def _edit_dist(a, b):
            if len(a) < len(b): return _edit_dist(b, a)
            if len(b) == 0: return len(a)
            prev = list(range(len(b) + 1))
            for i, ca in enumerate(a):
                curr = [i + 1]
                for j, cb in enumerate(b):
                    curr.append(min(prev[j+1]+1, curr[j]+1, prev[j]+(ca!=cb)))
                prev = curr
            return prev[len(b)]

        # Only fuzzy match if location has a comma (city, state format)
        if "," in loc and len(loc) > 4:
            city_part = loc.split(",")[0].strip()
            state_part = loc.split(",")[1].strip()[:2].upper() if "," in loc else ""
            best_match = None
            best_dist = 999

            for known in _KNOWN_CITIES:
                known_city = known.split(",")[0].strip()
                known_state = known.split(",")[1].strip()[:2].upper() if "," in known else ""

                # State must match if both have states
                if state_part and known_state and state_part != known_state:
                    continue

                dist = _edit_dist(city_part.lower(), known_city.lower())
                # Allow up to 3 char edits for cities > 5 chars
                max_dist = 3 if len(city_part) > 5 else 2 if len(city_part) > 3 else 1
                if dist <= max_dist and dist < best_dist:
                    best_dist = dist
                    best_match = known

            if best_match and best_dist > 0:
                return best_match
        # Garbage patterns
        garbage = ["And Role", "and role", "Unknown", "", "N/A", "Business, Economics", "Business Economics"]
        if loc in garbage:
            return "Unknown"
        # Separate check for USA variants
        if loc.lower() in ["in usa", "in us", "usa", "us", "united states"]:
            return "Remote"
        # Fix leading comma: ", MA" → "MA"
        if loc.startswith(","):
            loc = loc.lstrip(", ").strip()
            if len(loc) == 2 and loc.isalpha():
                return loc.upper()
            return loc if loc else "Unknown"
        # Fix "in USA" variants
        if loc.lower() in ["in usa", "in us", "usa", "us", "united states"]:
            return "Remote"
        return loc

    @staticmethod
    def _classify_resume(title):
        t = title.lower() if title else ''
        # ML checked FIRST — strong signals override DA
        ml_strong = [
            'machine learning', 'deep learning', 'reinforcement learning',
            'computer vision', 'natural language', 'generative ai', 'gen ai',
            'genai', 'large language', 'llm', 'foundation model',
            'agentic', 'ai agent', 'autonomous ai', 'multimodal',
            'diffusion model', 'nlp', 'neural', '/ml', 'ml/', 'cv/ml', 'ml/dl', 'ai/ml',
            'ai engineer', 'ai engineering', 'ai intern', 'ai research',
            'applied ai', 'ai application', 'ai automation', 'ai operations',
            'artificial intelligence',
            'ai platform', 'ai retail', 'ai strategy', 'ai/data',
            'research scientist', 'perception', 'robotics ai',
        ]
        if any(kw in t for kw in ml_strong):
            return 'ML'
        if any(kw in t for kw in [' ml ', 'ml ', ' ai ', 'ai ']):
            return 'ML'
        # DA
        da_kws = [
            'data engineer', 'data analyst', 'data science', 'data scientist',
            'business intelligence', ' bi ', 'bi ', 'data anal',
            'data management', 'analytics intern', 'data intern',
            'etl', 'data pipeline', 'data warehouse', 'power bi',
            'tableau', 'reporting analyst', 'database engineer',
            'data visualization', 'data operations', 'data governance',
            'data quality', 'data steward',
        ]
        if any(kw in t for kw in da_kws):
            return 'DA'
        return 'SDE'
    @staticmethod
    @lru_cache(maxsize=2048)
    def _normalize(text):
        return re.sub(r"[^a-z0-9]", "", text.lower()) if text else ""

    @staticmethod
    @lru_cache(maxsize=2048)
    def _clean_url(url):
        """Delegate to URLCleaner - ONE definition of "same URL".

        This used to be a second, subtly different implementation: it did NOT
        strip "www.", while URLCleaner does. The sheet was seeded with
        "www.google.com/search" and dedup looked up "google.com/search", so
        those URLs could never match and the same job was rewritten every run.
        Two implementations of one concept, silently disagreeing - the same
        root cause as the shadowed constants.
        """
        if not url:
            return ""
        try:
            from aggregator.utils import URLCleaner
            return URLCleaner.clean_url(url)
        except Exception:
            if "jobright.ai/jobs/info/" in url.lower():
                match = re.search(r"(jobright\.ai/jobs/info/[a-f0-9]+)", url, re.I)
                if match:
                    return match.group(1).lower()
            return re.sub(r"[?#].*$", "", url).lower().rstrip("/")
