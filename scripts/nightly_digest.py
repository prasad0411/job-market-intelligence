#!/usr/bin/env python3
"""Nightly digest — sends run summary to prasadckanade@gmail.com"""
import sys, os, datetime, json, logging, re, sqlite3
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from outreach.outreach_config import (
    MS_SENDER_EMAIL, MS_SENDER_NAME, MS_CLIENT_ID, MS_AUTHORITY, MS_SCOPES, MS_TOKEN_FILE,
    SHEETS_CREDS, SPREADSHEET,
)

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(message)s")

_LOCAL = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".local")
DIGEST_TO = "prasadckanade@gmail.com"


def _get_token():
    import msal
    cache = msal.SerializableTokenCache()
    if os.path.exists(MS_TOKEN_FILE):
        cache.deserialize(open(MS_TOKEN_FILE).read())
    app = msal.PublicClientApplication(MS_CLIENT_ID, authority=MS_AUTHORITY, token_cache=cache)
    accts = app.get_accounts()
    result = app.acquire_token_silent(MS_SCOPES, account=accts[0]) if accts else None
    if not result or "access_token" not in result:
        raise Exception("MS token expired")
    if cache.has_state_changed:
        import fcntl as _fcntl
        with open(MS_TOKEN_FILE + ".lock", "w") as _lf:
            _fcntl.flock(_lf, _fcntl.LOCK_EX)
            open(MS_TOKEN_FILE, "w").write(cache.serialize())
            _fcntl.flock(_lf, _fcntl.LOCK_UN)
    return result["access_token"]


def _source_quality_stats():
    """Get source quality rates from brain.json."""
    try:
        import json
        brain_path = os.path.join(_LOCAL, "brain.json")
        if not os.path.exists(brain_path):
            return {}
        b = json.load(open(brain_path))
        sq = b.get("aggregator", {}).get("source_quality", {})
        result = {}
        for src, data in sq.items():
            if isinstance(data, dict) and data.get("runs", 0) >= 2:
                result[src] = {
                    "rate": round(data.get("quality_rate", 0) * 100, 1),
                    "valid": data.get("valid", 0),
                    "fetched": data.get("fetched", 0),
                    "runs": data.get("runs", 0),
                }
        return result
    except Exception:
        return {}

def _latest_run_stats():
    """Get stats from SQLite run history."""
    db = os.path.join(_LOCAL, "run_history.db")
    if not os.path.exists(db):
        return {}
    try:
        con = sqlite3.connect(db, timeout=30)
        row = con.execute(
            "SELECT ts,valid,discarded,failed_http,elapsed_seconds FROM runs ORDER BY ts DESC LIMIT 1"
        ).fetchone()
        con.close()
        if row:
            return {
                "ts": row[0][:16],
                "valid": row[1],
                "discarded": row[2],
                "failed_http": row[3],
                "elapsed_min": round(row[4] / 60, 1) if row[4] else 0,
            }
    except Exception as e:
        log.debug(f"DB read failed: {e}")
    return {}


def _bounced_today():
    """Count bounces in last 24h."""
    f = os.path.join(_LOCAL, "bounced_emails.json")
    if not os.path.exists(f):
        return 0
    try:
        d = json.load(open(f))
        cut = (datetime.datetime.now() - datetime.timedelta(hours=24)).isoformat()
        return sum(1 for v in d.values() if v.get("bounced_at", "") >= cut)
    except Exception:
        return 0


def _sent_today():
    """Count emails sent today from send_scheduled.py SENT_LOG_FILE (sent_log.json)."""    # sent_log.json stores {"email||subject": "YYYY-MM-DD HH:MM:SS"}
    # written by _save_sl() in send_scheduled.py
    f = os.path.join(_LOCAL, "sent_log.json")
    if not os.path.exists(f):
        return 0
    try:
        d = json.load(open(f))
        today = datetime.date.today().isoformat()
        return sum(1 for v in d.values() if isinstance(v, str) and v[:10] == today)
    except Exception:
        return 0


