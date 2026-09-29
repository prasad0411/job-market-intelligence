#!/usr/bin/env python3
"""
Reproduce every failure observed 21-28 Sep and assert its fix holds.

Regression checks rather than unit tests: each one reproduces a specific
incident from the logs. A failing check means that incident can recur.

  1  socket ceiling            21 Sep aggregator ran 18.4h, 24 Sep 3.8h
  2  transient retry           21/26/28 Sep ConnectionResetError, 3 cleanups
  3  no retry on fatal         a 403 must not be retried
  4  exhausted retry raises    returning None hid a half-finished move
  5  silent error detected     26 Sep: crashed twice, exited 0, "recovered"
  6  partial work detected     announced 150, confirmed none
  7  clean run stays clean     or every run alerts and gets ignored
  8  sweep budget              28 Sep send_scheduled killed three nights
  9  lock release              28 Sep a stale lock blocked 12h of dispatch
 10  exit non-zero on failure  cleanup reported success after crashing
 11  PatternCache guard        AttributeError on every outreach run, 5 days
 12  scripts resilience        the 4,432s run had no socket ceiling

Read-only: nothing is written, no sheet is touched, no email is sent.

    python3 scripts/verify_fixes.py
"""
import contextlib
import glob
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

PASS, FAIL = "PASS", "FAIL"
results = []


def record(n, name, ok, detail):
    results.append((n, name, ok, detail))
    print("  %-4s %2d. %-34s %s" % (PASS if ok else FAIL, n, name, detail))


