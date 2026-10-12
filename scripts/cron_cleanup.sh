#!/bin/bash
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
cd "/Users/prasadkanade/Documents/Prasad Kanade/Job Hunt Tracker"
source venv/bin/activate

LOG_DIR=".local/cron_logs"
mkdir -p "$LOG_DIR"
TIMESTAMP=$(date '+%Y-%m-%d_%H-%M')
LOG_FILE="$LOG_DIR/cleanup_${TIMESTAMP}.log"
LAST_RUN_FILE=".local/cleanup_last_run.txt"
MIN_DAYS=2

echo "=== Cleanup check at $(date) ===" >> "$LOG_FILE"

# 2-day gate: skip if last run was less than MIN_DAYS days ago
if [ -f "$LAST_RUN_FILE" ]; then
    LAST_RUN=$(cat "$LAST_RUN_FILE")
    TODAY=$(date '+%Y-%m-%d')
    LAST_TS=$(date -j -f "%Y-%m-%d" "$LAST_RUN" "+%s" 2>/dev/null || date -d "$LAST_RUN" "+%s" 2>/dev/null)
    NOW_TS=$(date '+%s')
    DIFF_DAYS=$(( (NOW_TS - LAST_TS) / 86400 ))

    if [ "$DIFF_DAYS" -lt "$MIN_DAYS" ]; then
        echo "  Skipping: last run was $DIFF_DAYS day(s) ago (min: $MIN_DAYS)" >> "$LOG_FILE"
        echo "=== Cleanup skipped ===" >> "$LOG_FILE"
        exit 0
    fi
fi

echo "  Running cleanup (last run gate passed)" >> "$LOG_FILE"
python3 scripts/cleanup_not_applied.py >> "$LOG_FILE" 2>&1

# Record last run date
date '+%Y-%m-%d' > "$LAST_RUN_FILE"

echo "=== Cleanup finished at $(date) ===" >> "$LOG_FILE"
find "$LOG_DIR" -name "*.log" -mtime +7 -delete
