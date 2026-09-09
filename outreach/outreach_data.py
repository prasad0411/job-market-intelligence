import re
#!/usr/bin/env python3
"""Outreach Pipeline — Data Layer (Sheets, Credits, NameParser, PatternCache)."""
from outreach.brain import Brain
from aggregator.atomic_json import write_json as _atomic_write_json

import os, re, json, time, datetime, logging, unicodedata
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from outreach.outreach_config import (
    SPREADSHEET,
    VALID_TAB,
    OUTREACH_TAB,
    SHEETS_CREDS,
    O_HEADERS,
    C,
    V_COMPANY,
    V_TITLE,
    V_JOBID,
    V_LOCATION,
    V_RESUME,
    SHEET_PAUSE,
    CREDITS_FILE,
    APIS,
    MAX_DAILY,
    PATTERNS_FILE,
    PAT_A,
    PAT_B,
    PAT_C,
    STRIP_PRE,
    STRIP_SUF,
    STATE_TO_TIMEZONE,
    TZ_DISPLAY,
    SEND_HOUR,
    HM_LI_MSG_TEMPLATE,
    REC_LI_MSG_TEMPLATE,
    LI_MSG_MAX,
)

log = logging.getLogger(__name__)


def is_suspicious_email(email):
    """Check if email domain is an ATS/internal platform — not a real company email."""
    if not email or "@" not in email:
        return True
    try:
        from outreach.outreach_config import SUSPICIOUS_EMAIL_DOMAINS
    except ImportError:
        return False
    domain = email.split("@")[1].lower().strip()
    # Check if domain ends with any suspicious pattern
    for sus in SUSPICIOUS_EMAIL_DOMAINS:
        if domain.endswith(sus):
            return True
    # Check if domain has 3+ subdomains (likely internal routing)
    if domain.count(".") >= 3:
        return True
    return False


