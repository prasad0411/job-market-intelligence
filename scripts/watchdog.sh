#!/bin/bash
# ============================================================
# self_healing_watchdog.sh — fully autonomous self-healing watchdog
# Compatible with macOS bash 3.2 (no associative arrays)
# ============================================================

BASE_DIR="/Users/prasadkanade/Documents/Prasad Kanade/Job Hunt Tracker"
LOG="$BASE_DIR/.local/watchdog.log"
ALERT_LOG="$BASE_DIR/.local/watchdog_alerts.log"
LOCAL="$BASE_DIR/.local"
SCRIPTS="$BASE_DIR/scripts"
mkdir -p "$LOCAL"

if [[ -f "$LOG" ]] && [[ $(stat -f%z "$LOG" 2>/dev/null || echo 0) -gt 512000 ]]; then
    mv "$LOG" "${LOG}.$(date +%Y%m%d)"
    find "$LOCAL" -name "watchdog.log.*" -mtime +7 -delete
fi

log()   { echo "  $1" >> "$LOG"; }
send_alert() { alert "$1 - $2"; }

alert() {
    echo "[$(date)] $1" >> "$ALERT_LOG"
    osascript -e "display notification \"$1\" with title \"Job Tracker Watchdog\" sound name \"Basso\"" 2>/dev/null
}

echo "" >> "$LOG"
echo "=== Watchdog $(date) ===" >> "$LOG"

NOW=$(date +%s)
FAILURES=0
RESTARTS=0

# Jobs: "health_name|cron_runner_module|max_age_sec"
# No launchd labels — scheduler daemon owns all jobs now
# Watchdog just re-runs via cron_runner.sh if stale/failed
JOBS=(
    "aggregator|aggregator|28800"
    "send_scheduled|scripts/send_scheduled|86400"
    "process_bounces|scripts/process_bounces|3600"
    "nightly_digest|scripts/nightly_digest|108000"
    "outreach|outreach|108000"
    "cleanup_not_applied|scripts/cleanup_not_applied|108000"
    "build_auto_blacklist|scripts/build_auto_blacklist|108000"
    "retry_simplify|scripts/retry_simplify|108000"
)

auto_fix_pycache() {
    find "$BASE_DIR/scripts/__pycache__" "$BASE_DIR/outreach/__pycache__" "$BASE_DIR/aggregator/__pycache__" \
         -name "*.pyc" -mtime +1 -delete 2>/dev/null
    log "  [autofix] Cleared stale .pyc files"
}

restart_agent() {
    # No-op: launchd labels no longer used.
    # Jobs are re-run directly via cron_runner.sh below.
    log "  [restart] (launchd restart disabled — scheduler daemon owns jobs)"
}

for JOB_DEF in "${JOBS[@]}"; do
    HEALTH_NAME=$(echo "$JOB_DEF" | cut -d'|' -f1)
    CRON_MODULE=$(echo "$JOB_DEF" | cut -d'|' -f2)
    MAX_AGE=$(echo "$JOB_DEF"| cut -d'|' -f3)
    # Strip scripts/ prefix for health file name (matches cron_runner.sh basename behavior)
    HEALTH_FILE="$LOCAL/health_${HEALTH_NAME}.json"

    if [[ ! -f "$HEALTH_FILE" ]]; then
        log "[$HEALTH_NAME] No health file yet"
        continue
    fi

    EXIT_CODE=$(python3 -c "import json; d=json.load(open('$HEALTH_FILE')); print(d.get('exit_code','?'))" 2>/dev/null)
    LAST_RUN=$(python3  -c "import json; d=json.load(open('$HEALTH_FILE')); print(d.get('last_run',''))" 2>/dev/null)
    DURATION=$(python3  -c "import json; d=json.load(open('$HEALTH_FILE')); print(d.get('duration_seconds','?'))" 2>/dev/null)
    FILE_MOD=$(stat -f %m "$HEALTH_FILE" 2>/dev/null || echo 0)
    FILE_AGE=$(( NOW - FILE_MOD ))

    log "[$HEALTH_NAME] exit=$EXIT_CODE age=${FILE_AGE}s dur=${DURATION}s last=$LAST_RUN"

    if [[ "$EXIT_CODE" != "0" && "$EXIT_CODE" != "?" ]]; then
        FAILURES=$((FAILURES + 1))
        log "  ⚠ FAILED — auto-fixing and re-running job directly"
        alert "$HEALTH_NAME failed (exit $EXIT_CODE) — re-running"
        auto_fix_pycache
        sleep 2
        bash "$SCRIPTS/cron_runner.sh" "$CRON_MODULE" >> "$LOG" 2>&1 &
    fi

    if [[ $FILE_AGE -gt $MAX_AGE ]]; then
        log "  ⚠ STALE — ${FILE_AGE}s — re-running $HEALTH_NAME"
        auto_fix_pycache
        sleep 2
        bash "$SCRIPTS/cron_runner.sh" "$CRON_MODULE" >> "$LOG" 2>&1 &
    fi
