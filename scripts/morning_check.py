#!/usr/bin/env python3
"""
Evidence that each fix from the past month is working in production.

Three tools, three different questions:

    verify_fixes.py    is the fix still in the code?
    doctor.py          are the jobs running and completing?
    morning_check.py   did each fix actually fire when it was needed?

The third is this one. A fix can be present in the source and still not be
doing anything - _sheets_retry was written and applied to no method for
weeks, record_rejection was written and called from nowhere, the 20% cleanup
guard ran every day and blocked every run. Presence is not function.

Each check below reads the last 48 hours of logs for the footprint a working
fix leaves behind, and reports "no evidence yet" rather than passing when
there is nothing to judge.

    python3 scripts/morning_check.py
    python3 scripts/morning_check.py --hours 72
"""
import argparse
import glob
import os
import re
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOGS = os.path.join(ROOT, ".local", "cron_logs")
LOCAL = os.path.join(ROOT, ".local")

PASS, FAIL, NODATA = "PASS", "FAIL", "----"
results = []


def record(n, fix, when, ok, detail):
    state = PASS if ok is True else FAIL if ok is False else NODATA
    results.append((n, fix, ok, detail))
    print("  %-4s %2d. %-30s %-9s %s" % (state, n, fix, when, detail))


def recent(hours):
    cutoff = time.time() - hours * 3600
    out = []
    for p in glob.glob(os.path.join(LOGS, "*.log")):
        try:
            if os.path.getmtime(p) >= cutoff:
                with open(p, errors="replace") as f:
                    out.append((os.path.basename(p), f.read()))
        except OSError:
            continue
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=int, default=48)
    a = ap.parse_args()

    logs = recent(a.hours)
    print("=" * 84)
    print("MORNING CHECK: %d log(s) from the last %dh" % (len(logs), a.hours))
    print("=" * 84)
    if not logs:
        print("\n  No recent runs. The scheduler may be stopped - check:")
        print("    launchctl list | grep jobtracker")
        return 1

    # 1. CLOCK line: sleep no longer read as a hang (30 Sep)
    hits = [n for n, t in logs if "CLOCK: wall" in t]
    slept = [n for n, t in logs
             if re.search(r"CLOCK:.*slept (\d+)s", t)
             and int(re.search(r"slept (\d+)s", t).group(1)) > 60]
    record(1, "active-time measurement", "30 Sep",
           bool(hits) if logs else None,
           "%d run(s) emitted CLOCK, %d spanned a suspend" % (len(hits), len(slept))
           if hits else "no CLOCK line yet - runs predate the fix")

    # 2. retry on transient errors (28 Sep)
    retried = [n for n, t in logs if re.search(r"retry \d+/\d+ in", t)]
    recovered = [n for n, t in logs
                 if re.search(r"retry \d+/\d+ in", t) and "exit: 0" in t]
    record(2, "transient retry", "28 Sep",
           (len(recovered) > 0) if retried else None,
           "%d retried, %d finished cleanly" % (len(retried), len(recovered))
           if retried else "no transient errors in this window")

    # 3. no unreleased locks (28 Sep)
    stale = []
    for p in glob.glob(os.path.join(LOCAL, "*.lock")):
        target = os.path.join(p, "pid") if os.path.isdir(p) else p
        try:
            with open(target) as f:
                pid = f.readline().strip()
        except OSError:
            continue
        if not pid:
            continue
        try:
            os.kill(int(pid), 0)
        except (OSError, ValueError):
            stale.append(os.path.basename(p))
    record(3, "locks release on exit", "28 Sep", not stale,
           "clean" if not stale else "stale: %s" % ", ".join(stale))

    # 4. quarantine cleared (29 Sep)
    try:
        # Not text=True: xattr -l prints raw attribute bytes for some files,
        # which are not valid utf-8 and made this check fail to decode.
        raw = subprocess.run(["xattr", "-r", "-l", os.path.join(ROOT, "scripts")],
                             capture_output=True, timeout=30).stdout
        nq = raw.count(b"com.apple.quarantine")
        record(4, "quarantine cleared", "29 Sep", nq == 0,
               "clean" if nq == 0 else "%d flagged file(s) - launchd cannot run them" % nq)
    except Exception as e:
        record(4, "quarantine cleared", "29 Sep", None, "could not check: %s" % str(e)[:34])

    # 5. silent errors no longer reported as success (28 Sep)
    misreported = []
    for n, t in logs:
        errored = bool(re.search(r"^✗ .*error|ConnectionResetError", t, re.M))
        if errored and "exit: 0" in t and re.search(r"alert:.*recovered", t):
            misreported.append(n)
    record(5, "silent errors detected", "28 Sep", not misreported,
           "clean" if not misreported else "misreported: %s" % ", ".join(misreported[:2]))

    # 6. sweep budget stops the email sender being killed (28 Sep)
    killed = [n for n, t in logs if "exit: 124" in t]
    bounded = [n for n, t in logs if "budget spent" in t]
    record(6, "sweep budget", "28 Sep", not killed,
           "no timeout kills%s" % (", budget stopped %d" % len(bounded) if bounded else "")
           if not killed else "%d run(s) still killed on timeout" % len(killed))

    # 7. PatternCache type guard (28 Sep)
    ae = [n for n, t in logs if "AttributeError: 'str' object has no attribute" in t]
    record(7, "PatternCache guard", "28 Sep", not ae,
           "clean" if not ae else "still raising in %d run(s)" % len(ae))

    # 8. cleanup exits non-zero when it fails (28 Sep)
    lied = []
    for n, t in logs:
        if "cleanup_not_applied" not in n:
            continue
        if re.search(r"^✗ .*error", t, re.M) and "exit: 0" in t:
            lied.append(n)
    record(8, "cleanup reports failure", "28 Sep", not lied,
           "clean" if not lied else "exited 0 after failing: %s" % lied[0])

    # 9. jobs are actually reaching the sheet
    totals = []
    for _n, t in logs:
        totals += [int(x) for x in re.findall(r"✓ DONE: (\d+) valid", t)]
    record(9, "collection is flowing", "-", bool(totals),
           "%d run(s) collected %d jobs" % (len(totals), sum(totals))
           if totals else "no completed aggregator run in this window")

    # 10. scheduler alive
    try:
        lc = subprocess.run(["launchctl", "list"], capture_output=True,
                            text=True, timeout=15).stdout
        loaded = "com.prasad.jobtracker.scheduler" in lc
        record(10, "scheduler loaded", "-", loaded,
               "loaded" if loaded else "NOT LOADED - nothing will run")
    except Exception as e:
        record(10, "scheduler loaded", "-", None, "could not check: %s" % str(e)[:34])

    bad = [r for r in results if r[2] is False]
    nodata = [r for r in results if r[2] is None]
    print()
    print("=" * 84)
    if not bad:
        print("ALL CHECKS OK%s" % (" (%d awaiting evidence)" % len(nodata)
                                   if nodata else ""))
    else:
        print("%d CHECK(S) FAILED" % len(bad))
        for n, fix, _ok, detail in bad:
            print("  %2d. %-30s %s" % (n, fix, detail))
    print("=" * 84)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
