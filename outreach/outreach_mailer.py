#!/usr/bin/env python3
"""Outreach Pipeline — Gmail Draft Creator + Email Drafter."""

import os, pickle, time, random, datetime, logging, json, base64
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders
from outreach.outreach_config import (
    GMAIL_CREDS,
    GMAIL_TOKEN,
    GMAIL_SCOPES,
    SENDER_NAME,
    SENDER_EMAIL,
    MS_SENDER_EMAIL,
    MS_SENDER_NAME,
    MS_CLIENT_ID,
    MS_AUTHORITY,
    MS_SCOPES,
    MS_TOKEN_FILE,
    MAX_HOURLY,
    DELAY_MIN,
    DELAY_MAX,
    HM_SUBJ,
    HM_BODY,
    REC_SUBJ,
    REC_BODY,
    RESUME_SDE,
    RESUME_ML,
    RESUME_DA,
    DRAFT_HISTORY_FILE,
    warmup_limit,
)
from outreach.outreach_data import Credits, NameParser

log = logging.getLogger(__name__)



_MAILER_FOLDER_CACHE: dict = {}


def _get_or_create_folder(token: str, name: str) -> str:
    """Get or create an Outlook mail folder by display name. Cached per process."""
    import requests as _r
    from outreach.outreach_config import MS_SENDER_EMAIL
    if name in _MAILER_FOLDER_CACHE:
        return _MAILER_FOLDER_CACHE[name]
    resp = _r.get(
        f"https://graph.microsoft.com/v1.0/users/{MS_SENDER_EMAIL}/mailFolders",
        headers={"Authorization": f"Bearer {token}"},
        params={"$top": 50}, timeout=10,
    )
    if resp.status_code == 200:
        for f in resp.json().get("value", []):
            _MAILER_FOLDER_CACHE[f["displayName"]] = f["id"]
        if name in _MAILER_FOLDER_CACHE:
            return _MAILER_FOLDER_CACHE[name]
    cr = _r.post(
        f"https://graph.microsoft.com/v1.0/users/{MS_SENDER_EMAIL}/mailFolders",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={"displayName": name}, timeout=10,
    )
    if cr.status_code in (200, 201):
        fid = cr.json()["id"]
        _MAILER_FOLDER_CACHE[name] = fid
        return fid
    raise Exception(f"Could not find or create folder '{name}'")


class Drafter:
    @staticmethod
    def draft(name, contact_type, company, title, job_id="", resume_type="SDE"):
        parsed = NameParser.parse(name)
        first = parsed["first"] if parsed else (name.split()[0] if name.strip() else company)
        # Role-specific template selection
        if contact_type == "hm":
            from outreach.outreach_config import HM_SUBJ, HM_BODIES, HM_BODY
            st = HM_SUBJ
            bt = HM_BODIES.get(resume_type, HM_BODY)
        else:
            from outreach.outreach_config import REC_SUBJ, REC_BODIES, REC_BODY
            st = REC_SUBJ
            bt = REC_BODIES.get(resume_type, REC_BODY)
        jid = job_id if job_id and job_id.strip() not in ("N/A", "", "N/a", "n/a") else ""
        subj, body = st, bt
        if not jid:
            subj = subj.replace(" | {job_id}", "")
            body = body.replace(" | {job_id}", "")
            subj = subj.replace(" {job_id}", "")
            body = body.replace(" {job_id}", "")
        # Shorten company name for email (keep full name in sheet)
        _short_co = company
        _strip_suffixes = [
            " Group Technologies", " Corporation", " Incorporated",
            " Holdings", " Solutions", " Enterprises", " Partners",
            " Services", " International",
            ", Inc.", ", Inc", " Inc.", " Inc", " LLC", " Ltd", " Ltd.",
            " Co.", " Corp.", " Corp", " LP", " L.P.",
        ]
        # Only strip suffix if remaining name is 3+ chars (avoid "Built Technologies" → "Built")
        for _sfx in _strip_suffixes:
            if _short_co.endswith(_sfx) and len(_short_co) - len(_sfx) >= 5:
                _short_co = _short_co[:-len(_sfx)].strip()
                break
        # Handle "X and Y" patterns: "Rivian and Volkswagen Group" → "Rivian"
        if " and " in _short_co and len(_short_co) > 20:
            _short_co = _short_co.split(" and ")[0].strip()

        vals = {
            "first": first,
            "title": title,
            "job_id": jid,
            "company": _short_co,
            "sender": SENDER_NAME,
        }
        for k, v in vals.items():
            subj = subj.replace(f"{{{k}}}", v)
            body = body.replace(f"{{{k}}}", v)
        return {"subject": subj, "body": body.replace("\n\n\n", "\n\n")}


