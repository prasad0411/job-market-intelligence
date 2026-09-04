#!/bin/bash
BASE_DIR="/Users/prasadkanade/Documents/Prasad Kanade/Job Hunt Tracker"
cd "$BASE_DIR" || { echo "FATAL: Cannot cd to $BASE_DIR"; exit 1; }
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
export HOME="/Users/prasadkanade"
source venv/bin/activate || { echo "FATAL: Cannot activate venv"; exit 1; }
MODULE="$1"
if [[ -z "$MODULE" ]]; then echo "FATAL: No module specified"; exit 1; fi

# ---------------------------------------------------------------------------
# CONCURRENCY GUARD
# Seven aggregators ran at once on 2026-09-03 because wakeup_recovery.sh
# invokes this script directly, bypassing scheduler.py and its disk lock.
# The lock belongs HERE, the single chokepoint every caller shares.
#
# mkdir, not flock: flock ships with util-linux and is absent from stock
# macOS, so the flock call in wakeup_recovery.sh silently does nothing.
# mkdir is atomic on every POSIX filesystem and needs no dependency.
# ---------------------------------------------------------------------------
LOCK_NAME=$(echo "$MODULE" | tr '/' '_')
LOCK_DIR="$BASE_DIR/.local/run_${LOCK_NAME}.lock"
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
    OLD_PID=$(cat "$LOCK_DIR/pid" 2>/dev/null)
    if [[ -n "$OLD_PID" ]] && kill -0 "$OLD_PID" 2>/dev/null; then
        echo "[$(date)] SKIP $MODULE — already running as pid $OLD_PID" \
            >> "$BASE_DIR/.local/concurrency.log"
        exit 0
    fi
    # Holder is dead. Reclaim only if the lock is also stale, so a fresh
    # process that has not yet written its pid file is never stolen from.
    if [[ -d "$LOCK_DIR" ]]; then
        AGE=$(( $(date +%s) - $(stat -f %m "$LOCK_DIR" 2>/dev/null \
                 || stat -c %Y "$LOCK_DIR" 2>/dev/null || echo 0) ))
        if (( AGE < 60 )); then
            echo "[$(date)] SKIP $MODULE — lock ${AGE}s old, holder starting" \
                >> "$BASE_DIR/.local/concurrency.log"
            exit 0
        fi
        echo "[$(date)] RECLAIM $MODULE — stale lock, dead pid $OLD_PID, ${AGE}s" \
            >> "$BASE_DIR/.local/concurrency.log"
        rm -rf "$LOCK_DIR"
        mkdir "$LOCK_DIR" 2>/dev/null || exit 0
    fi
fi
echo $$ > "$LOCK_DIR/pid"
trap 'rm -rf "$LOCK_DIR"' EXIT INT TERM

# Record the START time as well. health_*.json is written only on completion,
# so wakeup_recovery.sh measured staleness from the previous run's END and
# concluded a currently executing run was overdue.
printf '{"module":"%s","started":"%s","pid":%s}\n' \
    "$MODULE" "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$$" \
    > "$BASE_DIR/.local/running_${LOCK_NAME}.json"
trap 'rm -rf "$LOCK_DIR"; rm -f "$BASE_DIR/.local/running_'"$LOCK_NAME"'.json"' EXIT INT TERM
LOG_SAFE_NAME=$(basename "$MODULE")
LOG_DIR="$BASE_DIR/.local/cron_logs"
mkdir -p "$LOG_DIR"
TIMESTAMP=$(date '+%Y-%m-%d_%H-%M')
LOG_FILE="$LOG_DIR/${LOG_SAFE_NAME}_${TIMESTAMP}.log"
HEALTH_FILE="$BASE_DIR/.local/health_${LOG_SAFE_NAME}.json"
echo "=== [$MODULE] started at $(date) ===" >> "$LOG_FILE"
echo "=== Python: $(python3 --version 2>&1) | User: $(whoami) ===" >> "$LOG_FILE"
if [[ "$MODULE" == "outreach" || "$MODULE" == "scripts/send_scheduled" ]]; then
    echo "--- resume_sync ---" >> "$LOG_FILE"
    timeout 30 bash scripts/resume_sync.sh >> "$LOG_FILE" 2>&1 || true
fi
START_TS=$(date +%s)
if [[ "$MODULE" == scripts/* ]]; then
    python3 "${MODULE}.py" >> "$LOG_FILE" 2>&1
else
    python3 -m "$MODULE" >> "$LOG_FILE" 2>&1
fi
EXIT_CODE=$?
END_TS=$(date +%s)
DURATION=$((END_TS - START_TS))
echo "=== [$MODULE] finished at $(date) | exit: $EXIT_CODE | duration: ${DURATION}s ===" >> "$LOG_FILE"
cat > "$HEALTH_FILE" <<EOF
{
  "module": "$MODULE",
  "last_run": "$(date -u '+%Y-%m-%dT%H:%M:%SZ')",
  "exit_code": $EXIT_CODE,
  "duration_seconds": $DURATION,
  "log": "$LOG_FILE"
}
EOF
if [[ $EXIT_CODE -ne 0 ]]; then
    echo "[$(date)] FAILED: $MODULE (exit $EXIT_CODE) — $LOG_FILE" >> "$BASE_DIR/.local/failures.log"
fi
find "$LOG_DIR" -name "*.log" -mtime +7 -delete
exit $EXIT_CODE
