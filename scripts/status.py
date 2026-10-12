#!/usr/bin/env python3
"""
status.py — one-screen health readout for the whole pipeline.

Reads every .local/health_*.json (per-task records) plus health_state.json
(global heartbeat) and prints a single summary: what ran, when, exit codes,
and anything failed or stale. READ-ONLY — writes nothing, changes nothing.

Run:  python3 scripts/status.py
"""
import json
import os
import glob
from datetime import datetime, timezone

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOCAL = os.path.join(BASE, ".local")

STALE_HOURS = 26  # a daily task not seen in >26h is stale


def _parse_ts(v):
    if not v:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S",
                "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S.%f%z"):
        try:
            dt = datetime.strptime(v, fmt)
            return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt
        except ValueError:
            continue
    return None


def _age_hours(dt):
    if not dt:
        return None
    now = datetime.now(timezone.utc)
    return (now - dt).total_seconds() / 3600


def main():
    files = sorted(glob.glob(os.path.join(LOCAL, "health_*.json")))
    # health_state.json is the global one; handle it separately
    state_path = os.path.join(LOCAL, "health_state.json")
    task_files = [f for f in files if os.path.basename(f) != "health_state.json"]

    print("=" * 66)
    print("  PIPELINE STATUS  —", datetime.now().strftime("%Y-%m-%d %H:%M"))
    print("=" * 66)

    rows = []
    problems = []
    for f in task_files:
        name = os.path.basename(f)[len("health_"):-len(".json")]
        try:
            h = json.load(open(f))
        except Exception as e:
            rows.append((name, "?", "UNREADABLE", str(e)[:30]))
            problems.append(f"{name}: health file unreadable")
            continue
        ec = h.get("exit_code", "?")
        ts = _parse_ts(h.get("last_run") or h.get("last_run_ts") or h.get("last_check"))
        age = _age_hours(ts)
        dur = h.get("duration_seconds")

        if ec not in (0, "?"):
            status = "FAILED"
            problems.append(f"{name}: exit code {ec}")
        elif age is not None and age > STALE_HOURS:
            status = "STALE"
            problems.append(f"{name}: last ran {age:.0f}h ago")
        else:
            status = "ok"

        when = f"{age:.0f}h ago" if age is not None else "unknown"
        durs = f"{dur}s" if dur is not None else "-"
        rows.append((name, str(ec), status, f"{when:>10}  {durs:>7}"))

    w = max((len(r[0]) for r in rows), default=10)
    for name, ec, status, extra in rows:
        mark = "  " if status == "ok" else "!!"
        print(f" {mark} {name:<{w}}  exit={ec:<3}  {status:<10}  {extra}")

    # global heartbeat
    print("-" * 66)
    if os.path.exists(state_path):
        try:
            st = json.load(open(state_path))
            sc = st.get("last_source_counts", {})
            print(f"  Sheet rows: {st.get('last_sheet_count','?')}   "
                  f"Sheet stuck: {st.get('sheet_stuck_days',0)} days   "
                  f"Errors: {st.get('last_error_count','?')}")
            if sc:
                srcs = ", ".join(f"{k}={v}" for k, v in sc.items())
                print(f"  Last source counts: {srcs}")
            if st.get("sheet_stuck_days", 0) >= 2:
                problems.append(f"sheet stuck {st['sheet_stuck_days']} days")
        except Exception:
            print("  health_state.json unreadable")
    print("=" * 66)

    if problems:
        print(f"  {len(problems)} PROBLEM(S):")
        for p in problems:
            print(f"    - {p}")
    else:
        print("  All systems nominal.")
    print("=" * 66)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