class Mailer:
    def __init__(self, credits: Credits):
        self.cr = credits
        self._svc = None
        self._hourly = 0
        self._hour_start = datetime.datetime.now()
        self._drafts_created = set()
        self._bounced_emails: set = set()
        self._load_draft_history()
        self._ms_access_token = None  # cached for this run
        self._ms_precheck()  # silently refresh MS token on init

    def _ms_precheck(self):
        """
        Self-healing MS token refresh.
        - Silent refresh: always attempted first (works 99% of the time)
        - If silent fails and running interactively: device flow re-auth
        - If silent fails and running as daemon (no TTY): email alert sent
        - Token valid for ~1 hour; refresh token valid ~90 days
        - Proactively refreshes if token expires within 10 minutes
        """
        try:
            import msal, time as _t
            from outreach.outreach_config import (
                MS_CLIENT_ID, MS_AUTHORITY, MS_SCOPES, MS_TOKEN_FILE
            )
            if not os.path.exists(MS_TOKEN_FILE):
                return
            cache = msal.SerializableTokenCache()
            cache.deserialize(open(MS_TOKEN_FILE).read())
            app = msal.PublicClientApplication(
                MS_CLIENT_ID, authority=MS_AUTHORITY, token_cache=cache
            )
            accounts = app.get_accounts()
            if not accounts:
                self._alert_token_expired("No accounts in token cache")
                return
            result = app.acquire_token_silent(MS_SCOPES, account=accounts[0])
            if result and "access_token" in result:
                self._ms_access_token = result["access_token"]
                self._ms_token_acquired_at = __import__("time").time()
                if cache.has_state_changed:
                    import fcntl as _fcntl
                    with open(MS_TOKEN_FILE + ".lock", "w") as _lf:
                        _fcntl.flock(_lf, _fcntl.LOCK_EX)
                        open(MS_TOKEN_FILE, "w").write(cache.serialize())
                        _fcntl.flock(_lf, _fcntl.LOCK_UN)
                log.info("MS token pre-checked: valid")
                return
            # Silent refresh failed — token or refresh token expired
            log.warning("MS token silent refresh failed — attempting recovery")
            import sys as _sys
            is_interactive = _sys.stdin.isatty() if hasattr(_sys.stdin, 'isatty') else False
            if is_interactive:
                # Running from terminal — do device flow
                log.info("Interactive mode: initiating device flow re-auth")
                flow = app.initiate_device_flow(scopes=MS_SCOPES)
                if "user_code" in flow:
                    print(f"\n{'='*60}")
                    print("MS token expired. Re-authenticate:")
                    print(flow["message"])
                    print("="*60)
                    result = app.acquire_token_by_device_flow(flow)
                    if result and "access_token" in result:
                        self._ms_access_token = result["access_token"]
                        if cache.has_state_changed:
                            import fcntl as _fcntl
                            with open(MS_TOKEN_FILE + ".lock", "w") as _lf:
                                _fcntl.flock(_lf, _fcntl.LOCK_EX)
                                open(MS_TOKEN_FILE, "w").write(cache.serialize())
                                _fcntl.flock(_lf, _fcntl.LOCK_UN)
                        log.info("MS token refreshed via device flow")
                        return
            else:
                # Running as daemon — send alert, don't hang
                self._alert_token_expired("Token refresh failed (daemon mode)")
        except Exception as e:
            log.debug(f"MS token precheck failed: {e}")
            self._alert_token_expired(str(e))

    def _alert_token_expired(self, reason: str):
        """Send email alert when MS token needs manual re-auth."""
        try:
            from outreach.brain import Brain
            Brain.get().send_email_alert(
                "🔑 MS Token expired — run python3 scripts/test_ms_auth.py",
                f"Microsoft Graph token needs re-authentication.\n\n"
                f"Reason: {reason}\n\n"
                f"Fix: cd to project folder and run:\n"
                f"  python3 scripts/test_ms_auth.py\n\n"
                f"Emails will fail until this is done."
            )
            log.warning(f"MS token alert sent: {reason}")
        except Exception as _ae:
            log.debug(f"Token alert failed: {_ae}")

    def set_bounced(self, bounced: set):
        self._bounced_emails = {e.lower().strip() for e in bounced}
        if self._bounced_emails:
            log.info(f"Mailer: {len(self._bounced_emails)} bounced email(s) loaded")

    def _load_draft_history(self):
        """Load draft history — Brain is source of truth, file is fallback."""
        try:
            from outreach.brain import Brain
            brain_drafts = Brain.get()._data.get("draft_history", [])
            file_drafts = []
            if os.path.exists(DRAFT_HISTORY_FILE):
                file_drafts = json.load(open(DRAFT_HISTORY_FILE))
            self._drafts_created = set(brain_drafts) | set(file_drafts)
        except Exception:
            try:
                if os.path.exists(DRAFT_HISTORY_FILE):
                    self._drafts_created = set(json.load(open(DRAFT_HISTORY_FILE)))
            except Exception:
                self._drafts_created = set()

    def _save_draft_history(self):
        """Save draft history to both file and Brain."""
        try:
            json.dump(list(self._drafts_created), open(DRAFT_HISTORY_FILE, "w"))
        except Exception as _rfe:
            # losing this re-creates drafts already sent
            try:
                from scripts._resilient import record_failure as _rf
                _rf('draft history write', _rfe)
            except Exception:
                pass
        try:
            from outreach.brain import Brain
            b = Brain.get()
            b._data["draft_history"] = list(self._drafts_created)
            b.save()
        except Exception:
            pass

    def _draft_key(self, to_email, subject):
        return f"{to_email.lower().strip()}||{subject.strip()}"

    def _ms_token(self):
        """Get Microsoft Graph access token. Proactively refreshes if >45 min old."""
        import time as _t
        # Proactive refresh: if token is older than 45 min, re-acquire silently
        if self._ms_access_token:
            age = _t.time() - getattr(self, "_ms_token_acquired_at", 0)
            if age < 45 * 60:  # still fresh
                return self._ms_access_token
            log.info("MS token >45min old — proactively refreshing")
            self._ms_access_token = None  # force re-acquire below
        import msal, json as _j
        from outreach.outreach_config import MS_CLIENT_ID, MS_AUTHORITY, MS_SCOPES, MS_TOKEN_FILE, MS_SENDER_EMAIL
        cache = msal.SerializableTokenCache()
        if os.path.exists(MS_TOKEN_FILE):
            try:
                cache.deserialize(open(MS_TOKEN_FILE).read())
            except Exception:
                pass
        app = msal.PublicClientApplication(MS_CLIENT_ID, authority=MS_AUTHORITY, token_cache=cache)
        accounts = app.get_accounts()
        result = None
        if accounts:
            result = app.acquire_token_silent(MS_SCOPES, account=accounts[0])
        if not result or "access_token" not in result:
            print("\n" + "="*60)
            print("Microsoft sign-in required for Northeastern email.")
            print(f"Sign in with: {MS_SENDER_EMAIL}")
            print("="*60 + "\n")
            flow = app.initiate_device_flow(scopes=MS_SCOPES)
            if "user_code" not in flow:
                raise Exception(f"MS auth failed: {flow.get('error_description')}")
            print(flow["message"])
            result = app.acquire_token_by_device_flow(flow)
        if cache.has_state_changed:
            try:
                import fcntl as _fcntl
                with open(MS_TOKEN_FILE + ".lock", "w") as _lf:
                    _fcntl.flock(_lf, _fcntl.LOCK_EX)
                    open(MS_TOKEN_FILE, "w").write(cache.serialize())
                    _fcntl.flock(_lf, _fcntl.LOCK_UN)
            except Exception as e:
                log.debug(f"MS token cache save failed: {e}")
        if "access_token" not in result:
            raise Exception(f"MS token error: {result.get('error_description', result)}")
        self._ms_access_token = result["access_token"]
        self._ms_token_acquired_at = __import__("time").time()
        return self._ms_access_token

    def _service(self):
        if self._svc:
            return self._svc
        from google.auth.transport.requests import Request
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
        import sys as _sys

        creds = None
        if os.path.exists(GMAIL_TOKEN):
            with open(GMAIL_TOKEN, "rb") as f:
                creds = pickle.load(f)

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                try:
                    creds.refresh(Request())
                    with open(GMAIL_TOKEN, "wb") as f:
                        pickle.dump(creds, f)
                    log.info("Gmail token auto-refreshed")
                except Exception as _ge:
                    log.warning(f"Gmail token refresh failed: {_ge}")
                    creds = None
            if not creds:
                # Need re-auth
                if not os.path.exists(GMAIL_CREDS):
                    raise FileNotFoundError(f"Missing Gmail credentials: {GMAIL_CREDS}")
                is_interactive = _sys.stdin.isatty() if hasattr(_sys.stdin, 'isatty') else False
                if not is_interactive:
                    # Running as daemon — alert and fail gracefully
                    try:
                        from outreach.brain import Brain
                        Brain.get().send_email_alert(
                            "🔑 Gmail token expired — run python3 scripts/test_ms_auth.py",
                            "Gmail OAuth token needs re-authentication for bounce scanning.\n\n"
                            "Fix: python3 -c \"from outreach.outreach_mailer import Mailer; "
                            "from outreach.outreach_data import Credits; Mailer(Credits())._service()\"\n"
                            "(run from terminal, not cron)"
                        )
                    except Exception:
                        pass
                    raise RuntimeError("Gmail token expired — needs interactive re-auth")
                flow = InstalledAppFlow.from_client_secrets_file(GMAIL_CREDS, GMAIL_SCOPES)
                creds = flow.run_local_server(port=0)
                with open(GMAIL_TOKEN, "wb") as f:
                    pickle.dump(creds, f)
                log.info("Gmail token re-authenticated via browser")

        self._svc = build("gmail", "v1", credentials=creds)
        return self._svc

    def send(self, to_email, subject, body, resume_type="SDE", company="", title="", location="", send_at_iso="", confidence=100):
        result = {"success": False, "error": "", "timestamp": ""}
        if resume_type == "ML":
            resume_path = RESUME_ML
        elif resume_type == "DA":
            resume_path = RESUME_DA
        else:
            resume_path = RESUME_SDE

        key = self._draft_key(to_email, subject)
        if key in self._drafts_created:
            result["error"] = "Duplicate draft (already created)"
            log.info(f"Skipped duplicate draft: {to_email}")
            return result
        if to_email.lower().strip() in self._bounced_emails:
            result["error"] = f"Bounced: {to_email}"
            result["status"] = "Bounced"
            log.info(f"Skipped bounced email: {to_email}")
            return result

        wl, gl = warmup_limit(), self.cr.gmail_left()
        if min(wl, gl) <= 0:
            result["error"] = f"Daily limit (warm-up={wl}, left={gl})"
            return result

        now = datetime.datetime.now()
        if (now - self._hour_start).total_seconds() > 3600:
            self._hourly = 0
            self._hour_start = now
        if self._hourly >= MAX_HOURLY:
            result["error"] = f"Hourly limit ({MAX_HOURLY})"
            return result

        # Triple gate: suspicious email check at draft creation (handle comma-separated)
        from outreach.outreach_verifier import is_suspicious_email as verify_suspicious
        for _single_email in to_email.split(","):
            _single_email = _single_email.strip()
            if _single_email and verify_suspicious(_single_email):
                result["error"] = f"Suspicious domain blocked at draft creation: {_single_email}"
                log.warning(result["error"])
                return result

        if not to_email or "@" not in to_email:
            result["error"] = f"Invalid: {to_email}"
            return result

        try:
            svc = self._service()
            msg = MIMEMultipart()
            msg["From"] = f"{SENDER_NAME} <{SENDER_EMAIL}>"
            msg["To"] = to_email
            msg["Subject"] = subject
            msg["Reply-To"] = SENDER_EMAIL
            html_body = Mailer._to_html(body)
            msg.attach(MIMEText(html_body, "html"))

            if os.path.exists(resume_path):
                with open(resume_path, "rb") as rf:
                    part = MIMEBase("application", "pdf")
                    part.set_payload(rf.read())
                    encoders.encode_base64(part)
                    part.add_header(
                        "Content-Disposition",
                        f"attachment; filename={os.path.basename(resume_path)}",
                    )
                    msg.attach(part)
            else:
                log.warning(f"Resume not found: {resume_path}")

            # Send via Microsoft Graph (Northeastern .edu address)
            import requests as _req, json as _j
            token = self._ms_token()

            # Build message payload for Graph API
            msg_payload = {
                "message": {
                    "subject": subject,
                    "body": {
                        "contentType": "HTML",
                        "content": html_body,
                    },
                    "toRecipients": [
                        {"emailAddress": {"address": to_email}}
                    ],
                    "from": {
                        "emailAddress": {
                            "name": MS_SENDER_NAME,
                            "address": MS_SENDER_EMAIL,
                        }
                    },
                    "replyTo": [
                        {"emailAddress": {
                            "name": MS_SENDER_NAME,
                            "address": MS_SENDER_EMAIL,
                        }}
                    ],
                },
                "saveToSentItems": "true",
            }

            # Attach resume
            if os.path.exists(resume_path):
                with open(resume_path, "rb") as rf:
                    import base64 as _b64
                    file_bytes = rf.read()
                    encoded = _b64.b64encode(file_bytes).decode()
                    msg_payload["message"]["attachments"] = [{
                        "@odata.type": "#microsoft.graph.fileAttachment",
                        "name": os.path.basename(resume_path),
                        "contentType": "application/pdf",
                        "contentBytes": encoded,
                    }]
            else:
                log.warning(f"Resume not found: {resume_path}")

            # ── Create draft with scheduling metadata ──────────────────────
            draft_payload = msg_payload["message"].copy()
            draft_payload["internetMessageHeaders"] = [
                {"name": "X-Send-At",    "value": send_at_iso or ""},
                {"name": "X-Company",    "value": company or ""},
                {"name": "X-Job-Title",  "value": title or ""},
                {"name": "X-Location",   "value": location or ""},
                {"name": "X-Confidence", "value": str(confidence)},
            ]
            create_resp = _req.post(
                f"https://graph.microsoft.com/v1.0/users/{MS_SENDER_EMAIL}/messages",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                data=_j.dumps(draft_payload),
                timeout=30,
            )
            if create_resp.status_code == 401:
                # Token expired mid-run — force refresh and retry once
                log.warning("401 on draft create — forcing token refresh and retrying")
                self._ms_access_token = None
                self._ms_token_acquired_at = 0
                token = self._ms_token()
                create_resp = _req.post(
                    f"https://graph.microsoft.com/v1.0/users/{MS_SENDER_EMAIL}/messages",
                    headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                    data=_j.dumps(draft_payload), timeout=30,
                )
            if create_resp.status_code not in (200, 201):
                raise Exception(
                    f"Draft create failed {create_resp.status_code}: {create_resp.text[:200]}"
                )
            draft_id = create_resp.json()["id"]

            # ── Move draft to 'Scheduled Outreach' folder ─────────────────
            folder_id = _get_or_create_folder(token, "Scheduled Outreach")
            move_resp = _req.post(
                f"https://graph.microsoft.com/v1.0/users/{MS_SENDER_EMAIL}/messages/{draft_id}/move",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                json={"destinationId": folder_id},
                timeout=10,
            )
            if move_resp.status_code not in (200, 201):
                log.warning(f"Draft created but folder move failed: {move_resp.status_code}")
            else:
                log.info(f"Draft queued in 'Scheduled Outreach' → {to_email} | send_at={send_at_iso}")

            self._hourly += 1
            self.cr.use_gmail()
            self._drafts_created.add(key)
            self._save_draft_history()

            result["success"] = True
            result["timestamp"] = now.strftime("%Y-%m-%d %H:%M:%S")
            log.info(f"Sent via Northeastern -> {to_email}")
        except Exception as e:
            result["error"] = f"Send failed: {str(e)[:120]}"
            log.error(result["error"])
        return result

    @staticmethod
    def _to_html(body):
        """Convert plain text body to clean HTML with professional formatting."""
        paragraphs = body.split("\n\n")
        style = (
            "font-family: Arial, sans-serif; font-size: 14px; "
            "line-height: 1.6; color: #333333; margin: 0 0 14px 0;"
        )
        parts = []
        for p in paragraphs:
            p = p.strip()
            if not p:
                continue
            p_html = p.replace("\n", "<br>")
            parts.append(f'<p style="{style}">{p_html}</p>')
        return (
            '<div style="font-family: Arial, sans-serif;">'
            + "\n".join(parts)
            + "</div>"
        )

    def wait(self):
        time.sleep(random.randint(DELAY_MIN, DELAY_MAX))

    def capacity(self):
        return {
            "daily": min(warmup_limit(), self.cr.gmail_left()),
            "hourly": MAX_HOURLY - self._hourly,
        }
