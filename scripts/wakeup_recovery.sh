#!/bin/bash
# Self-healing wakeup recovery — fires on Mac wake/login, checks missed jobs
BASE="/Users/prasadkanade/Documents/Prasad Kanade/Job Hunt Tracker"
LOG="$BASE/.local/wakeup.log"
SCRIPTS="$BASE/scripts/cron_runner.sh"
LOCKFILE="$BASE/.local/wakeup.lock"

# flock ships with util-linux and is NOT present on stock macOS, so the
# previous `flock -n 9` silently did nothing and this script could run
# alongside itself. mkdir is atomic on every POSIX filesystem.
LOCKDIR="${LOCKFILE}.d"
if ! mkdir "$LOCKDIR" 2>/dev/null; then
    _pid=$(cat "$LOCKDIR/pid" 2>/dev/null)
    if [[ -n "$_pid" ]] && kill -0 "$_pid" 2>/dev/null; then exit 0; fi
    _age=$(( $(date +%s) - $(stat -f %m "$LOCKDIR" 2>/dev/null || echo 0) ))
    if (( _age < 300 )); then exit 0; fi
    rm -rf "$LOCKDIR"; mkdir "$LOCKDIR" 2>/dev/null || exit 0
fi
echo $$ > "$LOCKDIR/pid"
trap 'rm -rf "$LOCKDIR"' EXIT INT TERM

source "$BASE/venv/bin/activate" 2>/dev/null || true

now=$(date +%s)
echo "" >> "$LOG"
echo "=== Wakeup check $(date) ===" >> "$LOG"

# Self-heal: fix exit 78 on ALL plists before doing anything else
for _plist in "$HOME/Library/LaunchAgents"/com.prasad.jobtracker.*.plist; do
    _label=$(basename "$_plist" .plist)
    _exit=$(launchctl print gui/$(id -u)/$_label 2>/dev/null | grep "last exit code" | grep -o "[0-9]*" | head -1)
    if [[ "$_exit" == "78" ]]; then
        echo "  [fix78] $_label — reloading" >> "$LOG"
        launchctl remove "$_label" 2>/dev/null
        sleep 1
        launchctl load "$_plist" 2>/dev/null
        echo "  [fix78] $_label — reloaded" >> "$LOG"
    fi
done

# Jobs: "module|health_file|max_gap_sec"
JOBS=(
    "aggregator|health_aggregator|28800"
    "scripts/send_scheduled|health_send_scheduled|86400"
    "scripts/process_bounces|health_process_bounces|3600"
    "scripts/nightly_digest|health_nightly_digest|108000"
    "outreach|health_outreach|108000"
    "scripts/cleanup_not_applied|health_cleanup_not_applied|108000"
    "scripts/build_auto_blacklist|health_build_auto_blacklist|108000"
    "scripts/retry_simplify|health_retry_simplify|108000"
)

for JOB_DEF in "${JOBS[@]}"; do
    MODULE=$(echo "$JOB_DEF" | cut -d'|' -f1)
    HEALTH=$(echo "$JOB_DEF"  | cut -d'|' -f2)
    MAX_GAP=$(echo "$JOB_DEF" | cut -d'|' -f3)
    HEALTH_FILE="$BASE/.local/${HEALTH}.json"

    [[ ! -f "$HEALTH_FILE" ]] && continue

    last_run=$(python3 -c "
import json, datetime
try:
    d = json.load(open('$HEALTH_FILE'))
    lr = d.get('last_run','')
    if lr:
        dt = datetime.datetime.strptime(lr, '%Y-%m-%dT%H:%M:%SZ')
        print(int(dt.timestamp()))
    else:
        print(0)
except:
    print(0)
" 2>/dev/null)

    [[ -z "$last_run" || "$last_run" == "0" ]] && continue

    gap=$((now - last_run))

    if [[ $gap -gt $MAX_GAP ]]; then
        echo "  [$MODULE] Missed — ${gap}s since last run — running now" >> "$LOG"
        bash "$SCRIPTS" "$MODULE" >> "$LOG" 2>&1
        echo "  [$MODULE] Done" >> "$LOG"
    else
        echo "  [$MODULE] OK — ${gap}s since last run" >> "$LOG"
    fi
done

echo "=== Wakeup done ===" >> "$LOG"

# Trim log to 500 lines
if [[ -f "$LOG" ]]; then
    lines=$(wc -l < "$LOG")
    if [[ $lines -gt 500 ]]; then
        tail -400 "$LOG" > "${LOG}.tmp" && mv "${LOG}.tmp" "$LOG"
    fi
fi
