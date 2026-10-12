#!/bin/bash
# install_launchd.sh — replaces crontab with launchd agents
# Run once from project root: bash scripts/install_launchd.sh

set -e
LAUNCH_AGENTS="$HOME/Library/LaunchAgents"
PROJECT="/Users/prasadkanade/Documents/Prasad Kanade/Job Hunt Tracker"
SCRIPTS="$PROJECT/scripts"

echo "=== Installing launchd agents ==="

# 1. Copy plist files to LaunchAgents
cp "$SCRIPTS/com.prasad.jobtracker.aggregator.plist" "$LAUNCH_AGENTS/"
cp "$SCRIPTS/com.prasad.jobtracker.cleanup.plist" "$LAUNCH_AGENTS/"
echo "  ✓ Plist files copied to ~/Library/LaunchAgents/"

# 2. Make resume sync executable
chmod +x "$SCRIPTS/resume_sync.sh"
echo "  ✓ resume_sync.sh made executable"

# 3. Unload old agents if already loaded
launchctl unload "$LAUNCH_AGENTS/com.prasad.jobtracker.aggregator.plist" 2>/dev/null || true
launchctl unload "$LAUNCH_AGENTS/com.prasad.jobtracker.cleanup.plist" 2>/dev/null || true

# 4. Load new agents
launchctl load "$LAUNCH_AGENTS/com.prasad.jobtracker.aggregator.plist"
launchctl load "$LAUNCH_AGENTS/com.prasad.jobtracker.cleanup.plist"
echo "  ✓ launchd agents loaded"

# 5. Verify they loaded
echo ""
echo "Loaded agents:"
launchctl list | grep "com.prasad.jobtracker"

# 6. Remove old crontab entries
crontab -r 2>/dev/null || true
echo "  ✓ Old crontab removed"

echo ""
echo "=== Done! launchd will now:"
echo "  • Run aggregator at 8 AM, 3 PM, 9 PM (catches up if Mac was asleep)"
echo "  • Run cleanup at 7 AM every 2 days (catches up if Mac was asleep)"
echo "  • Sync resumes from Downloads before each run"
echo ""
echo "To check status:  launchctl list | grep com.prasad"
echo "To unload:        launchctl unload ~/Library/LaunchAgents/com.prasad.jobtracker.aggregator.plist"
