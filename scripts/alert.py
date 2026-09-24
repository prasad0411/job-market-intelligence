#!/usr/bin/env python3
"""
Email an alert when a scheduled module fails.

Called by cron_runner.sh after every run. Sends nothing on success, except
the one-off notice when a module that had been failing recovers.

Reuses nightly_digest's _get_token(), so there is one MSAL token cache and
one refresh path rather than two competing ones.

Throttling matters more than it sounds. A failing module retries every two
hours; twelve identical emails a day gets the channel ignored, which is
exactly what happened when an off-hours exit(1) filed 102 GitHub issues. So:
one alert per module per failure kind per 6 hours, immediate alert when the
failure kind changes, and a single notice on recovery so silence reliably
means healthy.

Usage (from cron_runner.sh):
    python3 scripts/alert.py MODULE EXIT_CODE DURATION_S LOG_PATH

Exit code is always 0: an alerting failure must never mask the failure it was
trying to report.
"""
import contextlib
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

QUIET_HOURS = 6
STATE = os.path.join(".local", "alert_state.json")
TAIL_LINES = 25

# Modules whose non-zero exit is a report, not a crash. health_heartbeat
# exits 1 by design when it finds issues - alerting on that would fire daily.
EXIT_IS_A_REPORT = {"scripts/health_heartbeat", "health_heartbeat"}


# ── throttle ────────────────────────────────────────────────────────────────

def _load(path=STATE):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return {}


def _save(d, path=STATE):
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(d, f, indent=2)
        os.replace(tmp, path)
        return True
    except Exception:
        return False


def should_alert(module, kind, now=None, state=None, quiet_hours=QUIET_HOURS):
    """Returns (send, reason, new_state)."""
    # time.time() only when now is genuinely absent: `now or time.time()`
    # treats now=0 as unset, which made the tests compare against wall clock.
    now = time.time() if now is None else now
    st = dict(state if state is not None else _load())
    prev = st.get(module, {})
    prev_kind, prev_at = prev.get("kind"), prev.get("at", 0)

    if kind == "recovered":
        st[module] = {"kind": "recovered", "at": now}
        if prev_kind in (None, "recovered"):
            return False, "was already healthy", st
        return True, "recovered from %s" % prev_kind, st

    if prev_kind != kind:
        st[module] = {"kind": kind, "at": now}
        return True, ("first failure" if prev_kind in (None, "recovered")
                      else "failure kind changed from %s" % prev_kind), st

    elapsed = now - prev_at
    if elapsed >= quiet_hours * 3600:
        st[module] = {"kind": kind, "at": now}
        return True, "still failing after %.1fh" % (elapsed / 3600.0), st

    return False, "throttled, same failure %.0fm ago" % (elapsed / 60.0), st


# ── classification ──────────────────────────────────────────────────────────

def classify(module, exit_code, duration, log_text):
    """Short failure class, or 'recovered' when the run was clean."""
    if exit_code in (124, 137):
        return "timeout"
    if "WATCHDOG: no progress" in log_text:
        return "stalled"
    if exit_code == 0:
        if "finished at" not in log_text:
            return "no_finish"
        return "recovered"
    if module in EXIT_IS_A_REPORT:
        return "recovered"       # exit 1 here means "found issues", not broken
    return "exit_%d" % exit_code


def extract_error(log_text):
    """Last traceback line, or the last ERROR line, for the subject."""
    tb = [m for m in re.finditer(r"^\w*Error: .*$|^\w*Exception: .*$",
                                 log_text, re.M)]
    if tb:
        return tb[-1].group(0)[:140]
    errs = re.findall(r"^.*(?:ERROR|CRITICAL).*$", log_text, re.M)
    if errs:
        return errs[-1].strip()[:140]
    return ""


# ── send ────────────────────────────────────────────────────────────────────

def send(subject, body):
    """Reuses the digest's token so there is a single MSAL cache."""
    import requests
    # DIGEST_TO lives in nightly_digest, not in outreach_config - alerts go
    # to the same address as the nightly digest so there is one inbox to watch.
    from scripts.nightly_digest import _get_token, DIGEST_TO
    from outreach.outreach_config import MS_SENDER_EMAIL, MS_SENDER_NAME

    payload = {
        "message": {
            "subject": subject,
            "body": {"contentType": "Text", "content": body},
            "toRecipients": [{"emailAddress": {"address": DIGEST_TO}}],
            "from": {"emailAddress": {"name": MS_SENDER_NAME,
                                      "address": MS_SENDER_EMAIL}},
        },
        "saveToSentItems": "false",
    }
    r = requests.post(
        "https://graph.microsoft.com/v1.0/users/%s/sendMail" % MS_SENDER_EMAIL,
        headers={"Authorization": "Bearer %s" % _get_token(),
                 "Content-Type": "application/json"},
        json=payload, timeout=30)
    return r.status_code in (200, 202), r.status_code


def main():
    if len(sys.argv) < 5:
        print("usage: alert.py MODULE EXIT_CODE DURATION_S LOG_PATH")
        return 0

    module = sys.argv[1]
    try:
        exit_code = int(sys.argv[2])
    except ValueError:
        exit_code = -1
    duration = sys.argv[3]
    log_path = sys.argv[4]

    log_text = ""
    # An unreadable log still leaves the exit code and duration worth
    # reporting, so this degrades rather than aborting.
    with contextlib.suppress(OSError):
        with open(log_path, errors="replace") as f:
            log_text = f.read()

    kind = classify(module, exit_code, duration, log_text)
    send_it, reason, state = should_alert(module, kind)

    # State is only recorded once the send has been attempted. Saving it up
    # front meant a failed send still burned the 6h throttle window, so a
    # transient Graph outage would silently suppress the next alert.
    if not send_it:
        _save(state)
        print("alert: %s %s - %s" % (module, kind, reason))
        return 0

    if kind == "recovered":
        subject = "[tracker] %s recovered" % module
        body = ("%s completed successfully.\n\nexit: %d\nduration: %ss\n\n%s\n"
                % (module, exit_code, duration, reason))
    else:
        err = extract_error(log_text)
        tail = "\n".join(log_text.splitlines()[-TAIL_LINES:])
        subject = "[tracker] %s %s" % (module, kind)
        if err:
            subject += " - %s" % err[:60]
        body = (
            "module:   %s\nkind:     %s\nexit:     %d\nduration: %ss\n"
            "log:      %s\nwhy now:  %s\n" % (module, kind, exit_code,
                                              duration, log_path, reason)
            + ("\nerror:    %s\n" % err if err else "")
            + "\n--- last %d log lines ---\n%s\n" % (TAIL_LINES, tail))

    try:
        ok, status = send(subject, body)
        if ok:
            _save(state)      # only throttle what actually went out
        print("alert: %s %s - %s (http %s)"
              % (module, kind, "sent" if ok else "send failed", status))
    except Exception as e:
        # Never let an alerting failure mask the failure being reported.
        print("alert: %s %s - could not send: %s" % (module, kind, str(e)[:90]))

    return 0


if __name__ == "__main__":
    sys.exit(main())
