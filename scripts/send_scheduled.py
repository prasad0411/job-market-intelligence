#!/usr/bin/env python3
"""
Send scheduled outreach emails from Outlook 'Scheduled Outreach' folder.
Drafts are created by outreach_mailer.py with X-Send-At / X-Company headers.
Run every 15 min via launchd — zero manual intervention needed.
"""
import sys, os, datetime, time, logging, json, re
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from outreach.outreach_config import (
    MS_SENDER_EMAIL, MS_CLIENT_ID, MS_AUTHORITY, MS_SCOPES, MS_TOKEN_FILE,
    SHEETS_CREDS, SPREADSHEET, OUTREACH_TAB, C,
)
from outreach.outreach_data import _cl
from outreach.brain import Brain
import requests as _req, gspread
from oauth2client.service_account import ServiceAccountCredentials
from aggregator.atomic_json import write_json as _atomic_write_json

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(message)s")

# ── smart bounce-retry ────────────────────────────────────────────────────────

def _email_to_pattern(email: str) -> str:
    """Reverse-engineer the pattern used to generate this email."""
    local = email.split("@")[0].lower()
    if "." in local:
        parts = local.split(".")
        if len(parts[0]) == 1:
            return "{f}.{last}"
        return "{first}.{last}"
    if "_" in local:
        return "{first}_{last}"
    if "-" in local:
        return "{first}-{last}"
    return "{first}"


def _next_best_email(original_email: str, name: str, brain) -> str | None:
    """
    Given a bounced email, ask Brain for the next best pattern for that domain.
    Returns a new candidate email or None if no alternatives exist.
    """
    if not original_email or "@" not in original_email:
        return None
    domain = original_email.split("@")[1].lower()
    bad_pattern = _email_to_pattern(original_email)

    # Record this pattern as failed in Brain
    brain.record_pattern_failure(domain, bad_pattern)

    # Parse name
    try:
        from outreach.outreach_data import NameParser
        parsed = NameParser.parse(name)
        if not parsed:
            return None
        first = parsed.get("first", "").lower()
        last = parsed.get("last", "").lower()
        f = first[0] if first else ""
        if not first or not last:
            return None
    except Exception:
        return None

    # All possible patterns
    all_patterns = [
        "{first}.{last}", "{f}.{last}", "{first}{last}",
        "{first}_{last}", "{first}-{last}", "{first}",
        "{last}.{first}", "{last}{first}",
    ]

    # Rank by Brain posterior, excluding already-failed patterns
    ranked = brain.rank_patterns_for(domain, all_patterns)

    # Generate email from top-ranked pattern
    for pattern in ranked:
        candidate = (
            pattern
            .replace("{first}", first)
            .replace("{last}", last)
            .replace("{f}", f)
        )
        if candidate != original_email.split("@")[0]:
            return f"{candidate}@{domain}"
    return None


# send_fail_counts was a 4th failure-tracking mechanism, written and
# never called. record_pattern_failure (wrong email format), bounce_log
# (hard bounces) and bounced_emails (dead addresses) already cover this,
# and they ARE wired. A 4th tracker means a 4th source of truth that can
# disagree with the other three. Removed - the brain key was never even
# created and the file held an empty {}.




_LOCAL = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".local")
SENT_LOG_FILE = os.path.join(_LOCAL, "sent_log.json")

SCHEDULED_FOLDER  = "Scheduled Outreach"
COLD_EMAILING_FOLDER = "Cold Emailing"

# ── auth ──────────────────────────────────────────────────────────────────────