def _recent_errors():
    """Get last 5 ERROR lines from outreach.log written today."""
    f = os.path.join(_LOCAL, "outreach.log")
    if not os.path.exists(f):
        return []
    today = datetime.date.today().strftime("%Y-%m-%d")
    errors = []
    try:
        with open(f, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if today in line and "| ERROR |" in line:
                    errors.append(line.strip()[:120])
        return errors[-5:]
    except Exception:
        return []


def _scheduler_health():
    """Read scheduler_state.json and return health summary."""
    f = os.path.join(_LOCAL, "scheduler_state.json")
    if not os.path.exists(f):
        return {}
    try:
        state = json.load(open(f))
        now = datetime.datetime.now()
        max_gaps = {
            "aggregator": 8*3600, "send_scheduled": 24*3600,
            "outreach": 30*3600, "nightly_digest": 30*3600,
            "cleanup_not_applied": 30*3600, "build_auto_blacklist": 30*3600,
            "retry_simplify": 30*3600, "process_bounces": 3600, "watchdog": 3600,
        }
        results = {}
        for job, last_str in state.items():
            try:
                last = datetime.datetime.fromisoformat(last_str)
                elapsed = (now - last).total_seconds()
                max_gap = max_gaps.get(job, 30*3600)
                results[job] = {
                    "last_run": last.strftime("%b %d %H:%M"),
                    "elapsed_h": round(elapsed/3600, 1),
                    "stale": elapsed > max_gap,
                }
            except Exception:
                pass
        return results
    except Exception:
        return {}


def _circuit_breaker_status():
    """Check circuit breaker state."""
    f = os.path.join(_LOCAL, "circuit_breaker.json")
    if not os.path.exists(f):
        return "OK"
    try:
        d = json.load(open(f))
        if d.get("tripped"):
            return f"TRIPPED: {d.get('trip_reason', '?')}"
        return f"OK (sent:{d.get('sent',0)}, bounced:{d.get('bounced',0)})"
    except Exception:
        return "Unknown"


def _outreach_queue_size():
    """Count rows with Extract=yes and no email yet."""
    try:
        import gspread
        from oauth2client.service_account import ServiceAccountCredentials
        from outreach.outreach_config import C, OUTREACH_TAB
        from outreach.outreach_data import _pad
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        creds = ServiceAccountCredentials.from_json_keyfile_name(SHEETS_CREDS, scope)
        ws = gspread.authorize(creds).open(SPREADSHEET).worksheet(OUTREACH_TAB)
        rows = ws.get_all_values()[1:]
        pending = sum(1 for r in rows
                      if len(r) > C["extract"]
                      and r[C["extract"]].strip().lower() == "yes"
                      and not r[C["hm_email"]].strip()
                      and not r[C["rec_email"]].strip())
        return pending
    except Exception:
        return -1


def _build_html(stats, sent, bounced, errors, cb, pending, burn_alerts=None, sched_health=None, anomaly_alerts=None, dq_report=None):
    now = datetime.datetime.now().strftime("%b %d, %Y %I:%M %p")
    ok_color = "#2d8a4e"
    warn_color = "#b45309"
    err_color = "#b91c1c"

    def row(label, value, color="#333"):
        return f'<tr><td style="padding:6px 12px;color:#666;">{label}</td><td style="padding:6px 12px;font-weight:500;color:{color};">{value}</td></tr>'

    agg_html = ""
    if stats:
        agg_html = f"""
        <h3 style="color:#444;margin:20px 0 8px;">Aggregator (last run: {stats.get('ts','?')})</h3>
        <table style="border-collapse:collapse;width:100%;">
            {row("Valid jobs added", stats.get('valid',0), ok_color)}
            {row("Discarded", stats.get('discarded',0))}
            {row("HTTP failures", stats.get('failed_http',0), warn_color if stats.get('failed_http',0) > 5 else "#333")}
            {row("Run time", f"{stats.get('elapsed_min',0)} min")}
        </table>"""

    errors_html = ""
    if errors:
        err_lines = "".join(f"<li style='font-size:12px;color:{err_color};margin:4px 0;'>{e}</li>" for e in errors)
        errors_html = f"<h3 style='color:{err_color};margin:20px 0 8px;'>Errors today</h3><ul>{err_lines}</ul>"

    cb_color = err_color if "TRIPPED" in cb else ok_color

    return f"""
    <div style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto;padding:20px;">
      <h2 style="color:#1a1a2e;border-bottom:2px solid #e2e8f0;padding-bottom:10px;">
        Job Hunt Pipeline — {now}
      </h2>

      <h3 style="color:#444;margin:20px 0 8px;">Outreach</h3>
      <table style="border-collapse:collapse;width:100%;">
        {row("Emails sent today", sent, ok_color if sent > 0 else "#333")}
        {row("Bounces (24h)", bounced, warn_color if bounced > 0 else ok_color)}
        {row("Pending extraction", pending if pending >= 0 else "?")}
        {row("Circuit breaker", cb, cb_color)}
      </table>

      {agg_html}
      {errors_html}

      {f'<h3 style="color:#b45309;margin:20px 0 8px;">⚠ API Credit Warnings</h3><ul>' + ''.join(f'<li style="color:#b45309;font-size:12px;">{a}</li>' for a in burn_alerts) + '</ul>' if burn_alerts else ''}
      {'<h3 style="color:#444;margin:20px 0 8px;">Scheduler Health</h3><table style="border-collapse:collapse;width:100%;">' + ''.join(
        f'<tr><td style="padding:4px 12px;color:#666;font-size:12px;">{job}</td><td style="padding:4px 12px;font-size:12px;color:{"#b91c1c" if v["stale"] else "#2d8a4e"};">{v["last_run"]} ({v["elapsed_h"]}h ago){" ⚠ STALE" if v["stale"] else " ✓"}</td></tr>'
        for job, v in (sched_health or {}).items()
      ) + '</table>' if sched_health else ''}
      {f'<h3 style="color:#b45309;margin:20px 0 8px;">📊 Source Anomalies</h3><ul>' + ''.join(f'<li style="color:{"#b91c1c" if a.severity == "critical" else "#b45309" if a.severity == "warning" else "#666"};font-size:12px;margin:4px 0;">{str(a)}</li>' for a in (anomaly_alerts or [])) + '</ul>' if anomaly_alerts else ''}
      {f'<h3 style="color:#444;margin:20px 0 8px;">📈 Data Quality</h3><pre style="font-size:11px;color:#555;background:#f8f8f8;padding:10px;border-radius:4px;overflow-x:auto;">' + (dq_report or '') + '</pre>' if dq_report else ''}
      <p style="color:#999;font-size:11px;margin-top:20px;">
        Sent from your Job Hunt Pipeline · kanade.pra@northeastern.edu
      </p>
    </div>
    """


def main():
    print(f"NIGHTLY DIGEST  {datetime.datetime.now().strftime('%b %d %I:%M %p')}")

    stats   = _latest_run_stats()
    sent    = _sent_today()
    bounced = _bounced_today()
    errors  = _recent_errors()
    cb      = _circuit_breaker_status()
    pending = _outreach_queue_size()
    burn_alerts = []
    try:
        from outreach.brain import Brain
        from outreach.outreach_data import Credits
        burn_alerts = Credits().burn_rate_alerts()
    except Exception:
        pass

    subject = f"Job Hunt — {datetime.date.today().strftime('%b %d')} | {stats.get('valid',0)} new jobs, {sent} emails sent"
    if errors:
        subject = "⚠ " + subject
    if "TRIPPED" in cb:
        subject = "🚨 Circuit breaker tripped! " + subject
    if burn_alerts:
        subject = "💳 " + subject

    sched_health = _scheduler_health()

    # Anomaly detection alerts
    anomaly_alerts = []
    try:
        from analytics.anomaly import AnomalyDetector
        detector = AnomalyDetector()
        anomaly_alerts = detector.check_all_sources()
        detector.close()
    except Exception:
        pass

    # Data quality report
    dq_report = ""
    try:
        from analytics.data_quality import DataQualityScorer
        from analytics.store import AnalyticsStore
        _dq_store = AnalyticsStore()
        dq_report = DataQualityScorer.quality_report_text(_dq_store)
        _dq_store.close()
    except Exception:
        pass

    html = _build_html(stats, sent, bounced, errors, cb, pending, burn_alerts, sched_health, anomaly_alerts, dq_report)

    try:
        import requests as _req
        token = _get_token()
        payload = {
            "message": {
                "subject": subject,
                "body": {"contentType": "HTML", "content": html},
                "toRecipients": [{"emailAddress": {"address": DIGEST_TO}}],
                "from": {"emailAddress": {"name": MS_SENDER_NAME, "address": MS_SENDER_EMAIL}},
            },
            "saveToSentItems": "false",
        }
        resp = _req.post(
            f"https://graph.microsoft.com/v1.0/users/{MS_SENDER_EMAIL}/sendMail",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json=payload, timeout=30,
        )
        if resp.status_code in (200, 202):
            print(f"  Digest sent to {DIGEST_TO}")
        else:
            print(f"  Digest failed: {resp.status_code} {resp.text[:100]}")
    except Exception as e:
        print(f"  Digest error: {e}")


if __name__ == "__main__":
    main()

