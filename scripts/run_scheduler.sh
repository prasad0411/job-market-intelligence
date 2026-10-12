#!/bin/bash

# Self-maintenance before the scheduler loops: clears the quarantine
# flag that stopped launchd executing cron_runner.sh on 29 Sep, and
# removes locks whose owning process is dead.
bash "$(dirname "$0")/selfcheck.sh" || true

cd "/Users/prasadkanade/Documents/Prasad Kanade/Job Hunt Tracker"
source venv/bin/activate
exec python3 scripts/scheduler.py