class Sheets:
    _resume_cache = None
    _location_cache = None

    def __init__(self):
        scope = [
            "https://spreadsheets.google.com/feeds",
            "https://www.googleapis.com/auth/drive",
        ]
        creds = ServiceAccountCredentials.from_json_keyfile_name(SHEETS_CREDS, scope)
        self.ss = gspread.authorize(creds).open(SPREADSHEET)
        self._ensure()

    def _ensure(self):
        try:
            self.ws = self.ss.worksheet(OUTREACH_TAB)
        except gspread.exceptions.WorksheetNotFound:
            self.ws = self.ss.add_worksheet(OUTREACH_TAB, rows=500, cols=len(O_HEADERS))
            log.info(f"Created '{OUTREACH_TAB}'")

        row1 = self.ws.row_values(1)
        if not row1 or row1[0] != O_HEADERS[0]:
            self._retry(
                self.ws.update,
                values=[O_HEADERS],
                range_name=f"A1:{_cl(len(O_HEADERS)-1)}1",
                value_input_option="RAW",
            )

            try:
                end = _cl(len(O_HEADERS) - 1)
                self.ws.format(
                    f"A1:{end}1",
                    {
                        "horizontalAlignment": "CENTER",
                        "verticalAlignment": "MIDDLE",
                        "textFormat": {
                            "fontFamily": "Times New Roman",
                            "fontSize": 13,
                            "bold": True,
                        },
                        "backgroundColor": {
                            "red": 0.698,
                            "green": 0.898,
                            "blue": 0.698,
                        },
                    },
                )
            except Exception as _e:
                log.debug(f"sheets op failed: {_e}")

            try:
                end = _cl(len(O_HEADERS) - 1)
                self.ws.format(
                    f"A2:{end}2000",
                    {
                        "horizontalAlignment": "CENTER",
                        "verticalAlignment": "MIDDLE",
                        "textFormat": {"fontFamily": "Times New Roman", "fontSize": 13},
                    },
                )
            except Exception as _e:
                log.debug(f"sheets op failed: {_e}")

            try:
                self.ss.batch_update(
                    {
                        "requests": [
                            {
                                "setDataValidation": {
                                    "range": {
                                        "sheetId": self.ws.id,
                                        "startRowIndex": 1,
                                        "endRowIndex": 2000,
                                        "startColumnIndex": 0,
                                        "endColumnIndex": len(O_HEADERS),
                                    },
                                    "rule": None,  # clear all first
                                }
                            },
                
                        ]
                    }
                )
            except Exception as _e:
                log.debug(f"sheets op failed: {_e}")

            try:
                widths = []
                sizes = {
                    0: 60,
                    1: 200,
                    2: 250,
                    3: 100,
                    4: 140,
                    5: 150,
                    6: 180,
                    7: 140,
                    8: 150,
                    9: 180,
                    10: 200,
                    11: 140,
                    12: 200,
                }
                for i in range(len(O_HEADERS)):
                    widths.append(
                        {
                            "updateDimensionProperties": {
                                "range": {
                                    "sheetId": self.ws.id,
                                    "dimension": "COLUMNS",
                                    "startIndex": i,
                                    "endIndex": i + 1,
                                },
                                "properties": {"pixelSize": sizes.get(i, 150)},
                                "fields": "pixelSize",
                            }
                        }
                    )
                self.ss.batch_update({"requests": widths})
            except Exception as _e:
                log.debug(f"sheets op failed: {_e}")

            try:
                self.ss.batch_update(
                    {
                        "requests": [
                            {
                                "updateSheetProperties": {
                                    "properties": {
                                        "sheetId": self.ws.id,
                                        "gridProperties": {"frozenRowCount": 1},
                                    },
                                    "fields": "gridProperties.frozenRowCount",
                                }
                            }
                        ]
                    }
                )
            except Exception as _e:
                log.debug(f"sheets op failed: {_e}")

            self._p()
        # Always apply body formatting (runs every session, not just creation)
        # Skip hm_li (col 5) and rec_li (col 8) to preserve hyperlink formatting
        try:
            li_cols = {C["hm_li"], C["rec_li"]}  # columns to skip (0-indexed)
            fmt = {
                "horizontalAlignment": "CENTER",
                "verticalAlignment": "MIDDLE",
                "textFormat": {"fontFamily": "Times New Roman", "fontSize": 13},
            }
            # Build contiguous column ranges excluding LinkedIn URL columns
            n_cols = len(O_HEADERS)
            range_start = None
            fmt_requests = []
            for i in range(n_cols):
                if i not in li_cols:
                    if range_start is None:
                        range_start = i
                else:
                    if range_start is not None:
                        fmt_requests.append({
                            "repeatCell": {
                                "range": {
                                    "sheetId": self.ws.id,
                                    "startRowIndex": 1,
                                    "endRowIndex": 2000,
                                    "startColumnIndex": range_start,
                                    "endColumnIndex": i,
                                },
                                "cell": {"userEnteredFormat": {
                                    "horizontalAlignment": "CENTER",
                                    "verticalAlignment": "MIDDLE",
                                    "textFormat": {"fontFamily": "Times New Roman", "fontSize": 13},
                                }},
                                "fields": "userEnteredFormat(horizontalAlignment,verticalAlignment,textFormat)",
                            }
                        })
                        range_start = None
            if range_start is not None:
                fmt_requests.append({
                    "repeatCell": {
                        "range": {
                            "sheetId": self.ws.id,
                            "startRowIndex": 1,
                            "endRowIndex": 2000,
                            "startColumnIndex": range_start,
                            "endColumnIndex": n_cols,
                        },
                        "cell": {"userEnteredFormat": {
                            "horizontalAlignment": "CENTER",
                            "verticalAlignment": "MIDDLE",
                            "textFormat": {"fontFamily": "Times New Roman", "fontSize": 13},
                        }},
                        "fields": "userEnteredFormat(horizontalAlignment,verticalAlignment,textFormat)",
                    }
                })
            if fmt_requests:
                self.ss.batch_update({"requests": fmt_requests})
            self._p()
        except Exception as _e:
            log.debug(f"op failed: {_e}")

    def pull(self):
        """Full positional sync: mirror Valid sheet order, copy Company/Title/JobID verbatim."""
        try:
            valid = self.ss.worksheet(VALID_TAB)
        except gspread.exceptions.WorksheetNotFound:
            log.error(f"'{VALID_TAB}' not found")
            return 0

        vdata = valid.get_all_values()
        self._p()
        odata = self.ws.get_all_values()
        self._p()

        # Build outreach lookup: key → row data (preserve HM/Rec/emails/notes)
        outreach_by_key = {}
        for r in odata[1:]:
            r = _pad(r)
            co = r[C["company"]].strip().lower()
            ti = r[C["title"]].strip().lower()
            jid = r[C["job_id"]].strip().lower()
            # Key by job_id first, then company+title
            if jid and jid != "n/a":
                outreach_by_key[("jid", jid)] = list(r)
            if co:
                outreach_by_key[("co_ti", f"{co}||{ti}")] = list(r)

        # Build new outreach rows in Valid sheet order
        result_rows = []
        seen = set()
        for row in vdata[1:]:
            while len(row) <= max(V_COMPANY, V_TITLE, V_JOBID):
                row = list(row) + [""]
            co = row[V_COMPANY].strip()
            ti = row[V_TITLE].strip() if len(row) > V_TITLE else ""
            jid = row[V_JOBID].strip() if len(row) > V_JOBID else ""
            spon = row[13].strip() if len(row) > 13 else ""
            if not co:
                continue
            jid_clean = jid.lower() if jid and jid.lower() != "n/a" else ""

            # Dedup within this sync
            dedup_key = f"{co.lower()}||{ti.lower()}||{jid_clean}"
            if dedup_key in seen:
                continue
            seen.add(dedup_key)
            # Get status and location from Valid Entries
            _v_status = row[1].strip() if len(row) > 1 else ""
            _v_location = row[8].strip() if len(row) > 8 else ""
            _v_role = (row[9].strip().upper() if len(row) > 9 else "")
            def _clean_city(loc):
                if not loc or loc.strip().lower() in ("unknown", "n/a", ""):
                    return ""
                # split on comma OR mangled " - " ; take first part (the city)
                c = re.split(r"\s*[,\-]\s*", loc.strip())[0].strip()
                # drop if it's just a 2-letter state code
                return "" if len(c) <= 2 else c
            _hm_term = {"ML": "machine learning manager", "DA": "analytics manager"}.get(_v_role, "engineering manager")
            # FILTER: Only include jobs that are Applied or previously tracked
            _has_existing = False
            if jid_clean:
                _has_existing = ("jid", jid_clean) in outreach_by_key
            if not _has_existing:
                _has_existing = ("co_ti", f"{co.lower()}||{ti.lower()}") in outreach_by_key
            # Not Applied + never tracked = skip entirely
            if _v_status.lower() == "not applied":
                if not _has_existing:
                    continue  # Never entered outreach
                else:
                    continue  # Was Applied, now reverted to Not Applied — remove
            if not _v_status or _v_status.lower() in ("", "not applied"):
                if not _has_existing:
                    continue

            # Find existing outreach row
            existing = None
            if jid_clean:
                existing = outreach_by_key.get(("jid", jid_clean))
            if not existing:
                existing = outreach_by_key.get(("co_ti", f"{co.lower()}||{ti.lower()}"))

            if existing:
                # Preserve existing outreach data but update Company/Title/JobID verbatim from Valid
                nr = list(existing)
                while len(nr) < len(O_HEADERS):
                    nr.append("")
                nr[C["company"]] = co  # verbatim from Valid
                nr[C["title"]] = ti    # verbatim from Valid
                nr[C["job_id"]] = jid  # verbatim from Valid
                # If status just changed to Applied, upgrade extract + add search links
                if _v_status.lower() == "applied" and nr[C["extract"]].strip().lower() != "yes":
                    nr[C["extract"]] = "yes"
                    import urllib.parse as _up
                    _loc_clean = _v_location.replace(",", "").strip() if _v_location else ""
                    if not nr[C["hm_li"]] or nr[C["hm_li"]].startswith("https://www.google.com"):
                        _city = _clean_city(_v_location)
                        _kw = f"{co} {_hm_term} {_city}".strip()
                        _geo = "&geoUrn=%5B%22103644278%22%5D"
                        nr[C["hm_li"]] = f"https://www.linkedin.com/search/results/people/?keywords={_up.quote_plus(_kw)}{_geo}"
                    if not nr[C["rec_li"]] or nr[C["rec_li"]].startswith("https://www.google.com"):
                        _city = _clean_city(_v_location)
                        _kw = f"{co} recruiter {_city}".strip()
                        _geo = "&geoUrn=%5B%22103644278%22%5D"
                        nr[C["rec_li"]] = f"https://www.linkedin.com/search/results/people/?keywords={_up.quote_plus(_kw)}{_geo}"
                # NEVER touch LinkedIn URL columns (F, J) — user enters these manually
                # Explicitly preserve LinkedIn URLs from existing row
                for li_col in [C["hm_li"], C["rec_li"]]:
                    if len(existing) > li_col and existing[li_col].strip():
                        nr[li_col] = existing[li_col]
            else:
                # New row — only copy shared columns
                nr = [""] * len(O_HEADERS)
                nr[C["company"]] = co
                nr[C["title"]] = ti
                nr[C["job_id"]] = jid
                # Skip non-Applied jobs entirely (they won't reach here after filter)
                nr[C["extract"]] = "Skip"

                # ── Smart Extract=yes + contact auto-fill ──────────────────────
                try:
                    import re as _re
                    _b = Brain.get()
                    _co_key = _re.sub(r"[^a-z0-9]", "", co.lower().strip())
                    _extract_yes = False
                    # ONLY extract for Applied jobs
                    _status = ""
                    try:
                        for _vr in vdata[1:]:
                            if len(_vr) > 2:
                                _vr_key = _re.sub(r"[^a-z0-9]", "", _vr[2].strip().lower())
                                if _vr_key == _co_key:
                                    _status = _vr[1].strip().lower() if len(_vr) > 1 else ""
                                    break
                    except Exception:
                        pass
                    if _status == "applied":
                        _extract_yes = True
                    if _extract_yes:
                        nr[C["extract"]] = "yes"
                        # Generate Google search links for HM and Recruiter
                        import urllib.parse as _up
                        _loc_clean = _v_location.replace(",", "").strip() if _v_location else ""
                        if not nr[C["hm_li"]]:
                            _city2 = _clean_city(_v_location)
                            _kw2 = f"{co} {_hm_term} {_city2}".strip()
                            nr[C["hm_li"]] = f"https://www.linkedin.com/search/results/people/?keywords={_up.quote_plus(_kw2)}&geoUrn=%5B%22103644278%22%5D"
                        if not nr[C["rec_li"]]:
                            _city3 = _clean_city(_v_location)
                            _kw3 = f"{co} recruiter {_city3}".strip()
                            nr[C["rec_li"]] = f"https://www.linkedin.com/search/results/people/?keywords={_up.quote_plus(_kw3)}&geoUrn=%5B%22103644278%22%5D"

                    # AUTO-FILL: exact role match first
                    for _role, _col_name, _col_email in [
                        ("hm", "hm_name", "hm_email"),
                        ("rec", "rec_name", "rec_email"),
                    ]:
                        _contact = _b.get_verified_contact(co, _role)
                        if _contact and _contact.get("email"):
                            nr[C[_col_name]] = _contact.get("name", "")
                            nr[C[_col_email]] = _contact["email"]
                            if _contact.get("linkedin"):
                                nr[C["hm_li" if _role == "hm" else "rec_li"]] = _contact["linkedin"]
                            log.info(f"pull: auto-filled {_role} for {co}: {_contact['email']}")

                    # AUTO-FILL: fallback — any non-bounced contact for this company by name
                    # _co_contacts was never built in this function, so this
                    # whole fallback raised NameError and never ran. Built the
                    # same way as line ~603: company_contacts keyed on the
                    # normalised company name.
                    _co_contacts = {}
                    try:
                        import re as _cc_re
                        _cc_key = _cc_re.sub(r"[^a-z0-9]", "", str(co).lower().strip())
                        _co_contacts = (_b._data.get("company_contacts", {})
                                        or {}).get(_cc_key, {}) or {}
                    except Exception as _cce:
                        log.debug(f"company_contacts lookup failed: {_cce}")
                    if not nr[C["hm_email"]] and not nr[C["rec_email"]] and _co_contacts:
                        for _rk, _cd in _co_contacts.items():
                            if _cd.get("email") and not _cd.get("bounced"):
                                _cn = "hm_name" if _rk == "hm" else "rec_name"
                                _ce = "hm_email" if _rk == "hm" else "rec_email"
                                nr[C[_cn]] = _cd.get("name", "")
                                nr[C[_ce]] = _cd["email"]
                                log.info(f"pull: company-cache fill for {co}: {_cd['email']}")
                                break

                except Exception as _bce:
                    log.debug(f"pull: smart fill failed for {co}: {_bce}")
            result_rows.append(nr)

        # Assign sequential Sr. No.
        for i, nr in enumerate(result_rows):
            nr[C["sr"]] = str(i + 1)

        # Count changes
        old_count = len(odata) - 1 if len(odata) > 1 else 0
        new_count = len(result_rows)
        added = max(0, new_count - old_count)

        # Write entire outreach sheet (header + all rows) in one batch
        if result_rows:
            all_data = [O_HEADERS] + result_rows
            end_col = _cl(len(O_HEADERS) - 1)

            # Clear existing data below what we'll write (handles deletions)
            total_existing = len(odata)
            total_new = len(all_data)
            if total_existing > total_new:
                # Clear orphaned rows
                clear_start = total_new + 1
                clear_end = total_existing
                try:
                    clear_range = f"A{clear_start}:{end_col}{clear_end}"
                    self._retry(
                        self.ws.batch_clear, [clear_range]
                    )
                    self._p()
                except Exception as _e:
                    pass  # suppressed: use log.debug(_e) to investigate

            # Write all data
            self._retry(
                self.ws.update,
                values=all_data,
                range_name=f"A1:{end_col}{len(all_data)}",
                value_input_option="USER_ENTERED",
            )
            self._p()

            # Format data rows
            if new_count > 0:
                try:
                    self.ws.format(
                        f"A2:{end_col}{new_count + 1}",
                        {
                            "horizontalAlignment": "CENTER",
                            "verticalAlignment": "MIDDLE",
                            "textFormat": {"fontFamily": "Times New Roman", "fontSize": 13},
                        },
                    )
                except Exception as _e:
                    pass  # suppressed: use log.debug(_e) to investigate

            # Re-apply hyperlinks for LinkedIn URL columns (batch write destroys them)
            li_requests = []
            for row_idx, nr in enumerate(result_rows, start=2):
                for li_col in [C["hm_li"], C["rec_li"]]:
                    url_val = nr[li_col].strip() if len(nr) > li_col else ""
                    if url_val and url_val.startswith("http"):
                        li_requests.append({
                            "updateCells": {
                                "range": {
                                    "sheetId": self.ws.id,
                                    "startRowIndex": row_idx - 1,
                                    "endRowIndex": row_idx,
                                    "startColumnIndex": li_col,
                                    "endColumnIndex": li_col + 1,
                                },
                                "rows": [{"values": [{
                                    "userEnteredValue": {"stringValue": url_val},
                                    "textFormatRuns": [{"startIndex": 0, "format": {"link": {"uri": url_val}}}],
                                }]}],
                                "fields": "userEnteredValue,textFormatRuns",
                            }
                        })
            if li_requests:
                try:
                    for chunk_i in range(0, len(li_requests), 100):
                        self.ss.batch_update({"requests": li_requests[chunk_i:chunk_i+100]})
                        self._p()
                except Exception as _le:
                    log.debug(f"LinkedIn hyperlink restore failed: {_le}")

            # Apply Extract dropdown ONLY to populated rows (not empty rows)
            if new_count > 0:
                try:
                    extract_col = C["extract"]
                    extract_requests = [{
                        "setDataValidation": {
                            "range": {
                                "sheetId": self.ws.id,
                                "startRowIndex": row_i,  # 0-indexed
                                "endRowIndex": row_i + 1,
                                "startColumnIndex": extract_col,
                                "endColumnIndex": extract_col + 1,
                            },
                            "rule": {
                                "condition": {
                                    "type": "ONE_OF_LIST",
                                    "values": [
                                        {"userEnteredValue": "yes"},
                                        {"userEnteredValue": "Skip"},
                                    ],
                                },
                                "showCustomUi": True,
                                "strict": False,
                            },
                        }
                    } for row_i in range(1, new_count + 1)]  # rows 2..N (0-indexed 1..N)
                    for chunk_i in range(0, len(extract_requests), 100):
                        self.ss.batch_update({"requests": extract_requests[chunk_i:chunk_i+100]})
                        self._p()
                except Exception as _dv:
                    log.debug(f"Extract dropdown failed: {_dv}")

            removed = max(0, old_count - new_count)
            if added > 0:
                print(f"  Sync: {added} new rows added from Valid Entries")
            if removed > 0:
                print(f"  Sync: {removed} orphaned rows removed")
            log.info(f"pull: {new_count} total rows ({added} new, {removed} removed)")

        return added

    def sync_with_valid(self):
        """No-op: pull() now handles full sync including deletions."""
        pass

    def auto_fill_from_brain(self):
        """Auto-fill HM/Recruiter names and emails from Brain contacts.
        
        For rows with Extract=yes but empty HM Name:
        1. Check Brain company_contacts for stored contacts
        2. Pre-fill name + email if found (skips all API calls at extraction time)
        
        Returns count of rows auto-filled.
        """
        try:
            from outreach.brain import Brain
            import re as _re
            b = Brain.get()
            contacts = b._data.get("company_contacts", {})
            if not contacts:
                return 0

            data = self.ws.get_all_values()
            updates = []
            filled = 0

            for i, r in enumerate(data[1:], start=2):
                r = _pad(r)
                co = r[C["company"]].strip()
                if not co:
                    continue
                extract = r[C["extract"]].strip().lower()
                if extract != "yes":
                    continue

                co_key = _re.sub(r"[^a-z0-9]", "", co.lower())
                co_contacts = contacts.get(co_key, {})

                for role, name_col, email_col in [
                    ("hm", "hm_name", "hm_email"),
                    ("rec", "rec_name", "rec_email"),
                ]:
                    current_name = r[C[name_col]].strip()
                    current_email = r[C[email_col]].strip()

                    if current_email:
                        continue  # already has email

                    contact = co_contacts.get(role, {})
                    if contact and contact.get("email") and not contact.get("bounced"):
                        name = contact.get("name", "")
                        email = contact.get("email", "")
                        # Column letters for batch update
                        name_letter = chr(65 + C[name_col])  # A=0, B=1, ...
                        email_letter = chr(65 + C[email_col])
                        if name and not current_name:
                            updates.append({"range": f"{name_letter}{i}", "values": [[name]]})
                        updates.append({"range": f"{email_letter}{i}", "values": [[email]]})
                        filled += 1
                        log.info(f"Brain auto-fill: {co} [{role}] → {email}")

            if updates:
                import time
                for chunk in range(0, len(updates), 20):
                    self.ws.batch_update(updates[chunk:chunk+20], value_input_option="USER_ENTERED")
                    time.sleep(1)
                log.info(f"Auto-filled {filled} contacts from Brain")
            return filled
        except Exception as e:
            log.warning(f"Brain auto-fill failed: {e}")
            return 0

    def format_outreach_sheet(self):
        """
        Format the Outreach Tracker for efficient manual work:
        1. Color Extract=yes cells green
        2. Add LinkedIn search links for companies without HM/Recruiter names
        3. Add priority indicators
        """
        try:
            import re as _re
            data = self.ws.get_all_values()
            if len(data) < 2:
                return

            ss = self.ws.spreadsheet
            extract_col = C["extract"]  # Column D (index 3)

            # ── 1. Color Extract=yes cells green ──────────────────────────
            color_requests = []
            for i, r in enumerate(data[1:], 1):  # 0-indexed for API
                r = _pad(r)
                extract_val = r[extract_col].strip().lower()
                if extract_val == "yes":
                    color_requests.append({
                        "repeatCell": {
                            "range": {
                                "sheetId": self.ws.id,
                                "startRowIndex": i,
                                "endRowIndex": i + 1,
                                "startColumnIndex": extract_col,
                                "endColumnIndex": extract_col + 1,
                            },
                            "cell": {"userEnteredFormat": {
                                "backgroundColor": {"red": 0.58, "green": 0.93, "blue": 0.31},
                                "textFormat": {"bold": True},
                            }},
                            "fields": "userEnteredFormat.backgroundColor,userEnteredFormat.textFormat.bold",
                        }
                    })
                elif extract_val == "skip":
                    color_requests.append({
                        "repeatCell": {
                            "range": {
                                "sheetId": self.ws.id,
                                "startRowIndex": i,
                                "endRowIndex": i + 1,
                                "startColumnIndex": extract_col,
                                "endColumnIndex": extract_col + 1,
                            },
                            "cell": {"userEnteredFormat": {
                                "backgroundColor": {"red": 0.9, "green": 0.9, "blue": 0.9},
                            }},
                            "fields": "userEnteredFormat.backgroundColor",
                        }
                    })

            if color_requests:
                import time
                for chunk in range(0, len(color_requests), 200):
                    ss.batch_update({"requests": color_requests[chunk:chunk+200]})
                    time.sleep(0.5)
                log.info(f"Formatted {len(color_requests)} Extract cells")

            return len(color_requests)

        except Exception as e:
            log.warning(f"Outreach formatting failed: {e}")
            return 0

    def rows_for_extraction(self):
        data = self.ws.get_all_values()
        self._p()

        ecache = {}
        for r in data[1:]:
            r = _pad(r)
            co = r[C["company"]].strip().lower()
            for nk, ek in [
                (C["hm_name"], C["hm_email"]),
                (C["rec_name"], C["rec_email"]),
            ]:
                n, e = r[nk].strip().lower(), r[ek].strip()
                if n and e:
                    ecache[(n, co)] = e

        rows = []
        for i, r in enumerate(data[1:], start=2):
            r = _pad(r)
            co = r[C["company"]].strip()
            if not co:
                continue
            hn, rn = r[C["hm_name"]].strip(), r[C["rec_name"]].strip()
            he, re_ = r[C["hm_email"]].strip(), r[C["rec_email"]].strip()
            err = ""  # Error Log column removed — errors in .local/outreach.log

            # FIX 1: Skip rows not marked for extraction
            extract_val = r[C["extract"]].strip().lower()
            if extract_val != "yes":
                continue

            # Auto-resolve: if name field contains LinkedIn URL, use it as the URL
            # and extract name from it during find()
            import re as _re_li
            def _resolve_li_name(name_val, li_val):
                """If name looks like a LinkedIn URL, swap into li field."""
                if not name_val:
                    return name_val, li_val
                parts = [p.strip() for p in name_val.split(",") if p.strip()]
                resolved_names = []
                for part in parts:
                    if _re_li.search(r"linkedin\.com/in/", part):
                        # Name field has a LinkedIn URL — use it as li if li is empty
                        if not li_val:
                            li_val = part
                        # Return empty name — finder will extract from URL
                        resolved_names.append(part)  # keep URL as name; finder handles it
                    else:
                        resolved_names.append(part)
                return ", ".join(resolved_names), li_val

            hn, hli = _resolve_li_name(hn, r[C["hm_li"]].strip())
            rn, rli = _resolve_li_name(rn, r[C["rec_li"]].strip())

            hn_list = [n.strip() for n in hn.split(",") if n.strip()] if hn else []
            rn_list = [n.strip() for n in rn.split(",") if n.strip()] if rn else []
            hn = ", ".join(hn_list)
            rn = ", ".join(rn_list)

            # Also queue if LinkedIn URL exists but name is empty — finder extracts name from URL
            hli_val = r[C["hm_li"]].strip()
            rli_val = r[C["rec_li"]].strip()
            need_h = (bool(hn) or bool(hli_val)) and not he and "HM:" not in err
            need_r = (bool(rn) or bool(rli_val)) and not re_ and "REC:" not in err

            if need_h:
                dup = ecache.get((hn.lower(), co.lower()))
                if dup:
                    self.write_email(i, "hm", dup, "cache")
                    need_h = False
            if need_r:
                dup = ecache.get((rn.lower(), co.lower()))
                if dup:
                    self.write_email(i, "rec", dup, "cache")
                    need_r = False

            if need_h or need_r:
                rows.append(
                    {
                        "row": i,
                        "co": co,
                        "title": r[C["title"]].strip(),
                        "jid": r[C["job_id"]].strip(),
                        "hn": hn,
                        "hli": r[C["hm_li"]].strip(),
                        "rn": rn,
                        "rli": r[C["rec_li"]].strip(),
                        "need_h": need_h,
                        "need_r": need_r,
                        "he": he,
                        "re": re_,
                    }
                )
        return rows

    def write_bounce_note(self, row, ct, email, bounced_at):
        """Write clean bounce note, overwrite delivery status, clear bad email."""
        try:
            notes_col = _cl(C["notes"])
            existing_note = self.ws.acell(f"{notes_col}{row}").value or ""
            self._p()

            r = _pad(self.ws.row_values(row))
            self._p()

            other_ct = "Rec" if ct == "hm" else "HM"
            this_ct = "HM" if ct == "hm" else "Rec"
            other_bounced = f"{other_ct} email bounced" in existing_note

            if other_bounced:
                bounce_note = f"HM and Rec emails bounced on {bounced_at}"
            else:
                bounce_note = f"{this_ct} email bounced on {bounced_at}"

            # Rebuild notes: remove old bounce and delivery notes for this contact
            old_parts = [p.strip() for p in existing_note.split("|") if p.strip()]
            keep = []
            for p in old_parts:
                if "bounced" in p.lower():
                    continue
                if "Delivered to" in p and this_ct in p:
                    if "and" in p:
                        keep.append(f"Delivered to {other_ct}")
                    continue
                keep.append(p)

            if keep:
                final = " | ".join(keep) + " | " + bounce_note
            else:
                final = bounce_note

            self._retry(self.ws.update_acell, f"{notes_col}{row}", final)
            self._p()

            email_col = C["hm_email"] if ct == "hm" else C["rec_email"]
            self._retry(self.ws.update_acell, f"{_cl(email_col)}{row}", "")
            self._p()
            log.info(f"Row {row}: {this_ct} bounce noted for {email}")
        except Exception as e:
            log.error(f"write_bounce_note row {row}: {e}")

    def flag_bounced_rows(self, bounced_emails: set):
        """Scan Outreach rows, flag bounces, auto-retry with alternative patterns."""
        if not bounced_emails:
            return 0
        bounced_lower = {e.lower().strip() for e in bounced_emails}
        try:
            data = self.ws.get_all_values()
            self._p()
            flagged = 0
            retried = 0
            today = __import__('datetime').date.today().strftime("%b %d, %Y")

            for i, r in enumerate(data[1:], start=2):
                r = _pad(r)
                he = r[C["hm_email"]].strip()
                re_ = r[C["rec_email"]].strip()
                co = r[C["company"]].strip()
                hn = r[C["hm_name"]].strip()
                rn = r[C["rec_name"]].strip()

                for ct, email, name in [("hm", he, hn), ("rec", re_, rn)]:
                    if not email or email.lower().strip() not in bounced_lower:
                        continue

                    self.write_bounce_note(i, ct, email, today)
                    flagged += 1

                    # Auto-retry: try alternative email pattern
                    if name and co and "@" in email:
                        new_email = self._retry_bounced_email(name, co, email)
                        if new_email and new_email.lower() != email.lower():
                            email_col = C["hm_email"] if ct == "hm" else C["rec_email"]
                            self._retry(self.ws.update_acell, f"{_cl(email_col)}{i}", new_email)
                            self._p()
                            # Update notes
                            notes_col = _cl(C["notes"])
                            existing = self.ws.acell(f"{notes_col}{i}").value or ""
                            self._p()
                            retry_note = f"Retried: {new_email}"
                            updated = f"{existing} | {retry_note}".strip(" |") if existing else retry_note
                            self._retry(self.ws.update_acell, f"{notes_col}{i}", updated)
                            self._p()
                            retried += 1
                            log.info(f"Row {i}: bounce retry {email} → {new_email}")

            if flagged:
                msg = f"  Bounce flags: {flagged} bad email(s) noted and cleared"
                if retried:
                    msg += f", {retried} retried with new pattern"
                log.info(msg)
                print(msg)
            return flagged
        except Exception as e:
            log.error(f"flag_bounced_rows failed: {e}")
            return 0

    def _retry_bounced_email(self, name, company, bounced_email):
        """Try alternative email patterns after a bounce. Returns new email or None."""
        try:
            from outreach.outreach_data import NameParser, PatternCache
            parsed = NameParser.parse(name)
            if not parsed or parsed["single"]:
                return None

            domain = bounced_email.split("@")[1] if "@" in bounced_email else ""
            if not domain:
                return None

            # Get the pattern that bounced
            pc = PatternCache()
            bounced_local = bounced_email.split("@")[0].lower()

            # Mark bounced pattern as failed for this domain
            failed_file = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                ".local", "failed_patterns.json"
            )
            failed = {}
            try:
                if os.path.exists(failed_file):
                    failed = json.load(open(failed_file))
            except Exception as _e:
                log.debug(f"sheets op failed: {_e}")
            if domain not in failed:
                failed[domain] = []
            # FIX 4: store both local part AND pattern string for domain-wide blocking
            if bounced_local not in failed[domain]:
                failed[domain].append(bounced_local)
            _f = parsed["fa"].lower() if parsed else ""
            _la = parsed["lc"] if parsed else ""
            _fi = parsed["fi"] if parsed else ""
            _li = parsed["li"] if parsed else ""
            from outreach.outreach_config import PAT_A, PAT_B, PAT_C
            for _pat in PAT_A + PAT_B + PAT_C:
                _gen = (_pat.replace("{first}", _f).replace("{last}", _la)
                            .replace("{f}", _fi).replace("{l}", _li))
                if _gen == bounced_local and _pat not in failed[domain]:
                    failed[domain].append(_pat)
                    break
            try:
                _atomic_write_json(failed_file, failed)
            except Exception as _e:
                log.debug(f"sheets op failed: {_e}")

            from outreach.outreach_config import PAT_A, PAT_B, PAT_C
            b = Brain.get()
            f = parsed["fa"].lower()
            la = parsed["lc"]
            fi = parsed["fi"]
            li = parsed["li"]
            # Record bounce in Brain
            for _p in PAT_A + PAT_B + PAT_C:
                _gen = (_p.replace("{first}", f).replace("{last}", la)
                          .replace("{f}", fi).replace("{l}", li))
                if _gen == bounced_local:
                    b.record_pattern_failure(domain, _p)
                    break
            # Rank candidates by Brain Bayesian posterior
            all_pats = list(dict.fromkeys(PAT_A + PAT_B))
            ranked_pats = b.rank_patterns_for(domain, all_pats)
            candidates = []
            for pat in ranked_pats:
                local = (pat.replace("{first}", f).replace("{last}", la)
                           .replace("{f}", fi).replace("{l}", li))
                if (local and local != bounced_local
                        and local not in failed.get(domain, [])
                        and not b.is_failed_pattern(domain, pat)):
                    candidates.append((f"{local}@{domain}", pat))

            if not candidates:
                return None

            # Try to verify each candidate
            try:
                from outreach.outreach_provider import ProviderVerifier
                pv = ProviderVerifier()
                for candidate, cand_pat in candidates[:5]:
                    result = pv.verify_email(candidate, domain)
                    if result == "exists":
                        pc.store(domain, cand_pat)
                        b.record_pattern_success(domain, cand_pat, candidate)
                        log.info(f"Bounce retry verified: {candidate}")
                        return candidate
                    else:
                        b.record_pattern_failure(domain, cand_pat)
            except Exception as e:
                log.debug(f"Bounce retry verification failed: {e}")

            if candidates:
                log.info(f"Bounce retry (unverified): {candidates[0][0]}")
                return candidates[0][0]

        except Exception as e:
            log.debug(f"_retry_bounced_email failed: {e}")
        return None

    def populate_linkedin_msgs(self):
        """Auto-populate HM and Rec LinkedIn Msg columns."""
        try:
            data = self.ws.get_all_values()
            self._p()
            updates = []
            for i, r in enumerate(data[1:], start=2):
                r = _pad(r)
                co = r[C["company"]].strip()
                title = r[C["title"]].strip()
                hn = r[C["hm_name"]].strip()
                rn = r[C["rec_name"]].strip()
                if not co:
                    continue
                # FIX 9: skip rows not marked for extraction
                extract_val = r[C["extract"]].strip().lower() if len(r) > C["extract"] else ""
                if extract_val != "yes":
                    continue
                # Skip if no emails discovered yet — LinkedIn msg without email is useless
                he = r[C["hm_email"]].strip() if len(r) > C["hm_email"] else ""
                re_ = r[C["rec_email"]].strip() if len(r) > C["rec_email"] else ""
                if not he and not re_:
                    continue
                # HM LinkedIn Msg (supports multiple comma-separated names)
                hm_existing = r[C["hm_li_msg"]].strip() if len(r) > C["hm_li_msg"] else ""
                if not hm_existing and hn:
                    hm_names = [n.strip() for n in hn.split(",") if n.strip()]
                    hm_msgs = []
                    for name in hm_names:
                        first = name.split()[0].strip()
                        msg = HM_LI_MSG_TEMPLATE.format(first=first, title=title, company=co)
                        if len(msg) > LI_MSG_MAX:
                            over = len(msg) - LI_MSG_MAX + 3
                            msg = HM_LI_MSG_TEMPLATE.format(first=first, title=title[:len(title)-over]+"...", company=co)
                        hm_msgs.append(msg)
                    col_letter = _cl(C["hm_li_msg"])
                    updates.append({"range": f"{col_letter}{i}", "values": [[", ".join(hm_msgs)]]})
                # Rec LinkedIn Msg (supports multiple comma-separated names)
                rec_existing = r[C["rec_li_msg"]].strip() if len(r) > C["rec_li_msg"] else ""
                if not rec_existing and rn:
                    rec_names = [n.strip() for n in rn.split(",") if n.strip()]
                    rec_msgs = []
                    for name in rec_names:
                        first = name.split()[0].strip()
                        msg = REC_LI_MSG_TEMPLATE.format(first=first, title=title, company=co)
                        if len(msg) > LI_MSG_MAX:
                            over = len(msg) - LI_MSG_MAX + 3
                            msg = REC_LI_MSG_TEMPLATE.format(first=first, title=title[:len(title)-over]+"...", company=co)
                        rec_msgs.append(msg)
                    col_letter = _cl(C["rec_li_msg"])
                    updates.append({"range": f"{col_letter}{i}", "values": [[", ".join(rec_msgs)]]})

            if updates:
                for chunk_start in range(0, len(updates), 50):
                    chunk = updates[chunk_start:chunk_start+50]
                    self._retry(self.ws.batch_update, chunk, value_input_option="USER_ENTERED")
                    self._p()
                print(f"  LinkedIn messages: {len(updates)} generated")
                log.info(f"populate_linkedin_msgs: {len(updates)} messages written")
            return len(updates)
        except Exception as e:
            log.error(f"populate_linkedin_msgs failed: {e}")
            return 0

    def write_email(self, row, ct, email, source):
        try:
            # Check pattern consistency: all emails at same domain should use same pattern
            from outreach.outreach_verifier import is_suspicious_email as _verify_sus
            for e in email.split(","):
                e = e.strip()
                if e and "@" in e:
                    domain = e.split("@")[1].lower()
                    local = e.split("@")[0].lower()
                    # Detect pattern of this email
                    this_pattern = None
                    if "." in local:
                        parts = local.split(".")
                        if len(parts) == 2 and len(parts[0]) > 1 and len(parts[1]) > 1:
                            this_pattern = "{first}.{last}"
                        elif len(parts) == 2 and len(parts[0]) == 1:
                            this_pattern = "{f}.{last}"
                    elif "_" in local:
                        this_pattern = "{first}_{last}"
                    # Check if we already have a different pattern for this domain in the sheet
                    if this_pattern:
                        from outreach.outreach_verifier import DomainHistory
                        confirmed = DomainHistory.get_confirmed_pattern(domain)
                        if confirmed and confirmed != this_pattern:
                            log.warning(f"Row {row} {ct}: Pattern mismatch for {domain} — email uses '{this_pattern}' but confirmed pattern is '{confirmed}'. Flagging for review.")
            # Check each email for suspicious domains (use new verifier with role-based + short local checks)
            from outreach.outreach_verifier import is_suspicious_email as _new_sus_check
            clean_emails = []
            for e in email.split(","):
                e = e.strip()
                if _new_sus_check(e):
                    log.warning(f"Row {row} {ct}: Suspicious domain skipped: {e}")
                else:
                    clean_emails.append(e)
            if not clean_emails:
                log.warning(f"Row {row} {ct}: All emails suspicious — skipping")
                return
            email = ", ".join(clean_emails)
            col = C["hm_email"] if ct == "hm" else C["rec_email"]
            self._retry(self.ws.update_acell, f"{_cl(col)}{row}", email)
            log.info(f"Row {row} {ct}: {email} (via {source})")
            self._p()
        except Exception as e:
            log.error(f"write_email row {row}: {e}")

    def write_confidence(self, row, confidence_score):
        """Write confidence label (High/Medium/Low) with color to the Confidence column."""
        try:
            from outreach.outreach_verifier import confidence_label
            label = confidence_label(confidence_score)
            col_letter = _cl(C["confidence"])
            self._retry(self.ws.update_acell, f"{col_letter}{row}", label)
            self._p()
            # Apply background color based on confidence
            colors = {
                "High": {"red": 0.56, "green": 0.93, "blue": 0.56},    # Green
                "Medium": {"red": 1.0, "green": 0.85, "blue": 0.4},    # Orange
                "Low": {"red": 0.96, "green": 0.5, "blue": 0.5},       # Red
            }
            color = colors.get(label)
            if color:
                try:
                    col_idx = C["confidence"]
                    self.ws.spreadsheet.batch_update({"requests": [{
                        "repeatCell": {
                            "range": {
                                "sheetId": self.ws.id,
                                "startRowIndex": row - 1,
                                "endRowIndex": row,
                                "startColumnIndex": col_idx,
                                "endColumnIndex": col_idx + 1,
                            },
                            "cell": {
                                "userEnteredFormat": {
                                    "backgroundColor": color,
                                    "textFormat": {"bold": True},
                                    "horizontalAlignment": "CENTER",
                                }
                            },
                            "fields": "userEnteredFormat",
                        }
                    }]})
                    self._p()
                except Exception as ce:
                    log.debug(f"Color formatting failed row {row}: {ce}")
        except Exception as e:
            log.error(f"write_confidence row {row}: {e}")

    def write_send_at(self, row, send_at_text, sent_date_text=""):
        try:
            self._retry(self.ws.update_acell, f"{_cl(C['send_at'])}{row}", send_at_text)
            self._p()
            if sent_date_text:
                self._retry(self.ws.update_acell, f"{_cl(C['sent_dt'])}{row}", sent_date_text)
                self._p()
        except Exception as e:
            log.error(f"write_send_at row {row}: {e}")

    def write_error(self, row, msg):
        """Errors logged to .local/outreach.log only — no sheet column."""
        log.debug(f"Row {row}: {msg}")

    def append_error(self, row, msg):
        """Errors logged to .local/outreach.log only — no sheet column."""
        log.debug(f"Row {row}: {msg}")

    def _build_resume_cache(self):
        """Build resume cache from Valid Entries. Safe to call multiple times."""
        try:
            valid = self.ss.worksheet("Valid Entries")
            rows = valid.get_all_values()
            self._p()
            Sheets._resume_cache = {}
            for row in rows[1:]:
                if len(row) > V_RESUME:
                    key = (
                        row[V_COMPANY].strip().lower(),
                        row[V_TITLE].strip().lower(),
                    )
                    r = row[V_RESUME].strip()
                    Sheets._resume_cache[key] = r if r in ("SDE", "ML", "DA") else "SDE"
        except Exception as e:
            import logging as _log
            _log.getLogger(__name__).debug(f"resume cache build failed: {e}")
            if Sheets._resume_cache is None:
                Sheets._resume_cache = {}

    def get_resume_type(self, company, title):
        if Sheets._resume_cache is None:
            self._build_resume_cache()
        return Sheets._resume_cache.get(
            (company.strip().lower(), title.strip().lower()), "SDE"
        )

    def get_location(self, company, title):
        if Sheets._location_cache is None:
            try:
                valid = self.ss.worksheet("Valid Entries")
                rows = valid.get_all_values()
                self._p()
                Sheets._location_cache = {}
                for row in rows[1:]:
                    if len(row) > 8:
                        key = (
                            row[V_COMPANY].strip().lower(),
                            row[V_TITLE].strip().lower(),
                        )
                        Sheets._location_cache[key] = row[8].strip()
            except Exception as _e:
                logging.debug("suppressed: %s", _e)
                Sheets._location_cache = {}
        return Sheets._location_cache.get(
            (company.strip().lower(), title.strip().lower()), ""
        )

    def get_job_url_domain(self, company, title):
        """Extract domain from Job URL in Valid Entries (column F, index 5)."""
        if not hasattr(Sheets, "_url_domain_cache") or Sheets._url_domain_cache is None:
            try:
                valid = self.ss.worksheet("Valid Entries")
                rows = valid.get_all_values()
                self._p()
                Sheets._url_domain_cache = {}
                for row in rows[1:]:
                    if len(row) > 5 and row[5].strip().startswith("http"):
                        key = (row[2].strip().lower(), row[3].strip().lower())
                        try:
                            from urllib.parse import urlparse
                            parsed = urlparse(row[5].strip())
                            domain = parsed.netloc.lower()
                            # Strip common prefixes
                            for prefix in ["www.", "jobs.", "careers.", "career.", "apply.", "recruiting.", "boards.greenhouse.io", "job-boards.greenhouse.io"]:
                                if domain.startswith(prefix) and domain != prefix.rstrip("."):
                                    domain = domain[len(prefix):]
                                    break
                            # Skip generic job boards — not the company domain
                            generic = {"lever.co", "greenhouse.io", "workday.com", "myworkdayjobs.com",
                                       "smartrecruiters.com", "icims.com", "ultipro.com", "taleo.net",
                                       "jobvite.com", "breezy.hr", "ashbyhq.com", "bamboohr.com",
                                       "jazz.co", "recruitee.com", "simplify.jobs", "linkedin.com",
                                       "indeed.com", "ziprecruiter.com", "glassdoor.com", "jobright.ai"}
                            if not any(g in domain for g in generic):
                                Sheets._url_domain_cache[key] = domain
                        except Exception as _e:
                            pass  # suppressed: use log.debug(_e) to investigate
            except Exception as _e:
                logging.debug("suppressed: %s", _e)
                Sheets._url_domain_cache = {}
        return Sheets._url_domain_cache.get(
            (company.strip().lower(), title.strip().lower()), ""
        )

    def compute_send_at(self, location):
        """Compute send time at 10 AM in company's timezone, display in EST.
        Returns (send_at_str, sent_date_str) tuple."""
        try:
            from zoneinfo import ZoneInfo
        except ImportError:
            try:
                from backports.zoneinfo import ZoneInfo
            except ImportError:
                return self._fallback_send_at()

        try:
            tz_name = "US/Eastern"
            state_code = None

            if location:
                m = re.search(r",\s*([A-Z]{2})\b", location)
                if m:
                    state_code = m.group(1)
                if not state_code:
                    try:
                        from aggregator.config import FULL_STATE_NAMES

                        for name, code in FULL_STATE_NAMES.items():
                            if name in location.lower():
                                state_code = code
                                break
                    except Exception as _e:
                        pass  # suppressed: use log.debug(_e) to investigate
                if state_code and state_code in STATE_TO_TIMEZONE:
                    tz_name = STATE_TO_TIMEZONE[state_code]

            tz = ZoneInfo(tz_name)
            now = datetime.datetime.now(tz)
            target = now.replace(hour=SEND_HOUR, minute=0, second=0, microsecond=0)

            if now.hour >= SEND_HOUR:
                target += datetime.timedelta(days=1)
            while target.weekday() >= 5:
                target += datetime.timedelta(days=1)

            # Convert to EST for display
            est = ZoneInfo("US/Eastern")
            target_est = target.astimezone(est)
            h = target_est.hour
            ampm = "AM" if h < 12 else "PM"
            dh = h % 12 or 12
            send_at = target_est.strftime("%b %d, ") + f"{dh}:{target_est.minute:02d} {ampm} ET"
            sent_date = target_est.strftime("%b %d, %Y")
            return send_at, sent_date
        except Exception as e:
            log.debug(f"Timezone calc failed: {e}")
            return self._fallback_send_at()

    @staticmethod
    def _fallback_send_at():
        """Used when the location has no parseable state.

        datetime.now() is server-local: on a GitHub runner that is UTC, so the
        old naive 11:00 was really 07:00 ET. Anchor to Eastern explicitly and
        label it honestly.
        """
        try:
            from zoneinfo import ZoneInfo
            now = datetime.datetime.now(ZoneInfo("US/Eastern"))
        except Exception:
            now = datetime.datetime.now()
        target = now.replace(hour=11, minute=0, second=0, microsecond=0)
        target += datetime.timedelta(days=1)
        while target.weekday() >= 5:
            target += datetime.timedelta(days=1)
        return target.strftime("%b %d, 11:00 AM ET"), target.strftime("%b %d, %Y")

    @staticmethod
    def _retry(func, *args, retries=3, **kwargs):
        for attempt in range(retries):
            try:
                return func(*args, **kwargs)
            except Exception as e:
                if "429" in str(e) or "RESOURCE_EXHAUSTED" in str(e):
                    wait = 2 ** (attempt + 1)
                    log.warning(f"Sheets rate limit, retrying in {wait}s...")
                    time.sleep(wait)
                elif attempt < retries - 1:
                    time.sleep(1)
                else:
                    raise

    def _p(self):
        time.sleep(SHEET_PAUSE)