done

# Docker/Reacher check
log "[docker] Checking Reacher..."
if docker ps 2>/dev/null | grep -q "reacher"; then
    log "[docker] Reacher ✓ running"
else
    log "[docker] Reacher ✗ NOT running — attempting restart"
    alert "Reacher Docker down — auto-starting"
    if docker info >/dev/null 2>&1; then
        cd "$BASE_DIR" && docker compose up -d >> "$LOG" 2>&1
        sleep 5
        docker ps 2>/dev/null | grep -q "reacher" && log "[docker] Reacher restarted ✓" || \
            { log "[docker] Reacher restart failed"; alert "Reacher restart FAILED — check manually"; }
    else
        open -a Docker 2>/dev/null
        sleep 30
        docker info >/dev/null 2>&1 && cd "$BASE_DIR" && docker compose up -d >> "$LOG" 2>&1 && \
            log "[docker] Docker + Reacher started ✓" || \
            { log "[docker] Docker failed to start"; alert "Docker failed — open manually"; }
    fi
fi

# ── Jobright cookie freshness check ──
log "[jobright] Checking cookie freshness..."
JOBRIGHT_COOKIES=$(python3 -c "
import json, os, time
f = os.path.join('.local', 'jobright_cookies.json')
if not os.path.exists(f):
    print('missing')
else:
    try:
        data = json.load(open(f))
        saved = data.get('saved_at', 0)
        age_days = (time.time() - saved) / 86400
        if age_days > 25:
            print('stale:' + str(int(age_days)))
        else:
            print('ok:' + str(int(age_days)))
    except Exception:
        print('error')
" 2>/dev/null)

if [[ "$JOBRIGHT_COOKIES" == missing ]]; then
    send_alert "Jobright cookies missing" "jobright_cookies.json not found — Jobright source will fail. Please log in and refresh cookies."
    log "[jobright] ⚠ cookies MISSING"
elif [[ "$JOBRIGHT_COOKIES" == stale:* ]]; then
    AGE="${JOBRIGHT_COOKIES#stale:}"
    send_alert "Jobright cookies stale (${AGE}d)" "Jobright cookies are ${AGE} days old. Please refresh them soon — it is your best source at 27% valid rate."
    log "[jobright] ⚠ cookies STALE (${AGE}d)"
elif [[ "$JOBRIGHT_COOKIES" == ok:* ]]; then
    log "[jobright] ✓ cookies fresh (${JOBRIGHT_COOKIES#ok:}d)"
fi

# MS token check
# MS token check
log "[token] Checking MS token validity..."
TOKEN_FILE="$LOCAL/ms_token.json"
if [[ -f "$TOKEN_FILE" ]]; then
    # Actually verify token is valid, not just file age (file updates every ~1hr on refresh)
    TOKEN_VALID=$(python3 -c "
import json, sys, os
sys.path.insert(0, '$BASE_DIR')
try:
    import msal
    from outreach.outreach_config import MS_CLIENT_ID, MS_AUTHORITY, MS_SCOPES, MS_TOKEN_FILE
    cache = msal.SerializableTokenCache()
    cache.deserialize(open(MS_TOKEN_FILE).read())
    app = msal.PublicClientApplication(MS_CLIENT_ID, authority=MS_AUTHORITY, token_cache=cache)
    accts = app.get_accounts()
    result = app.acquire_token_silent(MS_SCOPES, account=accts[0]) if accts else None
    print('valid' if result and 'access_token' in result else 'expired')
except Exception as e:
    print('error')
" 2>/dev/null)
    if [[ "$TOKEN_VALID" == "valid" ]]; then
        log "[token] MS token ✓ valid"
    elif [[ "$TOKEN_VALID" == "expired" ]]; then
        log "[token] ⚠ MS token EXPIRED — run: python3 scripts/test_ms_auth.py"
        alert "MS token expired — run test_ms_auth.py"
    else
        log "[token] ⚠ Could not verify MS token"
    fi
else
    log "[token] ⚠ No MS token file found"
    alert "MS token missing — run: python3 scripts/test_ms_auth.py"
fi

log ""
log "Summary: $FAILURES failure(s), $RESTARTS restart(s)"
log "=== Watchdog done ==="

# Trim log to 1000 lines
if [[ -f "$LOG" ]]; then
    LINES=$(wc -l < "$LOG")
    if [[ $LINES -gt 1000 ]]; then
        tail -800 "$LOG" > "${LOG}.tmp" && mv "${LOG}.tmp" "$LOG"
    fi
fi
