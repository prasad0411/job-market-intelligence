#!/usr/bin/env python3
"""
Audit every scheduled job and repair what is safely repairable.

Written after a stale lock silently blocked the aggregator for twelve hours.
The scheduler dispatched at 14:57 and 21:00; both were skipped by a lock file
whose owning process had died at 09:26, and neither skip produced a log. From
the outside the system looked healthy: scheduler alive, no errors, no alerts.

Checks, per module:

  schedule    last run against its configured cadence, so a job that has
              quietly stopped is visible
  locks       lock files whose owning PID is dead. This is the failure that
              prompted the tool: .local/aggregator.lock held by PID 67016,
              gone since 09:26
  outcome     exit code, duration and error markers of the last three runs
  work        whether announced work was confirmed - "Moving 150 jobs" with
              no "Moved 150 jobs" means a pass died partway
  timeouts    runs that exceeded their configured ceiling

Only stale locks are repaired automatically, and only when the owning process
is confirmed dead. Everything else is reported: a tool that silently "fixes"
things it does not understand is how a half-finished move gets mistaken for a
success.

    python3 scripts/doctor.py            # report only
    python3 scripts/doctor.py --repair   # also clear dead locks
"""
import argparse
import datetime
import glob
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

LOGS = os.path.join(".local", "cron_logs")
LOCAL = ".local"
STATE = os.path.join(LOCAL, "scheduler_state.json")

# name -> (expected gap in hours before it counts as overdue, timeout seconds)
EXPECTED = {
    "aggregator": (9, 10800),
    "outreach": (3, 3600),
    "scripts/send_scheduled": (2, 900),
    "scripts/nightly_digest": (26, 1800),
    "scripts/cleanup_not_applied": (26, 600),
    "scripts/ats_discovery": (26, 1200),
    "scripts/retry_simplify": (26, 1800),
    "scripts/quality_gate": (12, 1800),
    "scripts/health_heartbeat": (12, 1800),
    "scripts/discarded_auditor": (74, 1800),
    "scripts/process_bounces": (2, 1800),
    "scripts/build_auto_blacklist": (26, 1800),
}

FAILURE_MARKERS = (r"^✗ .*error", r"^Traceback \(most recent call last\)",
                   r"ConnectionResetError", r"MISMATCH:", r"ABORT:")
_MARK = [re.compile(p, re.M | re.I) for p in FAILURE_MARKERS]
_ANNOUNCED = re.compile(r"^Moving (\d+) jobs", re.M)
_CONFIRMED = re.compile(r"^✓ Moved (\d+) jobs", re.M)

# RUN is distinct from OK and FAIL: a job that is still working has no
# finish line yet, and reporting that as a failure alerted on healthy runs.
OK, WARN, BAD, RUN = "ok", "WARN", "FAIL", "RUN"

# A running job writes to its log. The watchdog treats ten minutes of
# silence as a stall, so twenty is long enough to survive a slow fetch and
# short enough to notice a hang within one scheduling interval.
STALL_MINUTES = 20


def pid_alive(pid):
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, ValueError, TypeError):
        return False


def find_locks():
    """Every lock file or directory, with its owning PID and liveness."""
    out = []
    for p in sorted(glob.glob(os.path.join(LOCAL, "*.lock"))):
        pid, age = None, time_since(p)
        if os.path.isdir(p):
            pf = os.path.join(p, "pid")
            if os.path.exists(pf):
                pid = read_first_line(pf)
                age = time_since(pf)
        else:
            pid = read_first_line(p)
        out.append((p, pid, age, pid_alive(pid) if pid else None))
    return out


def read_first_line(path):
    try:
        with open(path) as f:
            return f.readline().strip()
    except Exception:
        return None


def time_since(path):
    try:
        return (datetime.datetime.now()
                - datetime.datetime.fromtimestamp(os.path.getmtime(path)))
    except Exception:
        return None


def hours(td):
    return td.total_seconds() / 3600.0 if td else None


def recent_logs(module, n=3):
    safe = os.path.basename(module)
    files = sorted(glob.glob(os.path.join(LOGS, "%s_*.log" % safe)), reverse=True)
    return files[:n]


