#!/usr/bin/env python3
"""
Outreach Pipeline

    python3 -m outreach              # Full: pull + extract + draft
    python3 -m outreach status       # Show status
    python3 -m outreach reset        # Reset API credits
"""

import sys, os, datetime, logging


def _notify(msg):
    """Post run summary to Slack if SLACK_WEBHOOK_URL is set in .env."""
    try:
        _root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        _env = os.path.join(_root, ".env")
        _url = ""
        if os.path.exists(_env):
            for ln in open(_env):
                ln = ln.strip()
                if ln.startswith("SLACK_WEBHOOK_URL="):
                    _url = ln.split("=", 1)[1].strip()
                    break
        if not _url:
            return
        import urllib.request, json as _j
        data = _j.dumps({"text": msg}).encode()
        req = urllib.request.Request(_url, data=data,
                                     headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=5)
    except Exception as _swx:
        from aggregator.swallowed import swallow as _s; _s('run_outreach._notify', _swx)


from outreach.outreach_config import LOG_FILE
from outreach.outreach_data import Sheets, Credits
from outreach.outreach_finder import Finder
from outreach.outreach_mailer import Drafter, Mailer
from outreach.bounce_scanner import BounceScanner

# Dedicated outreach audit log — tracks every send/block decision
import logging as _ol
_audit_log = _ol.getLogger("outreach_audit")
_audit_handler = _ol.FileHandler(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".local", "outreach_audit.log"))
_audit_handler.setFormatter(_ol.Formatter("%(asctime)s | %(message)s"))
_audit_log.addHandler(_audit_handler)
_audit_log.setLevel(_ol.INFO)
from outreach.outreach_verifier import CircuitBreaker, AUTO_SEND_THRESHOLD
from outreach.brain import Brain

_log_dir = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".local"
)
os.makedirs(_log_dir, exist_ok=True)

from logging.handlers import RotatingFileHandler

