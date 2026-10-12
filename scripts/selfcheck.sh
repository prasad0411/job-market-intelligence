#!/bin/bash
# Self-maintenance. Runs before the scheduler starts and once a day after.
#
# Each step exists because something silently broke:
#
#   quarantine   29 Sep: launchd could not execute cron_runner.sh because
#                macOS had flagged it as downloaded. Nothing ran for 27h.
#                Every file copied in from ~/Downloads carries the flag.
#
#   locks        28 Sep: a lock left by a finished run blocked every
#                dispatch for 12h, and took quality_gate and
#                health_heartbeat down with it.
#
#   exec bits    a script that loses +x fails the same silent way.
#
#   audit        doctor.py reports overdue modules, exceeded ceilings and
#                runs that failed while reporting success.
#
# Exits non-zero when the audit finds problems, so the alerter picks it up.

set -uo pipefail
BASE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$BASE" || exit 1

LOG="$BASE/.local/selfcheck.log"
mkdir -p "$BASE/.local"
say() { echo "$(date '+%Y-%m-%d %H:%M:%S') | $*" | tee -a "$LOG"; }

say "=== selfcheck starting ==="

# ── 1. quarantine ───────────────────────────────────────────────────────────
# A quarantined shell script cannot be executed by a launchd process, and the
# failure message ("Operation not permitted") points at permissions rather
# than at the real cause.
Q=$(xattr -r -l "$BASE/scripts" "$BASE/aggregator" "$BASE/outreach" 2>/dev/null \
    | grep -c "com.apple.quarantine" || true)
if [[ "${Q:-0}" -gt 0 ]]; then
    xattr -d -r com.apple.quarantine "$BASE" 2>/dev/null
    say "cleared quarantine flag from $Q file(s)"
else
    say "quarantine: clean"
fi

# ── 2. executable bits ──────────────────────────────────────────────────────
FIXED=0
for f in "$BASE"/scripts/*.sh; do
    [[ -f "$f" && ! -x "$f" ]] && chmod +x "$f" && FIXED=$((FIXED+1))
done
[[ "$FIXED" -gt 0 ]] && say "restored +x on $FIXED script(s)" || say "exec bits: clean"

# ── 3. stale locks ──────────────────────────────────────────────────────────
# doctor.py --repair removes only locks whose owning process is confirmed
# dead, and reports everything else rather than guessing.
if [[ -f "$BASE/scripts/doctor.py" ]]; then
    python3 "$BASE/scripts/doctor.py" --repair >> "$LOG" 2>&1
    RC=$?
    CLEARED=$(grep -c "^  cleared " "$LOG" 2>/dev/null || echo 0)
    say "audit finished (exit $RC)"
else
    say "doctor.py missing - skipped audit"
    RC=0
fi

# ── 4. fixes still in place ─────────────────────────────────────────────────
if [[ -f "$BASE/scripts/verify_fixes.py" ]]; then
    if python3 "$BASE/scripts/verify_fixes.py" >> "$LOG" 2>&1; then
        say "regression checks: all pass"
    else
        say "regression checks: FAILURES - see $LOG"
        RC=1
    fi
fi

# ── 5. scheduler alive ──────────────────────────────────────────────────────
# Captured first rather than piped: grep -q exits on the first match, which
# sends SIGPIPE to launchctl, and with pipefail the pipeline then reports
# launchctl's failure. The check said NOT LOADED every time and would have
# restarted the scheduler daily for no reason.
LC_OUT=$(launchctl list 2>/dev/null || true)
if [[ "$LC_OUT" == *"com.prasad.jobtracker.scheduler"* ]]; then
    say "scheduler: loaded"
else
    say "scheduler: NOT LOADED - reloading"
    launchctl load -w ~/Library/LaunchAgents/com.prasad.jobtracker.scheduler.plist 2>/dev/null
    RC=1
fi

say "=== selfcheck done (exit ${RC:-0}) ==="

# keep the log from growing without bound
tail -2000 "$LOG" > "$LOG.tmp" 2>/dev/null && mv "$LOG.tmp" "$LOG"

exit "${RC:-0}"