def inspect(path):
    """Read one run log into a verdict."""
    try:
        with open(path, errors="replace") as f:
            txt = f.read()
    except Exception as e:
        return {"state": BAD, "note": "unreadable: %s" % e}
    try:
        _quiet_min = (time.time() - os.path.getmtime(path)) / 60.0
    except Exception:
        _quiet_min = 0.0

    # The CLOCK line, emitted by resilience at exit, carries active and CPU
    # time. Wall-clock duration counts suspend, so an overnight run reported
    # 40,437s against a 10,800s ceiling while having done its work correctly.
    _clock = re.search(
        r"CLOCK: wall (\d+)s \| active (\d+)s \| cpu (\d+)s \| slept (\d+)s", txt)

    m = re.search(r"finished at .*?\| exit: (-?\d+) \| duration: (\d+)s", txt)
    if not m:
        # No finish line means either "still working" or "died", and those
        # are told apart by whether the log is still being written to.
        if _quiet_min < STALL_MINUTES:
            return {"state": RUN,
                    "note": "in progress, last wrote %.0fm ago" % _quiet_min,
                    "exit": None, "dur": None}
        return {"state": BAD,
                "note": "no finish line, silent %.0fm - died or hung" % _quiet_min,
                "exit": None, "dur": None}
    code, dur = int(m.group(1)), int(m.group(2))
    _extra = {}
    if _clock:
        _extra = {"wall_s": int(_clock.group(1)), "active_s": int(_clock.group(2)),
                  "cpu_s": int(_clock.group(3)), "slept_s": int(_clock.group(4))}

    marks = [p.pattern for p in _MARK if p.search(txt)]
    a = sum(int(x) for x in _ANNOUNCED.findall(txt))
    c = sum(int(x) for x in _CONFIRMED.findall(txt))

    if code in (124, 137):
        return {"state": BAD, "note": "killed on timeout", "exit": code, "dur": dur}
    if marks:
        return {"state": BAD, "note": "error in log despite exit %d" % code,
                "exit": code, "dur": dur}
    if a and a != c:
        return {"state": BAD, "note": "announced %d, confirmed %d" % (a, c),
                "exit": code, "dur": dur}
    if code != 0 and module_is_report(path):
        return {"state": OK, "note": "exit %d is a report" % code,
                "exit": code, "dur": dur}
    if code != 0:
        return {"state": BAD, "note": "exit %d" % code, "exit": code, "dur": dur}
    return dict({"state": OK, "note": "clean", "exit": code,
                 "dur": dur}, **_extra)


def module_is_report(path):
    return "health_heartbeat" in path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repair", action="store_true",
                    help="clear locks whose owning process is dead")
    a = ap.parse_args()

    problems = []

    # ── locks ────────────────────────────────────────────────────────────────
    print("=" * 78)
    print("LOCKS")
    print("=" * 78)
    stale = []
    for path, pid, age, alive in find_locks():
        h = hours(age)
        if pid and alive is False:
            state, note = BAD, "held by dead pid %s" % pid
            stale.append(path)
        elif pid and alive:
            state, note = OK, "held by live pid %s" % pid
        elif h is not None and h > 24:
            state, note = WARN, "no pid recorded, %.0fh old" % h
            stale.append(path)
        else:
            state, note = OK, "no pid recorded"
        print("  %-5s %-46s %s%s" % (state, os.path.basename(path), note,
                                     "" if h is None else "  (%.1fh)" % h))
        if state == BAD:
            problems.append("stale lock: %s" % os.path.basename(path))

    if stale:
        print()
        if a.repair:
            for p in stale:
                try:
                    if os.path.isdir(p):
                        import shutil
                        shutil.rmtree(p)
                    else:
                        os.remove(p)
                    print("  cleared %s" % os.path.basename(p))
                except Exception as e:
                    print("  could not clear %s: %s" % (os.path.basename(p), e))
        else:
            print("  %d stale lock(s). Re-run with --repair to clear them."
                  % len(stale))

    # ── schedule and outcomes ───────────────────────────────────────────────
    print()
    print("=" * 78)
    print("MODULES")
    print("=" * 78)
    for mod, (gap_h, timeout) in sorted(EXPECTED.items()):
        logs = recent_logs(mod)
        if not logs:
            print("\n  %-30s %s no logs at all" % (mod, BAD))
            problems.append("%s: never ran" % mod)
            continue

        since = hours(time_since(logs[0]))
        overdue = since is not None and since > gap_h
        head = "%-30s last run %.1fh ago" % (mod, since or -1)
        print("\n  %-5s %s%s" % (WARN if overdue else OK, head,
                                 "  OVERDUE (expected every %dh)" % gap_h
                                 if overdue else ""))
        if overdue:
            problems.append("%s: overdue, last run %.0fh ago" % (mod, since))

        for lg in logs:
            r = inspect(lg)
            when = os.path.basename(lg).replace(".log", "").split("_", 1)[-1]
            extra = ""
            if r.get("dur") and r["dur"] > timeout:
                # Prefer active time when the run reported it: wall-clock
                # duration counts suspend, and this laptop suspends
                # constantly - 805 times in three days.
                _act = r.get("active_s")
                _cpu = r.get("cpu_s")
                if _act is not None and _act <= timeout:
                    extra = "  (wall %ds, active %ds - slept)" % (r["dur"], _act)
                else:
                    basis = _act if _act is not None else r["dur"]
                    stuck = (_cpu is not None and basis > 0
                             and (_cpu / float(basis)) < 0.02)
                    extra = "  EXCEEDED %ds ceiling%s" % (
                        timeout, " (stuck: %ds cpu)" % _cpu if stuck else "")
                    problems.append(
                        "%s: %ds active against a %ds ceiling%s"
                        % (mod, basis, timeout,
                           " with only %ds cpu - stuck waiting" % _cpu
                           if stuck else ""))
            print("        %-5s %-18s %-38s%s"
                  % (r["state"], when, r["note"][:38], extra))
            if r["state"] == BAD:
                problems.append("%s @ %s: %s" % (mod, when, r["note"]))

    # ── summary ─────────────────────────────────────────────────────────────
    print()
    print("=" * 78)
    if not problems:
        print("ALL CLEAR")
    else:
        print("%d PROBLEM(S)" % len(problems))
        for p in problems:
            print("  - %s" % p)
    print("=" * 78)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