_fh = RotatingFileHandler(
    os.path.join(_log_dir, "outreach.log"),
    maxBytes=5 * 1024 * 1024,  # 5MB
    backupCount=3,
)
_fh.setLevel(logging.INFO)
_fh.setFormatter(
    logging.Formatter(
        "%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
)
logging.getLogger().addHandler(_fh)
logging.getLogger().setLevel(logging.DEBUG)
_con = logging.StreamHandler()
_con.setLevel(logging.WARNING)
_con.setFormatter(logging.Formatter("%(message)s"))
logging.getLogger().addHandler(_con)
# Suppress noisy third-party loggers
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("requests_oauthlib").setLevel(logging.WARNING)
logging.getLogger("googleapiclient").setLevel(logging.WARNING)
logging.getLogger("google_auth_oauthlib").setLevel(logging.WARNING)
logging.getLogger("googleapiclient").setLevel(logging.WARNING)
logging.getLogger("google_auth_oauthlib").setLevel(logging.WARNING)
log = logging.getLogger("outreach")


def phase_pull(sheets):
    return sheets.pull()


def phase_draft_existing(sheets, mailer):
    from outreach.outreach_data import _pad, C

    data = sheets.ws.get_all_values()
    sheets._p()
    stats = {"drafts": 0, "draft_failed": 0}
    for i, r in enumerate(data[1:], start=2):
        r = _pad(r)
        he = r[C["hm_email"]].strip()
        re_ = r[C["rec_email"]].strip()
        send_at = r[C["send_at"]].strip()
        co = r[C["company"]].strip()
        title = r[C["title"]].strip()
        jid = r[C["job_id"]].strip()
        # FIX 1b: Skip rows not marked for extraction
        extract_val = r[C["extract"]].strip().lower() if len(r) > C["extract"] else ""
        if extract_val != "yes":
            continue
        if (not he and not re_) or send_at:
            continue
        resume_type = sheets.get_resume_type(co, title)
        parts = []
        if he:
            hm_names = [
                n.strip() for n in r[C["hm_name"]].strip().split(",") if n.strip()
            ]
            hm_emails = [e.strip() for e in he.split(",") if e.strip()]
            for idx_h in range(max(len(hm_names), len(hm_emails))):
                name = (
                    hm_names[idx_h]
                    if idx_h < len(hm_names)
                    else hm_names[-1] if hm_names else co
                )
                email = hm_emails[idx_h] if idx_h < len(hm_emails) else None
                if not email:
                    continue
                draft = Drafter.draft(name, "hm", co, title, jid, resume_type=resume_type)
                location = sheets.get_location(co, title)
                sa, _ = sheets.compute_send_at(location)
                result = mailer.send(
                    email, draft["subject"], draft["body"], resume_type,
                    company=co, title=title, location=location, send_at_iso=sa,
                )
                if result["success"]:
                    parts.append(f"HM draft created ({name.split()[0]})")
                    stats["drafts"] += 1
                    try:
                        from outreach.brain import Brain
                        Brain.get().store_verified_contact(co, "hm", name, email, confidence=0.9)
                    except Exception as _swx:
                        from aggregator.swallowed import swallow as _s; _s('run_outreach.phase_draft_existing', _swx)
                elif "Duplicate" not in result.get("error", ""):
                    parts.append(f"HM draft failed ({name.split()[0]})")
                    stats["draft_failed"] += 1
        if re_:
            rec_names = [
                n.strip() for n in r[C["rec_name"]].strip().split(",") if n.strip()
            ]
            rec_emails = [e.strip() for e in re_.split(",") if e.strip()]
            for idx_r in range(max(len(rec_names), len(rec_emails))):
                name = (
                    rec_names[idx_r]
                    if idx_r < len(rec_names)
                    else rec_names[-1] if rec_names else co
                )
                email = rec_emails[idx_r] if idx_r < len(rec_emails) else None
                if not email:
                    continue
                draft = Drafter.draft(name, "rec", co, title, jid, resume_type=resume_type)
                location = sheets.get_location(co, title)
                sa, _ = sheets.compute_send_at(location)
                result = mailer.send(
                    email, draft["subject"], draft["body"], resume_type,
                    company=co, title=title, location=location, send_at_iso=sa,
                )
                if result["success"]:
                    parts.append(f"Rec draft created ({name.split()[0]})")
                    stats["drafts"] += 1
                    try:
                        from outreach.brain import Brain
                        Brain.get().store_verified_contact(co, "rec", name, email, confidence=0.9)
                    except Exception as _swx:
                        from aggregator.swallowed import swallow as _s; _s('run_outreach.phase_draft_existing', _swx)
                elif "Duplicate" not in result.get("error", ""):
                    parts.append(f"Rec draft failed ({name.split()[0]})")
                    stats["draft_failed"] += 1
        if parts:
            print(f"  {co}: {', '.join(parts)}")
        location = sheets.get_location(co, title)
        sa, sd = sheets.compute_send_at(location)
        sheets.write_send_at(
            i, sa
        )  # Don't write sent_date yet — written after actual send
    return stats


def phase_extract_and_draft(sheets, finder, mailer):
    # Auto-fill emails from Brain contacts before extraction
    try:
        filled = sheets.auto_fill_from_brain()
        if filled:
            print(f"  Brain auto-fill: {filled} contacts pre-populated")
    except Exception as _af_e:
        log.debug(f"Brain auto-fill skipped: {_af_e}")

    # Format sheet: green Extract=yes, LinkedIn search links
    try:
        formatted = sheets.format_outreach_sheet()
        if formatted:
            print(f"  Formatted {formatted} Extract cells + LinkedIn links")
    except Exception as _fmt_e:
        log.debug(f"Sheet formatting skipped: {_fmt_e}")

    rows = sheets.rows_for_extraction()
    if not rows:
        return {
            "extracted": 0,
            "extract_failed": 0,
            "processed": 0,
            "drafts": 0,
            "draft_failed": 0,
        }

    stats = {
        "extracted": 0,
        "extract_failed": 0,
        "processed": 0,
        "drafts": 0,
        "draft_failed": 0,
    }

    # Load bounce + failed pattern data ONCE before the loop (not per row)
    _all_bounced = set(BounceScanner.load_bounced().keys())
    # Load domain reputation for Layer 1 defense
    _domain_rep = {}
    _daily_pause = False
    try:
        import json as _json
        _rep_file = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".local", "domain_reputation.json")
        if os.path.exists(_rep_file):
            with open(_rep_file) as _rf:
                _domain_rep = _json.load(_rf)
            log.info(f"Domain reputation loaded: {sum(1 for d in _domain_rep.values() if d.get('blocked'))} blocked domains")
            # Check today's bounce rate
            _cb_file = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".local", "circuit_breaker.json")
            if os.path.exists(_cb_file):
                with open(_cb_file) as _cbf:
                    _cb = _json.load(_cbf)
                _today_sent = _cb.get("sent_today", 0)
                _today_bounced = _cb.get("bounced_today", 0)
                if _today_sent > 5 and _today_bounced > 0 and _today_bounced / _today_sent > 0.10:
                    log.warning(f"SAFETY PAUSE: {_today_bounced}/{_today_sent} bounced today ({_today_bounced*100//_today_sent}%)")
                    _daily_pause = True
    except Exception as _swx:
        from aggregator.swallowed import swallow as _s; _s('run_outreach.phase_extract_and_draft', _swx)
    _failed_pat_file = os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), ".local", "failed_patterns.json")
    _failed_pats = {}
    try:
        import json as _json
        if os.path.exists(_failed_pat_file):
            _failed_pats = _json.load(open(_failed_pat_file))
    except Exception as _swx:
        from aggregator.swallowed import swallow as _s; _s('run_outreach.phase_extract_and_draft', _swx)
    _dh_file = os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), ".local", "domain_pattern_history.json")
    _dh_data = {}
    try:
        if os.path.exists(_dh_file):
            _dh_data = _json.load(open(_dh_file))
    except Exception as _swx:
        from aggregator.swallowed import swallow as _s; _s('run_outreach.phase_extract_and_draft', _swx)

    for row in rows:
        rn = row["row"]
        stats["processed"] += 1
        hm_res = rec_res = None
        parts = []

        jud = (
            sheets.get_job_url_domain(row["co"], row["title"])
            if hasattr(sheets, "get_job_url_domain")
            else ""
        )

        def _is_blocked(email):
            if not email or "@" not in email:
                return False
            # Safety valve: if daily bounce rate >10%, block everything
            if _daily_pause:
                log.info(f"Pre-send block: {email} — daily bounce rate too high, pausing")
                return True
            el = email.lower().strip()
            # Check 1: known bounced email addresses
            if el in _all_bounced:
                log.info(f"Pre-send block: {email} is in bounce cache")
                _audit_log.info(f"BLOCKED | {email} | bounce_cache | exact email previously bounced")
                return True
            domain = el.split("@")[1]
            local = el.split("@")[0]
            # Check 1b: domain reputation — block entire domain if 2+ bounces
            _rep = _domain_rep.get(domain, {})
            if _rep.get("blocked"):
                log.info(f"Pre-send block: {email} — domain {domain} blacklisted ({_rep.get('bounces', 0)} bounces)")
                _audit_log.info(f"BLOCKED | {email} | domain_reputation | {domain} has {_rep.get('bounces', 0)} bounces, score {_rep.get('score', 0)}")
                return True
            # Check 2: failed_patterns.json (local parts and pattern strings)
            for entry in _failed_pats.get(domain, []):
                if entry == local:
                    log.info(f"Pre-send block: {email} matches failed local in failed_patterns.json")
                    return True
                # entry might be a pattern string like {first}.{last} — skip those here
            # Check 3: domain_pattern_history.json failed patterns (pre-loaded above)
            try:
                _dh = _dh_data
                if _dh:
                    _entry = _dh.get(domain, {})
                    _failed_domain_pats = _entry.get("failed_patterns", [])
                    # Detect pattern of this email
                    _parts = local.split(".")
                    if len(_parts) == 2 and len(_parts[0]) > 1 and len(_parts[1]) > 1:
                        _pat = "{first}.{last}"
                    elif len(_parts) == 2 and len(_parts[0]) == 1:
                        _pat = "{f}.{last}"
                    elif "_" in local:
                        _pat = "{first}_{last}"
                    elif "-" in local:
                        _pat = "{first}-{last}"
                    else:
                        _pat = None
                    if _pat and _pat in _failed_domain_pats:
                        log.info(f"Pre-send block: {email} pattern '{_pat}' in domain_history failed list")
                        _audit_log.info(f"BLOCKED | {email} | domain_history | pattern '{_pat}' failed at {domain}")
                        return True
            except Exception as _dhe:
                log.debug(f"domain_history check failed: {_dhe}")
            return False

        if row["need_h"]:
            # Support multiple comma-separated HM names
            hm_names = [n.strip() for n in row["hn"].split(",") if n.strip()]
            hm_emails = []
            hm_failed = False
            for hm_name in hm_names:
                # Brain pre-check: skip 8-layer discovery if we have a verified contact
                _known_hm = None
                try:
                    from outreach.brain import Brain
                    _known_hm = Brain.get().get_verified_contact(row["co"], "hm")
                except Exception as _swx:
                    from aggregator.swallowed import swallow as _s; _s('run_outreach.phase_extract_and_draft', _swx)
                if _known_hm:
                    hm_res = {"email": _known_hm["email"], "confidence": _known_hm["confidence"],
                               "source": "brain_cache", "name": _known_hm.get("name","")}
                    log.info(f"  Brain cache hit for {row['co']} HM: {_known_hm['email']}")
                else:
                    # Skip Google search placeholder URLs — they're not real LinkedIn profiles
                    _hli = row["hli"] if row["hli"] and "linkedin.com/in/" in row["hli"] else ""
                    hm_res = finder.find(hm_name, row["co"], _hli, job_url_domain=jud)
                if hm_res["email"]:
                    if _is_blocked(hm_res["email"]):
                        log.warning(f"Blocked pre-send: {hm_res['email']} for {row['co']}")
                        hm_failed = True
                        stats["extract_failed"] += 1
                    else:
                        hm_emails.append(hm_res["email"])
                        stats["extracted"] += 1
                        # Store in Brain immediately after discovery
                        try:
                            from outreach.brain import Brain
                            Brain.get().store_verified_contact(
                                row["co"], "hm",
                                hm_res.get("name", hm_name or ""),
                                hm_res["email"],
                                confidence=hm_res.get("confidence", 0.7)
                            )
                        except Exception as _swx:
                            from aggregator.swallowed import swallow as _s; _s('run_outreach.phase_extract_and_draft', _swx)
                else:
                    hm_failed = True
                    stats["extract_failed"] += 1
            if hm_emails:
                sheets.write_email(rn, "hm", ", ".join(hm_emails), hm_res["source"])
                # Write confidence (use highest confidence from HM results)
                hm_conf = hm_res.get("confidence", 0)
                if hm_conf > 0:
                    sheets.write_confidence(rn, hm_conf)
                parts.append("HM email extracted")
            elif hm_failed:
                sheets.write_error(rn, f"HM: {hm_res['error']}")
                parts.append("HM email failed")

        if row["need_r"]:
            # Support multiple comma-separated Rec names
            _rn_raw = row["rn"] or row.get("rli", "")
            rec_names = [n.strip() for n in _rn_raw.split(",") if n.strip()]
            rec_emails = []
            rec_failed = False
            for rec_name in rec_names:
                _rli = row["rli"] if row["rli"] and "linkedin.com/in/" in row["rli"] else ""
                rec_res = finder.find(
                    rec_name, row["co"], _rli, job_url_domain=jud
                )
                if rec_res["email"]:
                    if _is_blocked(rec_res["email"]):
                        log.warning(f"Blocked pre-send: {rec_res['email']} for {row['co']}")
                        rec_failed = True
                        stats["extract_failed"] += 1
                    else:
                        rec_emails.append(rec_res["email"])
                        stats["extracted"] += 1
                        # Store in Brain immediately after discovery
                        try:
                            from outreach.brain import Brain
                            Brain.get().store_verified_contact(
                                row["co"], "rec",
                                rec_res.get("name", rec_name or ""),
                                rec_res["email"],
                                confidence=rec_res.get("confidence", 0.7)
                            )
                        except Exception as _swx:
                            from aggregator.swallowed import swallow as _s; _s('run_outreach.phase_extract_and_draft', _swx)
            if rec_emails:
                sheets.write_email(rn, "rec", ", ".join(rec_emails), rec_res["source"])
                # Write confidence (use lowest of HM and Rec — weakest link)
                rec_conf = rec_res.get("confidence", 0)
                existing_conf = hm_res.get("confidence", 0) if hm_res else 0
                final_conf = min(existing_conf, rec_conf) if existing_conf > 0 else rec_conf
                if final_conf > 0:
                    sheets.write_confidence(rn, final_conf)
                parts.append("Recruiter email extracted")
            elif rec_failed:
                sheets.append_error(rn, f"REC: {rec_res['error']}")
                parts.append("Recruiter email failed")
                stats["extract_failed"] += 1

        if parts:
            print(f"  {row['co']}: {', '.join(parts)}")

        hm_e = row.get("he") or (hm_res["email"] if hm_res else "")
        rec_e = row.get("re") or (rec_res["email"] if rec_res else "")
        # Block drafts for Low confidence emails
        from outreach.outreach_verifier import MANUAL_REVIEW_THRESHOLD
        hm_conf_val = hm_res.get("confidence", 0) if hm_res else 0
        rec_conf_val = rec_res.get("confidence", 0) if rec_res else 0
        if hm_e and hm_conf_val > 0 and hm_conf_val < MANUAL_REVIEW_THRESHOLD:
            log.info(f"Skipping HM draft for {row['co']}: Low confidence ({hm_conf_val})")
            hm_e = ""
        if rec_e and rec_conf_val > 0 and rec_conf_val < MANUAL_REVIEW_THRESHOLD:
            log.info(f"Skipping Rec draft for {row['co']}: Low confidence ({rec_conf_val})")
            rec_e = ""
        jid = row.get("jid", "")
        resume_type = sheets.get_resume_type(row["co"], row["title"])

        draft_parts = []
        if hm_e:
            hm_draft = Drafter.draft(
                row["hn"] or row["rn"], "hm", row["co"], row["title"], jid
            )
            _loc = sheets.get_location(row["co"], row["title"])
            _sa, _ = sheets.compute_send_at(_loc)
            _hm_conf = int(round(float(hm_res.get("confidence", 100)))) if hm_res else 100
            result = mailer.send(
                hm_e, hm_draft["subject"], hm_draft["body"], resume_type,
                company=row["co"], title=row["title"], location=_loc, send_at_iso=_sa,
                confidence=_hm_conf,
            )
            if result["success"]:
                draft_parts.append("HM draft created")
                stats["drafts"] += 1
                # Store verified contact so we never re-email same person
                try:
                    from outreach.brain import Brain
                    Brain.get().store_verified_contact(
                        row["co"], "hm", row.get("hn",""), hm_e, confidence=0.9)
                except Exception as _swx:
                    from aggregator.swallowed import swallow as _s; _s('run_outreach.phase_extract_and_draft', _swx)
            else:
                if "Duplicate" not in result["error"]:
                    draft_parts.append("HM draft failed")
                    stats["draft_failed"] += 1
                else:
                    draft_parts.append("HM draft exists")

        if rec_e:
            rec_draft = Drafter.draft(
                row["rn"] or row["hn"], "rec", row["co"], row["title"], jid
            )
            _loc = sheets.get_location(row["co"], row["title"])
            _sa, _ = sheets.compute_send_at(_loc)
            _rec_conf = int(round(float(rec_res.get("confidence", 100)))) if rec_res else 100
            result = mailer.send(
                rec_e, rec_draft["subject"], rec_draft["body"], resume_type,
                company=row["co"], title=row["title"], location=_loc, send_at_iso=_sa,
                confidence=_rec_conf,
            )
            if result["success"]:
                draft_parts.append("Recruiter draft created")
                stats["drafts"] += 1
                try:
                    from outreach.brain import Brain
                    Brain.get().store_verified_contact(
                        row["co"], "rec", row.get("rn",""), rec_e, confidence=0.9)
                except Exception as _swx:
                    from aggregator.swallowed import swallow as _s; _s('run_outreach.phase_extract_and_draft', _swx)
            else:
                if "Duplicate" not in result["error"]:
                    draft_parts.append("Recruiter draft failed")
                    stats["draft_failed"] += 1
                else:
                    draft_parts.append("Recruiter draft exists")

        if draft_parts:
            print(f"  {row['co']}: {', '.join(draft_parts)}")

        if hm_e or rec_e:
            location = sheets.get_location(row["co"], row["title"])
            sa, sd = sheets.compute_send_at(location)
            sheets.write_send_at(
                rn, sa
            )  # Don't write sent_date — written after actual send

    return stats