def _get_token():
    import msal
    cache = msal.SerializableTokenCache()
    if os.path.exists(MS_TOKEN_FILE):
        cache.deserialize(open(MS_TOKEN_FILE).read())
    app = msal.PublicClientApplication(MS_CLIENT_ID, authority=MS_AUTHORITY, token_cache=cache)
    accts = app.get_accounts()
    result = app.acquire_token_silent(MS_SCOPES, account=accts[0]) if accts else None
    if not result or "access_token" not in result:
        raise Exception("MS token expired — run: python3 scripts/test_ms_auth.py")
    if cache.has_state_changed:
        # File lock prevents race condition when multiple jobs refresh token simultaneously
        import fcntl as _fcntl
        with open(MS_TOKEN_FILE + ".lock", "w") as _lf:
            _fcntl.flock(_lf, _fcntl.LOCK_EX)
            open(MS_TOKEN_FILE, "w").write(cache.serialize())
            _fcntl.flock(_lf, _fcntl.LOCK_UN)
            _fcntl.flock(_lf, _fcntl.LOCK_UN)
    return result["access_token"]

# ── folder helpers ────────────────────────────────────────────────────────────

_FOLDER_CACHE = {}

def _get_folder_id(token, name):
    if name in _FOLDER_CACHE:
        return _FOLDER_CACHE[name]
    resp = _req.get(
        f"https://graph.microsoft.com/v1.0/users/{MS_SENDER_EMAIL}/mailFolders",
        headers={"Authorization": f"Bearer {token}"},
        params={"$top": 50}, timeout=10,
    )
    if resp.status_code == 200:
        for f in resp.json().get("value", []):
            _FOLDER_CACHE[f["displayName"]] = f["id"]
    return _FOLDER_CACHE.get(name)


def _ensure_folder(token, name):
    fid = _get_folder_id(token, name)
    if fid:
        return fid
    resp = _req.post(
        f"https://graph.microsoft.com/v1.0/users/{MS_SENDER_EMAIL}/mailFolders",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={"displayName": name}, timeout=10,
    )
    if resp.status_code in (200, 201):
        fid = resp.json()["id"]
        _FOLDER_CACHE[name] = fid
        log.info(f"Created Outlook folder: {name}")
        return fid
    raise Exception(f"Could not create folder '{name}': {resp.text[:100]}")


def _get_drafts_in_folder(token, folder_id):
    msgs, url = [], (
        f"https://graph.microsoft.com/v1.0/users/{MS_SENDER_EMAIL}"
        f"/mailFolders/{folder_id}/messages"
        f"?$top=50&$select=id,subject,toRecipients,internetMessageHeaders,createdDateTime"
    )
    while url:
        resp = _req.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=15)
        if resp.status_code != 200:
            break
        data = resp.json()
        msgs.extend(data.get("value", []))
        url = data.get("@odata.nextLink")
    return msgs


def _header(msg, name):
    for h in msg.get("internetMessageHeaders", []) or []:
        if h.get("name", "").lower() == name.lower():
            return h.get("value", "")
    return ""

# ── timezone helpers ──────────────────────────────────────────────────────────

def _should_send(send_at_iso):
    if not send_at_iso:
        return False
    try:
        # Try ISO format first: 2026-03-23T10:00:00
        try:
            send_at = datetime.datetime.fromisoformat(send_at_iso)
        except Exception:
            # Fallback: human format "Mar 23, 10:00 AM ET"
            clean = re.sub(r"\s*(ET|EST|EDT|PT|CT|MT)\s*$", "", send_at_iso.strip())
            year = datetime.datetime.now().year
            send_at = datetime.datetime.strptime(f"{clean} {year}", "%b %d, %I:%M %p %Y")
            # Treat as US Eastern
            from zoneinfo import ZoneInfo
            send_at = send_at.replace(tzinfo=ZoneInfo("America/New_York"))
        if send_at.tzinfo is None:
            send_at = send_at.replace(tzinfo=datetime.timezone.utc)
        now = datetime.datetime.now(datetime.timezone.utc)
        if (now - send_at).total_seconds() > 7 * 24 * 3600:
            return False  # stale — older than 7 days
        return now >= send_at
    except Exception:
        return False

# ── send + move ───────────────────────────────────────────────────────────────