class Credits:
    def __init__(self):
        self._d = {}
        self._load()

    def _default(self):
        t = datetime.datetime.now().strftime("%Y-%m-%d")
        d = {
            n: {"lim": c["limit"], "used": 0, "reset": t, "ok": True}
            for n, c in APIS.items()
        }
        d["gmail"] = {"lim": MAX_DAILY, "used": 0, "reset": t, "ok": True}
        return d

    def _load(self):
        if os.path.exists(CREDITS_FILE):
            try:
                self._d = json.load(open(CREDITS_FILE))
                self._auto_reset()
                return
            except Exception as _e:
                log.debug(f"sheets op failed: {_e}")
        self._d = self._default()
        self._save()

    def _save(self):
        try:
            _atomic_write_json(CREDITS_FILE, self._d)
        except Exception as _e:
            log.debug(f"op failed: {_e}")

    def _auto_reset(self):
        today = datetime.datetime.now().strftime("%Y-%m-%d")
        ch = False
        g = self._d.get("gmail", {})
        if g.get("reset") != today:
            self._d["gmail"] = {"lim": MAX_DAILY, "used": 0, "reset": today, "ok": True}
            ch = True
        for n in APIS:
            e = self._d.get(n, {})
            lr = e.get("reset", "")
            if lr:
                try:
                    ld = datetime.datetime.strptime(lr, "%Y-%m-%d")
                    now = datetime.datetime.now()
                    if now.month != ld.month or now.year != ld.year:
                        self._d[n] = {
                            "lim": APIS[n]["limit"],
                            "used": 0,
                            "reset": today,
                            "ok": True,
                        }
                        ch = True
                except Exception as _e:
                    pass  # suppressed: use log.debug(_e) to investigate
            if n not in self._d:
                self._d[n] = {
                    "lim": APIS[n]["limit"],
                    "used": 0,
                    "reset": today,
                    "ok": True,
                }
                ch = True
        if "gmail" not in self._d:
            self._d["gmail"] = {"lim": MAX_DAILY, "used": 0, "reset": today, "ok": True}
            ch = True
        if ch:
            self._save()

    def avail(self, p):
        e = self._d.get(p, {})
        return max(0, e.get("lim", 0) - e.get("used", 0)) if e.get("ok", True) else 0

    def use(self, p):
        e = self._d.setdefault(p, {"lim": 0, "used": 0, "reset": "", "ok": True})
        e["used"] = e.get("used", 0) + 1
        if e["used"] >= e.get("lim", 0):
            e["ok"] = False
        self._save()
        try:
            Brain.get().record_api_result(p, credit_used=True, email_found=False)
        except Exception:
            pass

    def record_email_found(self, api_name: str):
        """Call when an API returns a valid email — tracks ROI per credit."""
        try:
            Brain.get().record_api_result(api_name, credit_used=False, email_found=True)
        except Exception:
            pass

    def burn_rate_alerts(self) -> list:
        """Return alert strings for APIs approaching monthly exhaustion."""
        b = Brain.get()
        alerts = []
        for name, cfg in APIS.items():
            alert = b.api_burn_rate_alert(name, cfg.get("limit", 0))
            if alert:
                alerts.append(alert)
        return alerts

    def gmail_left(self):
        return self.avail("gmail")

    def use_gmail(self):
        self.use("gmail")

    def report(self):
        lines = []
        for n, e in self._d.items():
            a = max(0, e.get("lim", 0) - e.get("used", 0))
            lines.append(
                f"  {n:12s} {a:>4d}/{e.get('lim',0)} {'OK' if e.get('ok') else 'EXHAUSTED'}"
            )
        return "\n".join(lines)

    def reset_all(self):
        self._d = self._default()
        self._save()