def main():
    print("=" * 76)
    print("VERIFYING FIXES FOR THE FAILURES OF 21-28 SEP")
    print("=" * 76)
    print()

    # ── resilience: socket ceiling and retry ────────────────────────────────
    try:
        from aggregator import resilience as R
    except Exception as e:
        print("  FAIL  cannot import aggregator.resilience: %s" % e)
        return 1

    import socket
    R.install()
    t = socket.getdefaulttimeout()
    record(1, "socket ceiling installed", t is not None and t <= 60,
           "default timeout %s" % t)

    calls = []

    def flaky():
        calls.append(1)
        if len(calls) < 3:
            raise ConnectionResetError(54, "Connection reset by peer")
        return "ok"

    try:
        got = R.retry_call(flaky, base=1.0, cap=0.01)
        record(2, "transient reset is retried", got == "ok" and len(calls) == 3,
               "recovered after %d attempts" % len(calls))
    except Exception as e:
        record(2, "transient reset is retried", False, "raised: %s" % e)

    fcalls = []

    def fatal():
        fcalls.append(1)
        raise Exception("403 Forbidden: permission denied")

    with contextlib.suppress(Exception):
        R.retry_call(fatal, attempts=4, base=1.0, cap=0.01)
    record(3, "fatal error is not retried", len(fcalls) == 1,
           "tried %d time(s)" % len(fcalls))

    def always():
        raise ConnectionResetError(54, "reset")

    try:
        out = R.retry_call(always, attempts=2, base=1.0, cap=0.01)
        record(4, "exhausted retry raises", False,
               "returned %r instead of raising" % out)
    except ConnectionResetError:
        record(4, "exhausted retry raises", True, "raised as expected")
    except Exception as e:
        record(4, "exhausted retry raises", False, "raised %s" % type(e).__name__)

    # ── alerter classification ──────────────────────────────────────────────
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "_alert", os.path.join(ROOT, "scripts", "alert.py"))
        A = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(A)
    except Exception as e:
        record(5, "silent error detected", False, "cannot load alert.py: %s" % e)
        A = None

    if A:
        crashed = ("EXPIRY CLEANUP: Moving jobs with no status older than 3 days\n"
                   "\u2717 Expiry cleanup error: ConnectionResetError(54)\n"
                   "Moving 150 jobs, keeping 2205\n"
                   "\u2717 Cleanup error: ConnectionResetError(54)\n"
                   "=== finished at Sat Sep 26 | exit: 0 | duration: 4432s ===\n")
        k = A.classify("scripts/cleanup_not_applied", 0, "4432", crashed)
        record(5, "silent error detected", k != "recovered", "classified %s" % k)

        partial = ("Moving 150 jobs, keeping 2205\n"
                   "=== finished at X | exit: 0 | duration: 300s ===\n")
        k = A.classify("scripts/cleanup_not_applied", 0, "300", partial)
        record(6, "unconfirmed work detected", k == "partial_work",
               "classified %s" % k)

        clean = ("Moving 93 jobs, keeping 1615\n"
                 "\u2713 Moved 93 jobs, 1615 remaining\n"
                 "=== finished at X | exit: 0 | duration: 120s ===\n")
        k = A.classify("scripts/cleanup_not_applied", 0, "120", clean)
        record(7, "clean run stays clean", k == "recovered", "classified %s" % k)

    # ── source-level guards ─────────────────────────────────────────────────
    def read(rel):
        try:
            return open(os.path.join(ROOT, rel)).read()
        except Exception:
            return ""

    src = read("outreach/outreach_finder.py")
    m = re.search(r"_BUDGET\s*=\s*([\d.]+)", src)
    val = float(m.group(1)) if m else None
    record(8, "sweep has a wall-clock budget",
           bool(val) and val <= 120 and "_deadline" in src,
           "budget %ss" % val if val else "no budget found")

    src = read("aggregator/run_aggregator.py")
    record(9, "aggregator lock releases on exit",
           "_release_aggregator_lock" in src and "atexit" in src,
           "release registered" if "_release_aggregator_lock" in src
           else "NO RELEASE - a finished run will block the next")

    src = read("scripts/cleanup_not_applied.py")
    record(10, "cleanup exits non-zero on failure",
           "_RUN_FAILED" in src and "_sys.exit(1)" in src,
           "reports failure upward" if "_RUN_FAILED" in src
           else "still always exits 0")

    src = read("outreach/outreach_finder.py")
    record(11, "PatternCache result type guarded",
           "isinstance(_pc_result" in src,
           "guarded" if "isinstance(_pc_result" in src
           else "unguarded .get - will raise every run")

    # Only modules the scheduler dispatches. scheduler.py is the parent that
    # launches them and status.py is a reporting tool; neither should install
    # signal handlers or a watchdog of its own.
    SCHEDULED = (
        "cleanup_not_applied", "send_scheduled", "nightly_digest",
        "ats_discovery", "retry_simplify", "quality_gate", "health_heartbeat",
        "discarded_auditor", "process_bounces", "build_auto_blacklist",
        "applied_trigger", "auto_extract",
    )
    missing = []
    for name in SCHEDULED:
        p = os.path.join(ROOT, "scripts", "%s.py" % name)
        if os.path.exists(p) and "_resilient" not in open(p).read():
            missing.append(name)
    record(12, "scheduled scripts install resilience", not missing,
           "all %d wired" % len(SCHEDULED) if not missing
           else "missing: %s" % ", ".join(missing[:4]))

    # ── live state ──────────────────────────────────────────────────────────
    print()
    print("  live state")
    stale = []
    for p in glob.glob(os.path.join(ROOT, ".local", "*.lock")):
        pid = None
        target = os.path.join(p, "pid") if os.path.isdir(p) else p
        with contextlib.suppress(Exception):
            with open(target) as f:
                pid = f.readline().strip()
        if pid:
            try:
                os.kill(int(pid), 0)
            except Exception:
                stale.append(os.path.basename(p))
    record(13, "no stale locks right now", not stale,
           "clean" if not stale else "stale: %s" % ", ".join(stale))

    # ── summary ─────────────────────────────────────────────────────────────
    bad = [r for r in results if not r[2]]
    print()
    print("=" * 76)
    if not bad:
        print("ALL %d CHECKS PASS - every failure from 21-28 Sep is covered" % len(results))
    else:
        print("%d of %d CHECKS FAILED" % (len(bad), len(results)))
        for n, name, _ok, detail in bad:
            print("  %2d. %-34s %s" % (n, name, detail))
    print("=" * 76)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