def _send_draft(token, msg_id):
    resp = _req.post(
        f"https://graph.microsoft.com/v1.0/users/{MS_SENDER_EMAIL}/messages/{msg_id}/send",
        headers={"Authorization": f"Bearer {token}", "Content-Length": "0"},
        timeout=30,
    )
    if resp.status_code not in (200, 202):
        raise Exception(f"Send failed {resp.status_code}: {resp.text[:150]}")


def _move(token, msg_id, folder_id):
    resp = _req.post(
        f"https://graph.microsoft.com/v1.0/users/{MS_SENDER_EMAIL}/messages/{msg_id}/move",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={"destinationId": folder_id}, timeout=10,
    )
    return resp.status_code in (200, 201)

# ── sheet update ──────────────────────────────────────────────────────────────

def _get_sheets():
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_name(SHEETS_CREDS, scope)
    return gspread.authorize(creds).open(SPREADSHEET).worksheet(OUTREACH_TAB)


def _mark_sent(ws, company, title, date_str):
    try:
        data = ws.get_all_values()
        for i, row in enumerate(data[1:], start=2):
            if (len(row) > max(C["company"], C["title"]) and
                    row[C["company"]].strip().lower() == company.lower() and
                    row[C["title"]].strip().lower() == title.lower() and
                    not row[C["sent_dt"]].strip()):
                ws.update_acell(f"{_cl(C['sent_dt'])}{i}", date_str)
                time.sleep(0.5)
                return
    except Exception as e:
        log.debug(f"Sheet mark-sent failed: {e}")


# ── two-phase claim ───────────────────────────────────────────────────────────
# Sends are not atomic with the sheet write. _send_draft() succeeds, then the
# folder-move block runs, and only then is the date recorded. A crash in that
# gap used to leave the row blank and eligible for a re-send. The claim closes
# the window: written before the send, replaced by the date after.

CLAIM_PREFIX = "SENDING"
CLAIM_STALE_MIN = 30


def _claim_state(cell, now=None):
    """blank -> free, fresh claim -> locked, old claim -> stale, else sent."""
    s = str(cell or "").strip()
    if not s:
        return "free"
    if not s.startswith(CLAIM_PREFIX):
        return "sent"
    now = now or datetime.datetime.now()
    try:
        ts = datetime.datetime.fromisoformat(s.split("|", 1)[1])
    except Exception:
        return "stale"  # unparseable claim: let Graph decide
    return "stale" if (now - ts).total_seconds() > CLAIM_STALE_MIN * 60 else "locked"


def _find_row(ws, company, title):
    """Return (row_number, sent_dt_cell) for this company/title, or (None, None)."""
    try:
        data = ws.get_all_values()
    except Exception as e:
        log.debug(f"Row lookup failed: {e}")
        return None, None
    need = max(C["company"], C["title"], C["sent_dt"])
    for i, row in enumerate(data[1:], start=2):
        if (len(row) > need and
                row[C["company"]].strip().lower() == company.lower() and
                row[C["title"]].strip().lower() == title.lower()):
            return i, row[C["sent_dt"]]
    return None, None


def _sent_via_graph(token, to_email, subject, days_back=14):
    """Ask Sent Items whether this mail already went out.

    The sheet and sent_log.json are both caches; the mail folder is the only
    real record. Used to resolve a stale claim left by a crashed run.
    Returns True only on a positive match - a failed lookup returns False so
    an API problem never blocks a legitimate send.
    """
    try:
        since = (datetime.datetime.utcnow()
                 - datetime.timedelta(days=days_back)).strftime("%Y-%m-%dT%H:%M:%SZ")
        safe = subject.replace("'", "''")
        r = _req.get(
            f"https://graph.microsoft.com/v1.0/users/{MS_SENDER_EMAIL}"
            f"/mailFolders/sentitems/messages",
            headers={"Authorization": f"Bearer {token}"},
            params={"$filter": f"subject eq '{safe}' and sentDateTime ge {since}",
                    "$top": 25},
            timeout=10,
        )
        if r.status_code != 200:
            log.debug(f"Sent-items lookup HTTP {r.status_code}")
            return False
        for m in r.json().get("value", []):
            for rec in m.get("toRecipients", []):
                if rec["emailAddress"]["address"].lower() == to_email.lower():
                    return True
    except Exception as e:
        log.debug(f"Sent-items lookup failed: {e}")
    return False


