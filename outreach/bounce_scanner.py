#!/usr/bin/env python3
"""Bounce Scanner — reads Gmail for delivery failure notifications."""

import os
import re
import json
import base64
import logging
import datetime
from aggregator.atomic_json import write_json as _atomic_write_json

log = logging.getLogger(__name__)

BOUNCED_EMAILS_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    ".local",
    "bounced_emails.json",
)


class BounceScanner:

    @staticmethod
    def load_bounced() -> dict:
        """Load persisted bounce cache. Returns {email: {bounced_at, subject}}."""
        try:
            if os.path.exists(BOUNCED_EMAILS_FILE):
                return json.load(open(BOUNCED_EMAILS_FILE))
        except Exception as _swx:
            from aggregator.swallowed import swallow as _s; _s('bounce_scanner.load_bounced', _swx)
        return {}

    @staticmethod
    @staticmethod
    def update_domain_reputation():
        """Update domain reputation scores from bounce cache."""
        import json
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        rep_file = os.path.join(base, ".local", "domain_reputation.json")
        bounce_file = os.path.join(base, ".local", "bounced_emails.json")
        try:
            bounced = {}
            if os.path.exists(bounce_file):
                with open(bounce_file) as f:
                    bounced = json.load(f)
            reputation = {}
            if os.path.exists(rep_file):
                with open(rep_file) as f:
                    reputation = json.load(f)
            # Count bounces per domain
            from collections import Counter
            domain_bounces = Counter()
            for email in bounced:
                if "@" in email:
                    domain_bounces[email.split("@")[1]] += 1
            # Update reputation
            for domain, count in domain_bounces.items():
                if domain not in reputation:
                    reputation[domain] = {"score": 0, "bounces": 0, "successes": 0, "blocked": False}
                reputation[domain]["bounces"] = count
                reputation[domain]["score"] = count * -30
                reputation[domain]["blocked"] = count >= 5  # 5+ bounces = permanent block (5 pattern attempts)
                if count >= 2:
                    reputation[domain]["reason"] = f"{count} bounced emails — domain blocked"
            with open(rep_file, "w") as f:
                json.dump(reputation, f, indent=2)
        except Exception as _swx:
            from aggregator.swallowed import swallow as _s; _s('bounce_scanner.update_domain_reputation', _swx)

    @staticmethod
    def save_bounced(cache: dict):
        try:
            _atomic_write_json(BOUNCED_EMAILS_FILE, cache)
        except Exception as e:
            log.error(f"Failed to save bounce cache: {e}")
        BounceScanner.update_domain_reputation()

    @staticmethod
    def scan(gmail_service, days_back: int = 14) -> set:
        """
        Scan Gmail for bounce notifications from the last `days_back` days.
        Returns set of bounced email addresses (lowercase).
        Also persists new bounces to .local/bounced_emails.json.
        """
        bounced = BounceScanner.load_bounced()
        newly_found = set()

        try:
            after_date = (
                datetime.datetime.now() - datetime.timedelta(days=days_back)
            ).strftime("%Y/%m/%d")

            query = (
                f"after:{after_date} "
                "(from:mailer-daemon OR "
                'subject:"Delivery Status Notification" OR '
                'subject:"Mail delivery failed" OR '
                'subject:"Undeliverable" OR '
                'subject:"Delivery Failure" OR '
                'subject:"failure notice")'
            )

            # FIX 3b: also scan "Failed Emails" label explicitly
            label_query = f"after:{after_date} label:failed-emails"

            messages = []
            seen_ids = set()
            for q in [query, label_query]:
                try:
                    result = (
                        gmail_service.users()
                        .messages()
                        .list(userId="me", q=q, maxResults=100)
                        .execute()
                    )
                    for m in result.get("messages", []):
                        if m["id"] not in seen_ids:
                            seen_ids.add(m["id"])
                            messages.append(m)
                except Exception as qe:
                    log.debug(f"Bounce query failed ({q[:40]}): {qe}")

            if not messages:
                log.info("Bounce scanner: no bounce messages found")
                return set(bounced.keys())

            log.info(
                f"Bounce scanner: checking {len(messages)} potential bounce messages"
            )

            for msg_meta in messages:
                msg_id = msg_meta["id"]
                try:
                    msg = (
                        gmail_service.users()
                        .messages()
                        .get(userId="me", id=msg_id, format="full")
                        .execute()
                    )

                    subject = BounceScanner._get_header(msg, "Subject") or ""
                    failed_email = BounceScanner._extract_failed_email(msg)

                    if failed_email:
                        email_lower = failed_email.lower()
                        if email_lower not in bounced:
                            bounced[email_lower] = {
                                "bounced_at": datetime.datetime.now().isoformat(),
                                "subject": subject[:200],
                                "msg_id": msg_id,
                            }
                            # FIX 8: invalidate stale email_verify_cache entry
                            try:
                                import os as _os
                                _ev_file = _os.path.join(
                                    _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))),
                                    ".local", "email_verify_cache.json"
                                )
                                if _os.path.exists(_ev_file):
                                    _ev = json.load(open(_ev_file))
                                    if email_lower in _ev:
                                        del _ev[email_lower]
                                        _atomic_write_json(_ev_file, _ev)
                                        log.info(f"FIX8: Invalidated stale verify cache for {email_lower}")
                            except Exception as _eve:
                                log.debug(f"verify cache invalidation failed: {_eve}")
                            newly_found.add(email_lower)
                            log.info(f"Bounce detected: {email_lower} | {subject[:60]}")
                            # Mark contact as bounced in Brain for contact reuse system
                            try:
                                from outreach.brain import Brain
                                _b = Brain.get()
                                # Find which company this contact belongs to
                                for _co_key, _contacts in _b._data.get("company_contacts", {}).items():
                                    for _role, _contact in _contacts.items():
                                        if _contact.get("email", "").lower() == email_lower:
                                            _b.mark_contact_bounced(_co_key, _role, email_lower)
                                            log.info(f"Brain: marked bounced contact {email_lower} for {_co_key}")
                                            break
                            except Exception as _bce:
                                log.debug(f"Brain bounce mark failed: {_bce}")
                            # Auto-raise confidence threshold for this domain in Brain
                            try:
                                from outreach.brain import Brain
                                _dom = email_lower.split("@")[1] if "@" in email_lower else ""
                                if _dom:
                                    _b = Brain.get()
                                    _t = _b._data.setdefault("domain_thresholds", {})
                                    _cur = _t.get(_dom, 75)
                                    _new = min(_cur + 10, 95)
                                    if _new > _cur:
                                        _t[_dom] = _new
                                        _b.save()
                                        log.info(f"Brain: raised threshold {_dom}: {_cur} → {_new}")
                            except Exception as _swx:
                                from aggregator.swallowed import swallow as _s; _s('bounce_scanner.scan', _swx)
                            
                            # Learn from bounce: record failed pattern in DomainHistory
                            try:
                                from outreach.outreach_verifier import DomainHistory, CircuitBreaker
                                from outreach.outreach_config import PAT_A, PAT_B, PAT_C
                                if "@" in email_lower:
                                    local_part = email_lower.split("@")[0]
                                    domain = email_lower.split("@")[1]
                                    # Reverse-engineer which pattern template generated this email
                                    # by checking all patterns against the local part
                                    # We need a name to reverse-engineer, so we extract from subject
                                    # Subject format: "Prasad Kanade — Application for Title | JobID"
                                    bounced_pattern = None
                                    for pat in PAT_A + PAT_B + PAT_C:
                                        # Check if this pattern shape matches the local part structure
                                        if "." in local_part and pat == "{first}.{last}":
                                            bounced_pattern = pat
                                            break
                                        elif "_" in local_part and pat == "{first}_{last}":
                                            bounced_pattern = pat
                                            break
                                        elif "-" in local_part and pat == "{first}-{last}":
                                            bounced_pattern = pat
                                            break
                                    if not bounced_pattern:
                                        # Infer from structure
                                        if "." in local_part:
                                            parts = local_part.split(".")
                                            if len(parts) == 2 and len(parts[0]) > 1 and len(parts[1]) > 1:
                                                bounced_pattern = "{first}.{last}"
                                            elif len(parts) == 2 and len(parts[0]) == 1:
                                                bounced_pattern = "{f}.{last}"
                                        elif len(local_part) > 3 and local_part[0].isalpha():
                                            # Could be flast or firstlast
                                            bounced_pattern = "{first}{last}"
                                    if bounced_pattern:
                                        DomainHistory.record_failure(domain, bounced_pattern, email_lower)
                                        log.info(f"DomainHistory: recorded failed pattern '{bounced_pattern}' for {domain}")
                                    
                                    # Record bounce in circuit breaker
                                    CircuitBreaker.record_bounce()
                            except Exception as e:
                                log.debug(f"DomainHistory bounce recording failed: {e}")

                except Exception as e:
                    log.debug(f"Failed to process bounce msg {msg_id}: {e}")
                    continue

        except Exception as e:
            log.error(f"Bounce scanner failed: {e}")
            return set(bounced.keys())

        if newly_found:
            BounceScanner.save_bounced(bounced)
            log.info(f"Bounce scanner: {len(newly_found)} new bounces recorded")
            print(
                f"  Bounce scanner: {len(newly_found)} new bounce(s) → .local/bounced_emails.json"
            )
        else:
            log.info("Bounce scanner: no new bounces")

        # Record successful deliveries: emails sent 24h+ ago that didn't bounce
        try:
            from outreach.outreach_verifier import DomainHistory
            from outreach.outreach_config import PAT_A, PAT_B, PAT_C, SHEETS_CREDS, SPREADSHEET
            import gspread as _gs
            from oauth2client.service_account import ServiceAccountCredentials as _SAC
            scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
            creds = _SAC.from_json_keyfile_name(SHEETS_CREDS, scope)
            gc = _gs.authorize(creds)
            ws = gc.open(SPREADSHEET).worksheet("Outreach Tracker")
            data = ws.get_all_values()
            now = datetime.datetime.now()
            confirmed_count = 0
            for row in data[1:]:
                while len(row) < 15:
                    row.append("")
                sent_date = row[13].strip()  # Sent Date column
                hm_email = row[6].strip()
                rec_email = row[10].strip()
                if not sent_date:
                    continue
                # Check if sent 24h+ ago
                try:
                    from dateutil import parser as _dp
                    import warnings as _w
                    from dateutil.parser import UnknownTimezoneWarning as _UTW
                    with _w.catch_warnings():
                        _w.filterwarnings("ignore", category=_UTW)
                        sent_dt = _dp.parse(sent_date, tzinfos={
                            "ET": -18000, "EST": -18000, "EDT": -14400,
                            "PT": -28800, "PST": -28800, "PDT": -25200,
                            "CT": -21600, "CST": -21600, "CDT": -18000,
                            "MT": -25200, "MST": -25200, "MDT": -21600,
                        })
                    age_hours = (now - sent_dt).total_seconds() / 3600
                    if age_hours < 24:
                        continue
                except Exception as _swx:
                    from aggregator.swallowed import swallow as _s; _s('bounce_scanner.scan', _swx)
                    continue
                # If email was sent and NOT bounced, confirm the pattern
                for email in [hm_email, rec_email]:
                    if not email or "@" not in email:
                        continue
                    email_lower = email.lower().strip()
                    if email_lower in bounced:
                        continue  # This one bounced, skip
                    domain = email_lower.split("@")[1]
                    local = email_lower.split("@")[0]
                    pattern = None
                    if "." in local:
                        parts = local.split(".")
                        if len(parts) == 2 and len(parts[0]) > 1 and len(parts[1]) > 1:
                            pattern = "{first}.{last}"
                        elif len(parts) == 2 and len(parts[0]) == 1:
                            pattern = "{f}.{last}"
                    elif "_" in local:
                        pattern = "{first}_{last}"
                    if pattern:
                        existing = DomainHistory.get_confirmed_pattern(domain)
                        if not existing:
                            DomainHistory.record_success(domain, pattern, email_lower)
                            confirmed_count += 1
                            # FIX 2: also update PatternCache so find() uses it immediately
                            try:
                                from outreach.outreach_data import PatternCache
                                PatternCache().store(domain, pattern)
                                log.info(f"PatternCache updated: {domain} -> {pattern}")
                            except Exception as _pce:
                                log.debug(f"PatternCache update failed: {_pce}")
            if confirmed_count > 0:
                log.info(f"DomainHistory: confirmed {confirmed_count} successful patterns from sent emails")
                print(f"  Domain patterns: {confirmed_count} confirmed from successful deliveries")
        except Exception as e:
            log.debug(f"Delivery confirmation failed (non-fatal): {e}")

        return set(bounced.keys())

    @staticmethod
    def _get_header(msg: dict, name: str) -> str:
        headers = msg.get("payload", {}).get("headers", [])
        for h in headers:
            if h.get("name", "").lower() == name.lower():
                return h.get("value", "")
        return ""

    @staticmethod
    def _extract_failed_email(msg: dict) -> str:
        """
        Extract the failed recipient email from a bounce message.
        Three methods in order of reliability:
          1. DSN MIME part: "Final-Recipient: rfc822; email@domain"  (RFC 3464)
          2. Natural language patterns (Outlook/Gmail human-readable body)
          3. Email address near bounce error codes
        """
        payload = msg.get("payload", {})
        all_text = BounceScanner._collect_text_parts(payload)

        for text in all_text:
            # Method 1: RFC 3464 DSN standard header — most reliable
            m = re.search(
                r"Final-Recipient\s*:\s*rfc822\s*;\s*([\w.+%-]+@[\w.-]+\.\w+)",
                text,
                re.I,
            )
            if m:
                return m.group(1).strip()

            # Method 2: Outlook/Gmail natural language
            m = re.search(
                r"(?:your message to|message to)\s+<?([\w.+%-]+@[\w.-]+\.\w+)>?",
                text,
                re.I,
            )
            if m:
                return m.group(1).strip()

            # Method 3: Email address on its own line near SMTP error codes
            lines = text.split("\n")
            for i, line in enumerate(lines):
                line = line.strip()
                if re.match(r"^[\w.+%-]+@[\w.-]+\.\w+$", line):
                    context = " ".join(lines[max(0, i - 3) : i + 3]).lower()
                    if any(
                        kw in context
                        for kw in [
                            "550",
                            "5.1",
                            "not exist",
                            "no such user",
                            "unknown user",
                            "invalid",
                            "rejected",
                            "failed",
                            "does not exist",
                            "address not found",
                        ]
                    ):
                        return line

        # Method 4: Any email near bounce keywords in full body
        full_text = " ".join(all_text)
        full_lower = full_text.lower()
        bounce_keywords = [
            "couldn't be delivered",
            "could not be delivered",
            "delivery failed",
            "delivery failure",
            "not delivered",
            "undeliverable",
            "no such user",
            "user unknown",
            "address not found",
            "does not exist",
        ]
        for kw in bounce_keywords:
            idx = full_lower.find(kw)
            if idx >= 0:
                window = full_text[max(0, idx - 200) : idx + 200]
                emails = re.findall(r"[\w.+%-]+@[\w.-]+\.\w+", window)
                for email in emails:
                    el = email.lower()
                    # Skip bounce infrastructure addresses
                    if not any(
                        skip in el
                        for skip in [
                            "mailer-daemon",
                            "postmaster",
                            "noreply",
                            "no-reply",
                            "googlemail.com",
                            "google.com",
                            "microsoft.com",
                            "amazonses.com",
                            "bounce",
                            "donotreply",
                        ]
                    ):
                        return email

        return ""

    @staticmethod
    def _collect_text_parts(payload: dict) -> list:
        """Recursively collect all decoded text from a Gmail message payload."""
        texts = []
        body = payload.get("body", {})
        data = body.get("data", "")
        if data:
            try:
                decoded = base64.urlsafe_b64decode(data + "==").decode(
                    "utf-8", errors="replace"
                )
                texts.append(decoded)
            except Exception as _swx:
                from aggregator.swallowed import swallow as _s; _s('bounce_scanner._collect_text_parts', _swx)
        for part in payload.get("parts", []):
            texts.extend(BounceScanner._collect_text_parts(part))
        return texts
