#!/bin/bash
# resume_sync.sh — auto-sync latest resumes from Downloads to .local/
# Run by launchd on wake, before aggregator/outreach

DOWNLOADS="/Users/prasadkanade/Downloads"
LOCAL="/Users/prasadkanade/Documents/Prasad Kanade/Job Hunt Tracker/.local"
LOG="$LOCAL/resume_sync.log"

echo "=== Resume sync at $(date) ===" >> "$LOG"

sync_resume() {
    local filename="$1"
    local label="$2"
    local src="$DOWNLOADS/$filename"
    local dst="$LOCAL/$filename"

    if [ -f "$src" ]; then
        if [ ! -f "$dst" ] || [ "$src" -nt "$dst" ]; then
            cp "$src" "$dst"
            echo "  ✓ Updated $label resume from Downloads" >> "$LOG"
        else
            echo "  ℹ $label resume already up to date" >> "$LOG"
        fi
    elif [ -f "$dst" ]; then
        echo "  ⚠ $label resume not in Downloads — using existing .local copy" >> "$LOG"
    else
        echo "  ✗ MISSING $label resume — not in Downloads AND not in .local!" >> "$LOG"
        # Write to failures.log so watchdog/digest picks it up
        echo "[$(date)] MISSING RESUME: $filename not found anywhere" >> "$LOCAL/../.local/failures.log" 2>/dev/null || true
    fi
}

sync_resume "Prasad Kanade SWE Resume.pdf" "SWE"
sync_resume "Prasad Kanade ML Resume.pdf" "ML"
sync_resume "Prasad Kanade Data Resume.pdf" "Data"

echo "=== Resume sync done ===" >> "$LOG"