def _claim_row(ws, company, title, token=None, to_email=None, subject=None):
    """Mark the row as in-flight. Returns row number, or None to skip sending."""
    row, cell = _find_row(ws, company, title)
    if row is None:
        return None
    state = _claim_state(cell)
    if state == "sent":
        log.info(f"Already sent per sheet: {company} | {title}")
        return None
    if state == "locked":
        log.warning(f"Row claimed by another run, skipping: {company} | {title}")
        return None
    if state == "stale":
        if token and to_email and subject and _sent_via_graph(token, to_email, subject):
            log.warning(f"Stale claim but mail is in Sent Items - recording: {company}")
            _confirm_row(ws, row, datetime.datetime.now().strftime("%b %d, %Y"))
            return None
        log.info(f"Stale claim, no mail found - retrying: {company} | {title}")
    try:
        ws.update_acell(f"{_cl(C['sent_dt'])}{row}",
                        f"{CLAIM_PREFIX}|{datetime.datetime.now().isoformat(timespec='seconds')}")
        time.sleep(0.5)
        return row
    except Exception as e:
        log.warning(f"Claim write failed for {company}: {e}")
        return None


def _confirm_row(ws, row, date_str):
    """Replace the claim with the real send date."""
    try:
        ws.update_acell(f"{_cl(C['sent_dt'])}{row}", date_str)
        time.sleep(0.5)
        return True
    except Exception as e:
        log.error(f"CONFIRM FAILED row {row} - mail sent, sheet still claimed: {e}")
        return False


def _release_row(ws, row):
    """Clear a claim after a send that never happened."""
    try:
        ws.update_acell(f"{_cl(C['sent_dt'])}{row}", "")
        time.sleep(0.5)
    except Exception as e:
        log.debug(f"Release failed row {row}: {e}")


# ── sent log ──────────────────────────────────────────────────────────────────

def _load_sl():
    try:
        if os.path.exists(SENT_LOG_FILE):
            return json.load(open(SENT_LOG_FILE))
    except Exception:
        pass
    return {}


def _save_sl(sl):
    try:
        _atomic_write_json(SENT_LOG_FILE, sl)
    except Exception as e:
        log.error(f"Sent log save: {e}")


def _is_dup(sl, email, subj):
    k = f"{email.lower()}||{subj.lower()}"
    ts = sl.get(k)
    if not ts:
        return False
    try:
        # 30 days, not 7: a contact re-entering the sheet after a week was
        # eligible for a second identical email. The sheet claim covers the
        # in-flight race; this covers the same person reappearing later.
        return (datetime.datetime.now() - datetime.datetime.fromisoformat(ts)).days < 30
    except Exception:
        return False


def _rec_sent(sl, email, subj):
    sl[f"{email.lower()}||{subj.lower()}"] = datetime.datetime.now().isoformat()

# ── main ──────────────────────────────────────────────────────────────────────


_LIVE_FINDER = None
def _get_live_finder():
    global _LIVE_FINDER
    if _LIVE_FINDER is None:
        from outreach.outreach_data import Credits
        from outreach.outreach_finder import Finder
        _LIVE_FINDER = Finder(Credits())
    return _LIVE_FINDER