class NameParser:
    @staticmethod
    def parse(name):
        if not name or not name.strip():
            return None
        n = name.strip()
        # If comma-separated multi-name like "John Smith, Jane Doe" — take first
        # (multi-name handling is done at caller level by splitting on comma)
        if "," in n:
            # Check if it looks like "Last, First" (single person) or "Name1, Name2" (multi)
            parts = [p.strip() for p in n.split(",")]
            if len(parts) >= 2:
                # Heuristic: if second part has a space, it's multi-person
                if " " in parts[1] or len(parts) > 2:
                    # Multi-person: take only first
                    n = parts[0]
                # else it's "Last, First" format — handled below
        # Step 1: Strip parentheticals FIRST e.g. "Joanna (Maskas) Clark" → "Joanna Clark"
        import re as _re
        n = _re.sub(r"\([^)]*\)\s*", "", n).strip()
        # Step 2: Strip academic credentials e.g. "Kelsey Anderson, M.S." → "Kelsey Anderson"
        # Runs BEFORE comma-split so credentials after comma don't confuse Last/First detection
        _creds_pat = (
            r"(?:,\s*|\s+)"
            r"\b(?:M\.?S|Ph\.?D|M\.?B\.?A|M\.?D|J\.?D|D\.?O|R\.?N|"
            r"C\.?P\.?A|P\.?E|D\.?V\.?M|Pharm\.?D|Ed\.?D|Psy\.?D|"
            r"Sc\.?D|LL\.?[MB]|B\.?[SAE])\.?\b"
        )
        n = _re.sub(_creds_pat, "", n, flags=_re.I).strip().rstrip(",").strip()
        # Step 3: Strip trailing degree words
        n = _re.sub(
            r",?\s+(?:Masters?|Bachelors?|Doctor(?:ate)?|Engineer|Prof(?:essor)?)\s*\.?\s*$",
            "", n, flags=_re.I
        ).strip()
        if "," in n:
            parts = [p.strip() for p in n.split(",", 1)]
            if len(parts) == 2 and parts[0] and parts[1]:
                if parts[1].lower().rstrip(".") not in STRIP_SUF:
                    n = f"{parts[1]} {parts[0]}"
        # Strip parenthetical parts like (Maskas) from "Joanna (Maskas) Clark"
        import re as _re
        n = _re.sub(r'\([^)]*\)\s*', '', n).strip()
        ws = n.split()
        while ws and ws[0].lower().rstrip(".") in STRIP_PRE:
            ws = ws[1:]
        while ws and ws[-1].lower().rstrip(".") in STRIP_SUF:
            ws = ws[:-1]
        if not ws:
            return None
        first = ws[0]
        single = len(ws) == 1
        last = "" if single else (ws[1] if len(ws) == 2 else " ".join(ws[1:]))
        fa, la = _ascii(first), _ascii(last) if last else ""
        lc = re.sub(r"[^a-z]", "", la.lower()) if la else ""
        return {
            "full": name.strip(),
            "first": first,
            "last": last,
            "fa": fa,
            "la": la,
            "lc": lc,
            "fi": fa[0].lower() if fa else "",
            "li": la[0].lower() if la else "",
            "single": single,
            "hyph": "-" in first,
            "multi": " " in last if last else False,
        }

    @staticmethod
    def gen_phased(parsed, domains):
        if not parsed or not domains:
            return [], [], []
        if parsed["single"]:
            return [f"{parsed['fa'].lower()}@{d}" for d in domains], [], []
        f, la, fi, li = parsed["fa"].lower(), parsed["lc"], parsed["fi"], parsed["li"]

        def build(pats):
            s = set()
            for p in pats:
                lp = (
                    p.replace("{first}", f)
                    .replace("{last}", la)
                    .replace("{f}", fi)
                    .replace("{l}", li)
                )
                if lp and len(lp) >= 2:
                    s.add(lp)
            return s

        pa, pb, pc = build(PAT_A), build(PAT_B), build(PAT_C)
        ex = set()
        if parsed["hyph"]:
            nh, dh = f.replace("-", ""), f.replace("-", ".")
            bi = "".join(p[0] for p in f.split("-") if p)
            ex.update(
                [
                    f"{nh}.{la}",
                    f"{dh}.{la}",
                    f"{bi}{la}",
                    f"{bi}.{la}",
                    f"{f.split('-')[0]}.{la}",
                ]
            )
        if parsed["multi"]:
            pts = parsed["la"].lower().split()
            fin = re.sub(r"[^a-z]", "", pts[-1])
            part = {"van", "von", "de", "del", "di", "la", "le", "el", "al", "bin"}
            np_ = [re.sub(r"[^a-z]", "", p) for p in pts if p not in part]
            if fin:
                ex.update([f"{f}.{fin}", f"{fi}{fin}"])
            if np_:
                j = "".join(np_)
                ex.update([f"{f}.{j}", f"{fi}{j}"])
        if len(f) > 6 or len(la) > 8:
            ex.update([f"{f[:3]}.{la}", f"{f}.{la[:4]}", f"{fi}{la[:6]}"])
        pb.update({lp for lp in ex if lp and len(lp) >= 2})

        def emails(lps):
            return [f"{lp}@{d}" for d in domains for lp in lps]

        return emails(pa), emails(pb), emails(pc)