def status(sheets, credits, mailer):
    print(f"  Extraction queue: {len(sheets.rows_for_extraction())}")
    c = mailer.capacity()
    print(f"  Capacity: {c['daily']} daily, {c['hourly']} hourly")


def main():
    now = datetime.datetime.now().strftime("%d %B, %Y")
    cmd = sys.argv[1].lower() if len(sys.argv) > 1 else "full"

    if cmd == "reset":
        Credits().reset_all()
        print("Credits reset.")
        return

    print(f"OUTREACH PIPELINE  {now}")
    print("-" * 40)

    cr = Credits()
    sh = Sheets()
    fi = Finder(cr)
    ma = Mailer(cr)
    # Self-heal: verify MS token is valid before doing any work
    # _ms_precheck() already called in Mailer.__init__ but verify result
    if not ma._ms_access_token:
        import sys as _sys
        if _sys.stdin.isatty():
            print("  MS token needs re-auth. Follow the prompt below:")
        else:
            print("  WARNING: MS token unavailable — emails will fail. Check your email for alert.")
            log.warning("Outreach run started with no MS token")

    if cmd == "status":
        status(sh, cr, ma)
        return

    # Circuit breaker check
    can_send, cb_reason = CircuitBreaker.can_send()
    b = Brain.get()
    if not can_send:
        print(f"  Circuit breaker TRIPPED: {cb_reason}")
        print("  No new emails will be drafted. Fix the issue first.")
        print(f"  Status: {CircuitBreaker.status()}")
        if b.cb_should_alert_trip():
            b.send_email_alert(
                "🚨 Circuit breaker TRIPPED — outreach paused",
                f"Circuit breaker tripped: {cb_reason}\n\nStatus: {CircuitBreaker.status()}\n\n"
                f"No emails will be sent until you manually reset.\n"
                f"Check .local/outreach.log for details."
            )
            b.cb_record_trip_alert()
    else:
        print(f"  Circuit breaker: {CircuitBreaker.status()}")
        if b.cb_should_pre_warn():
            cb_state = b._data["circuit_breaker"]
            sent = cb_state.get("sent_today", 0)
            bounced = cb_state.get("bounced_today", 0)
            rate = bounced / sent if sent else 0
            b.send_email_alert(
                f"⚠️ Bounce rate warning: {rate:.0%} ({bounced}/{sent})",
                f"Bounce rate is {rate:.0%} — circuit breaker trips at 30%.\n\n"
                f"Sent today: {sent}\nBounced today: {bounced}\n\n"
                f"Consider pausing outreach or reviewing email patterns."
            )
            b.cb_record_pre_warn()
            log.warning(f"Pre-trip warning sent: bounce rate {rate:.0%}")

    print("Scanning for bounced emails...")
    try:
        svc = ma._service()
        bounced_emails = BounceScanner.scan(svc, days_back=14)
        if bounced_emails:
            ma.set_bounced(bounced_emails)
            sh.flag_bounced_rows(bounced_emails)
    except Exception as e:
        log.warning(f"Bounce scan failed (non-fatal): {e}")

    # A missing provider key is the difference between a working run and one
    # that extracts nothing, and it is otherwise invisible in the output.
    try:
        from outreach.outreach_config import key as _pk
        _missing = [_n for _n in ("APOLLO_API_KEY", "HUNTER_API_KEY",
                                  "SNOV_API_KEY", "PROSPEO_API_KEY")
                    if not (_pk(_n) or "").strip()]
        if len(_missing) == 4:
            log.warning("NO EMAIL PROVIDER API KEY SET - email lookups will "
                        "all fail. Set at least one in .env: %s",
                        ", ".join(_missing))
            print("  WARNING: no email provider API key set; 0 emails will be found")
    except Exception as _ke:
        log.debug("Provider key check skipped: %s", _ke)

    phase_pull(sh)
    sh.sync_with_valid()

    # Auto-set Extract=yes based on location/sponsorship/pattern signals
    try:
        import subprocess, sys as _sys
        subprocess.run([_sys.executable, "scripts/auto_extract.py"], timeout=120)
    except Exception as _ae:
        log.debug(f"auto_extract skipped: {_ae}")

    # Mark delivered emails (sent 12h+ ago, no bounce)
    try:
        all_bounced = set(BounceScanner.load_bounced().keys())
        sh.mark_delivered(all_bounced)
    except Exception as e:
        log.debug(f"Delivery marking skipped: {e}")
    s = phase_extract_and_draft(sh, fi, ma)

    sh.populate_linkedin_msgs()

    d = phase_draft_existing(sh, ma)
    s["drafts"] += d["drafts"]
    s["draft_failed"] += d["draft_failed"]
    # 62 handlers in outreach/ record counts here. Without this call they
    # record into nothing - the aggregator prints its summary, outreach did
    # not, which is why its silent failures stayed silent.
    try:
        from aggregator.swallowed import report as _sw_report
        _sw_report(verbose=True)
    except Exception as _sre:
        log.debug(f"Swallow report failed: {_sre}")

    print("-" * 40)
    print(
        f"Email IDs: {s['extracted']} extracted, {s['extract_failed']} failed ({s['processed']} processed)"
    )
    print(f"Drafts:    {s['drafts']} created, {s['draft_failed']} failed")
    _notify(
        f":outbox_tray: *Outreach run complete* ({now})\n"
        f"Extracted: {s['extracted']} emails, {s['extract_failed']} failed\n"
        f"Drafts: {s['drafts']} created, {s['draft_failed']} failed\n"
        f"Circuit breaker: {CircuitBreaker.status()}"
    )


if __name__ == "__main__":
    main()