def main():
    # Run applied trigger first — sets Extract=yes for newly Applied jobs
    try:
        from scripts.applied_trigger import run as _applied_trigger
        _at_count = _applied_trigger()
        if _at_count:
            print(f"  Applied trigger: {_at_count} rows set to Extract=yes")
    except Exception as _at_e:
        pass  # non-critical, don't block sending

    try:
        from zoneinfo import ZoneInfo
    except ImportError:
        from backports.zoneinfo import ZoneInfo

    now_et = datetime.datetime.now(ZoneInfo("America/New_York"))
    print(f"SEND SCHEDULED (Outlook Drafts)  {now_et.strftime('%b %d, %Y %I:%M %p ET')}")
    print("-" * 55)

    try:
        token = _get_token()
    except Exception as e:
        print(f"  ERROR: {e}"); return

    brain = Brain.get()
    sl    = _load_sl()

    # Load bounce lists — skip addresses confirmed bounced via NDR processor
    _BOUNCE_LOG = os.path.join(_LOCAL, "bounce_log.json")
    _BOUNCED_F  = os.path.join(_LOCAL, "bounced_emails.json")
    try:
        _bl = json.load(open(_BOUNCE_LOG)) if os.path.exists(_BOUNCE_LOG) else {}
        _be = json.load(open(_BOUNCED_F))  if os.path.exists(_BOUNCED_F)  else {}
    except Exception:
        _bl, _be = {}, {}
    # Also load pattern blacklist (bad-pattern domains that may not have hard-bounced)
    _PBL_F = os.path.join(_LOCAL, "pattern_blacklist.json")
    try:
        _pbl = json.load(open(_PBL_F)) if os.path.exists(_PBL_F) else {}
    except Exception:
        _pbl = {}
    all_bounced = set(list(_bl.keys()) + list(_be.keys()))
    _blacklisted_domains = {str(k).lower() for k in _pbl.keys()}
    # Load confirmed email patterns so we can flag unconfirmed-domain guesses
    _DPH_F = os.path.join(_LOCAL, "domain_pattern_history.json")
    try:
        _dph = json.load(open(_DPH_F)) if os.path.exists(_DPH_F) else {}
    except Exception:
        _dph = {}
    _confirmed_domains = {str(k).lower() for k, v in _dph.items()
                          if isinstance(v, dict) and v.get("confirmed_pattern")}
    if all_bounced:
        log.info(f"Bounce skip list: {len(all_bounced)} addresses loaded")
    # A/B verify mode: A=definitive-provider only (strict), B=also Reacher-safe-if-not-catchall
    try:
        import json as _json_m
        _mode_data = _json_m.load(open(os.path.join(_LOCAL, "send_verify_mode.json")))
        _VERIFY_MODE = _mode_data.get("mode", "A")
    except Exception:
        _VERIFY_MODE = "A"
    log.info(f"Verify mode: {_VERIFY_MODE}")
    log.info(f"Blacklisted domains: {len(_blacklisted_domains)} | "
             f"Confirmed-pattern domains: {len(_confirmed_domains)}")

    ws    = None  # lazy — only loaded if we actually send

    scheduled_fid = _ensure_folder(token, SCHEDULED_FOLDER)
    cold_fid      = _ensure_folder(token, COLD_EMAILING_FOLDER)

    # Adaptive send limit
    try:
        cb = brain._data.get("circuit_breaker", {})
        s, b = cb.get("sent_today", 0), cb.get("bounced_today", 0)
        rate = b / s if s >= 5 else 0.0
        max_send = 30 if rate <= 0.02 else 25 if rate <= 0.05 else 20 if rate <= 0.10 else 15
    except Exception:
        max_send = 15
    log.info(f"Adaptive send limit: {max_send}/run")

    drafts = _get_drafts_in_folder(token, scheduled_fid)
    print(f"  Found {len(drafts)} draft(s) in '{SCHEDULED_FOLDER}'")

    sent_n = skipped = failed = dedup = 0

    for msg in drafts:
        if sent_n >= max_send:
            skipped += 1; continue

        msg_id   = msg["id"]
        subject  = msg.get("subject", "")
        recips   = [r["emailAddress"]["address"]
                    for r in msg.get("toRecipients", [])
                    if r.get("emailAddress", {}).get("address")]
        to_email = recips[0] if recips else ""

        if not to_email:
            skipped += 1; continue

        send_at_iso  = _header(msg, "X-Send-At")
        company      = _header(msg, "X-Company")
        title        = _header(msg, "X-Job-Title")
        confidence   = _header(msg, "X-Confidence")

        # Skip very low confidence emails (pattern guess, unverified)
        try:
            conf_val = float(confidence) if confidence else 100.0
            if conf_val < 50:
                log.info(f"Low confidence skip: {to_email} conf={conf_val}")
                print(f"  ⊘ Skipped (low confidence {conf_val}): {to_email}")
                skipped += 1; continue
        except Exception:
            pass

        if _is_dup(sl, to_email, subject):
            log.debug(f"Dup: {to_email}"); dedup += 1; continue

        # Skip previously bounced addresses (learned from NDR processor)
        if to_email.lower() in all_bounced:
            log.info(f"Bounce skip: {to_email} (previously bounced)")
            print(f"  ⊘ Skipped (bounced): {to_email}")
            skipped += 1; continue
        _dom = to_email.split("@")[-1].lower()
        # Skip blacklisted-pattern domains
        if _dom in _blacklisted_domains:
            log.info(f"Blacklist skip: {to_email} (domain {_dom} blacklisted)")
            print(f"  ⊘ Skipped (blacklisted domain): {to_email}")
            skipped += 1; continue
        # Unconfirmed domain + low confidence: try LIVE verification before sending.
        # Correctness over volume — only send if the mailbox is actually verified.
        if _dom not in _confirmed_domains and conf_val < 70:
            _verified_ok = False
            try:
                from outreach.outreach_data import Credits
                from outreach.outreach_finder import Finder
                _fin = _get_live_finder()
                _vres = _fin.verifier.verify(to_email, _dom)
                _vconf = _vres.get("confidence", 0)
                _vsrc = _vres.get("source", "?")
                _vsrc_l = str(_vsrc).lower()
                _vdef = any(x in _vsrc_l for x in ("google", "gxlu", "microsoft", "365"))
                _vreacher_ok = ("reacher" in _vsrc_l and "catchall" not in _vsrc_l)
                _v_accept = (_vconf >= 85 and _vdef) or (_VERIFY_MODE == "B" and _vconf >= 80 and _vreacher_ok)
                if _v_accept:
                    _verified_ok = True
                    log.info(f"Live-verified {to_email} conf={_vconf} ({_vsrc}) - sending")
                    print(f"  ✓ Live-verified ({_vconf}, {_vsrc}): {to_email}")
                else:
                    # Single address failed. Try a full pattern SWEEP for this
                    # person+domain (right name, wrong format case). Reuses the
                    # finder's catch-all-guarded sweep; sends corrected addr if found.
                    _swept = None
                    try:
                        _local = to_email.split("@")[0]
                        _name_guess = _local.replace(".", " ").replace("_", " ").replace("-", " ").strip()
                        if _name_guess and len(_name_guess.split()) >= 2:
                            _sweep = _get_live_finder().find(_name_guess, company, job_url_domain=_dom)
                            _se = (_sweep.get("email") or "").lower()
                            _sc = _sweep.get("confidence", 0)
                            _ssrc = str(_sweep.get("source", "")).lower()
                            # Only trust DEFINITIVE provider confirmation for auto-send.
                            # Reacher "safe" alone false-positives on accept-then-bounce
                            # domains (e.g. coherent.com), so it is NOT sufficient.
                            _definitive = any(x in _ssrc for x in ("google", "gxlu", "microsoft", "365"))
                            _reacher_ok = ("reacher" in _ssrc and "catchall" not in _ssrc)
                            _sweep_accept = (_sc >= 85 and _definitive) or (_VERIFY_MODE == "B" and _sc >= 80 and _reacher_ok)
                            if _se and _se != to_email and _sweep_accept:
                                _swept = _se
                                log.info(f"Pattern sweep found {_se} conf={_sc} (was {to_email})")
                                print(f"  ↺ Sweep corrected {to_email} -> {_se} (conf={_sc})")
                    except Exception as _swe:
                        log.warning(f"Pattern sweep error for {to_email}: {_swe}")
                    if _swept:
                        to_email = _swept
                        _verified_ok = True
                    else:
                        log.info(f"Live-verify + sweep failed {to_email} conf={_vconf} ({_vsrc}) - skip")
                        print(f"  ⊘ Skipped (unverified + sweep failed): {to_email}")
            except Exception as _ve:
                log.warning(f"Live-verify error {to_email}: {_ve} - skip (safe)")
                print(f"  ⊘ Skipped (verify error, safe): {to_email}")
            if not _verified_ok:
                skipped += 1; continue

        # Pre-send MX check: verify domain can still receive email
        try:
            import dns.resolver as _dns
            _domain = to_email.split("@")[1]
            _dns.resolve(_domain, "MX", lifetime=5)
        except Exception as _mx_e:
            log.warning(f"MX check failed for {to_email}: {_mx_e} — skipping")
            print(f"  ⊘ Skipped (no MX): {to_email}")
            skipped += 1; continue

        if not _should_send(send_at_iso):
            log.debug(f"Not yet: {company} | send_at={send_at_iso}"); skipped += 1; continue

        print(f"  → {company} | {to_email} | {subject[:45]}")

        _claimed_row = None
        if company and title:
            if ws is None:
                try:
                    ws = _get_sheets(); time.sleep(1)
                except Exception as _we:
                    log.debug(f"Sheet open failed: {_we}")
            if ws is not None:
                _claimed_row = _claim_row(ws, company, title, token, to_email, subject)
                if _claimed_row is None:
                    _r, _c = _find_row(ws, company, title)
                    if _r is not None and _claim_state(_c) in ("sent", "locked"):
                        print(f"  ⊘ Skipped (already sent or in flight): {company}")
                        skipped += 1
                        continue

        try:
            _send_draft(token, msg_id)
            print(f"    ✓ Sent")
            sent_n += 1
            _rec_sent(sl, to_email, subject)
            # Confirm immediately: the folder-move block below is slow and the
            # row must not sit claimed while it runs.
            if _claimed_row is not None:
                _confirm_row(ws, _claimed_row, now_et.strftime("%b %d, %Y"))
            # Record pattern success in Brain — self-learning
            try:
                domain = to_email.split("@")[1]
                pattern = _email_to_pattern(to_email)
                brain.record_pattern_success(domain, pattern, to_email)
            except Exception:
                pass
            try: brain.cb_record_send()
            except Exception as _cb_e: log.debug(f'cb_record_send failed: {_cb_e}')

            # Move sent copy from Sent Items → Cold Emailing
            time.sleep(3)
            try:
                safe = subject.replace("'", "''")
                # Note: $orderby removed — causes 400 on some tenants
                si = _req.get(
                    f"https://graph.microsoft.com/v1.0/users/{MS_SENDER_EMAIL}"
                    f"/mailFolders/sentitems/messages",
                    headers={"Authorization": f"Bearer {token}"},
                    params={"$filter": f"subject eq '{safe}'", "$top": 10},
                    timeout=10,
                )
                moved = False
                if si.status_code == 200:
                    # Find most recent match for this recipient
                    candidates = []
                    for m in si.json().get("value", []):
                        rs = [r["emailAddress"]["address"].lower()
                              for r in m.get("toRecipients", [])]
                        if to_email.lower() in rs:
                            candidates.append(m)
                    if candidates:
                        # Sort by sentDateTime descending in Python
                        candidates.sort(
                            key=lambda x: x.get("sentDateTime", x.get("createdDateTime", "")),
                            reverse=True
                        )
                        if _move(token, candidates[0]["id"], cold_fid):
                            print(f"    ✓ Moved → '{COLD_EMAILING_FOLDER}'")
                            moved = True
                if not moved:
                    print(f"    ⚠ Could not move to '{COLD_EMAILING_FOLDER}' (will retry next run)")
            except Exception as me:
                log.debug(f"Move failed: {me}")

            # Update sheet (fallback: only if the claim path did not run)
            if company and title and _claimed_row is None:
                try:
                    if ws is None:
                        ws = _get_sheets(); time.sleep(1)
                    _mark_sent(ws, company, title, now_et.strftime("%b %d, %Y"))
                except Exception as se:
                    log.debug(f"Sheet update: {se}")

        except Exception as e:
            print(f"    ✗ {e}")
            failed += 1
            # Send raised: release the claim so the row is retried next run.
            if _claimed_row is not None:
                _release_row(ws, _claimed_row)
                _claimed_row = None
            # Smart bounce-retry: find next best pattern and queue new draft
            try:
                next_email = _next_best_email(to_email, company, brain)
                if next_email:
                    print(f"    ↻ Retrying with next pattern: {next_email}")
                    # Get the full draft message to clone it
                    clone_resp = _req.get(
                        f"https://graph.microsoft.com/v1.0/users/{MS_SENDER_EMAIL}/messages/{msg_id}",
                        headers={"Authorization": f"Bearer {token}"},
                        params={"$select": "subject,body,attachments,internetMessageHeaders"},
                        timeout=10,
                    )
                    if clone_resp.status_code == 200:
                        orig = clone_resp.json()
                        # Build new draft with corrected email
                        new_payload = {
                            "subject": orig.get("subject", subject),
                            "body": orig.get("body", {"contentType": "HTML", "content": ""}),
                            "toRecipients": [{"emailAddress": {"address": next_email}}],
                            "internetMessageHeaders": orig.get("internetMessageHeaders", []),
                            "isDraft": True,
                        }
                        # Update X-Send-At to now + 1 hour
                        import datetime as _dt
                        new_send_at = (_dt.datetime.now(_dt.timezone.utc) +
                                       _dt.timedelta(hours=1)).isoformat()
                        new_payload["internetMessageHeaders"] = [
                            h for h in new_payload["internetMessageHeaders"]
                            if h.get("name") != "X-Send-At"
                        ] + [{"name": "X-Send-At", "value": new_send_at}]
                        cr = _req.post(
                            f"https://graph.microsoft.com/v1.0/users/{MS_SENDER_EMAIL}/messages",
                            headers={"Authorization": f"Bearer {token}",
                                     "Content-Type": "application/json"},
                            json=new_payload, timeout=30,
                        )
                        if cr.status_code in (200, 201):
                            new_id = cr.json()["id"]
                            _req.post(
                                f"https://graph.microsoft.com/v1.0/users/{MS_SENDER_EMAIL}/messages/{new_id}/move",
                                headers={"Authorization": f"Bearer {token}",
                                         "Content-Type": "application/json"},
                                json={"destinationId": scheduled_fid}, timeout=10,
                            )
                            print(f"    ✓ Retry draft queued: {next_email}")
                        else:
                            print(f"    ✗ Retry draft failed: {cr.status_code}")
            except Exception as re_err:
                log.debug(f"Bounce retry failed: {re_err}")

        # Adaptive delay
        try:
            cb  = brain._data.get("circuit_breaker", {})
            s   = cb.get("sent_today", 0)
            b   = cb.get("bounced_today", 0)
            r   = b / s if s >= 5 else 0.0
            delay = 60 if r > 0.05 else (30 if s > 10 else 45)
        except Exception:
            delay = 45
        time.sleep(delay)

    _save_sl(sl)
    print("-" * 55)
    print(f"Sent:{sent_n}  Skipped:{skipped}  Failed:{failed}  Dedup:{dedup}")


if __name__ == "__main__":
    main()