_SEED = {
    "google.com": "{first}.{last}",
    "meta.com": "{first}.{last}",
    "amazon.com": "{f}{last}",
    "apple.com": "{first}_{last}",
    "microsoft.com": "{first}.{last}",
    "netflix.com": "{first}.{last}",
    "salesforce.com": "{first}.{last}",
    "stripe.com": "{first}.{last}",
    "bain.com": "{first}.{last}",
    "neuralink.com": "{first}.{last}",
    "rivian.com": "{first}.{last}",
    "northwesternmutual.com": "{first}.{last}",
    "f5.com": "{first}.{last}",
    "hp.com": "{first}.{last}",
    "seismic.com": "{first}.{last}",
    "inogen.com": "{first}.{last}",
    "snowflake.com": "{first}.{last}",
    "uber.com": "{first}.{last}",
    "airbnb.com": "{first}.{last}",
    "figma.com": "{first}.{last}",
    "servicenow.com": "{first}.{last}",
    "intuit.com": "{first}.{last}",
    "oracle.com": "{first}.{last}",
    "adobe.com": "{first}.{last}",
    "ibm.com": "{first}.{last}",
    "nvidia.com": "{first}.{last}",
    "jpmorgan.com": "{f}{last}",
    "goldmansachs.com": "{first}.{last}",
    "deloitte.com": "{first}{last}",
    "mckinsey.com": "{f}.{last}",
    "bcg.com": "{f}.{last}",
    "tesla.com": "{first}.{last}",
    "openai.com": "{first}.{last}",
    "databricks.com": "{first}.{last}",
    "palantir.com": "{first}.{last}",
    "tiktok.com": "{first}.{last}",
    "t-mobile.com": "{first}.{last}",
    "verizon.com": "{first}.{last}",
    "coinbase.com": "{first}.{last}",
    "cloudflare.com": "{first}.{last}",
    "twilio.com": "{first}.{last}",
    "spacex.com": "{first}.{last}",
    "intel.com": "{first}.{last}",
    "amd.com": "{first}.{last}",
    "pwc.com": "{first}.{last}",
    "accenture.com": "{first}.{last}",
    "citi.com": "{first}.{last}",
}


