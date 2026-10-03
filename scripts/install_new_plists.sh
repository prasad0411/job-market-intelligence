#!/bin/bash
# Install new launchd agents for send_scheduled and nightly_digest
set -e
PLIST_DIR="/Users/prasadkanade/Documents/Prasad Kanade/Job Hunt Tracker/scripts"
LAUNCH_DIR="$HOME/Library/LaunchAgents"
mkdir -p "$LAUNCH_DIR"

for plist in send digest; do
    src="$PLIST_DIR/com.prasad.jobtracker.${plist}.plist"
    dst="$LAUNCH_DIR/com.prasad.jobtracker.${plist}.plist"
    cp "$src" "$dst"
    launchctl unload "$dst" 2>/dev/null || true
    launchctl load "$dst"
    echo "Loaded: com.prasad.jobtracker.$plist"
done

echo ""
echo "Scheduled jobs:"
launchctl list | grep com.prasad
echo ""
echo "Send scheduled: 9:00, 10:30, 11:30, 12:30 ET"
echo "Nightly digest: 12:22 AM"