class PatternCache:
    _singleton = None

    def __new__(cls, *args, **kwargs):
        if cls._singleton is None:
            inst = object.__new__(cls)
            inst._init()
            cls._singleton = inst
        return cls._singleton

    def _init(self):
        self._d = dict(_SEED)
        if os.path.exists(PATTERNS_FILE):
            try:
                self._d.update(json.load(open(PATTERNS_FILE)))
            except Exception as _e:
                log.debug(f"PatternCache load failed: {_e}")
        try:
            b = Brain.get()
            for domain, entry in b._data.get("domains", {}).items():
                pat = entry.get("email_pattern")
                conf = entry.get("pattern_confidence", 0.0)
                if pat and conf >= 0.5 and domain not in self._d:
                    self._d[domain] = pat
        except Exception as _be:
            log.debug(f"Brain→PatternCache merge failed: {_be}")

    def _save(self):
        try:
            _atomic_write_json(PATTERNS_FILE, self._d)
        except Exception as _e:
            log.debug(f"op failed: {_e}")

    def get(self, domain):
        return self._d.get(domain.lower())

    def store(self, domain, pat):
        self._d[domain.lower()] = pat
        self._save()
        try:
            Brain.get().record_pattern_success(domain, pat, "")
        except Exception:
            pass

    def detect(self, email, parsed):
        if not email or "@" not in email or not parsed:
            return None
        local, dom = email.split("@")[0].lower(), email.split("@")[1].lower()
        f, la, fi, li = parsed["fa"].lower(), parsed["lc"], parsed["fi"], parsed["li"]
        for p in PAT_A + PAT_B + PAT_C:
            gen = (
                p.replace("{first}", f)
                .replace("{last}", la)
                .replace("{f}", fi)
                .replace("{l}", li)
            )
            if gen == local:
                self.store(dom, p)
                return p
        return None

    def gen_single(self, parsed, domain):
        b = Brain.get()
        p = b.best_pattern_for(domain) or self.get(domain)
        if not p:
            from outreach.outreach_config import PAT_A, PAT_B, PAT_C
            all_pats = list(dict.fromkeys(PAT_A + PAT_B + PAT_C))
            ranked = b.rank_patterns_for(domain, all_pats)
            p = ranked[0] if ranked else None
        if not p or not parsed:
            return None
        f, la, fi, li = parsed["fa"].lower(), parsed["lc"], parsed["fi"], parsed["li"]
        lp = (
            p.replace("{first}", f)
            .replace("{last}", la)
            .replace("{f}", fi)
            .replace("{l}", li)
        )
        return f"{lp}@{domain}" if lp and len(lp) >= 2 else None


def _cl(idx):
    r = ""
    i = idx
    while i >= 0:
        r = chr(i % 26 + ord("A")) + r
        i = i // 26 - 1
    return r


def _pad(row):
    return (
        list(row) + [""] * (len(O_HEADERS) - len(row))
        if len(row) < len(O_HEADERS)
        else row
    )


def _ascii(text):
    try:
        n = unicodedata.normalize("NFKD", text)
        a = n.encode("ASCII", "ignore").decode("ASCII")
        return a if a else text
    except Exception as _e:
        logging.debug("suppressed: %s", _e)
        return text

