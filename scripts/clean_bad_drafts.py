#!/usr/bin/env python3
"""
Clean bad/stale drafts from 'Scheduled Outreach'.
- Bounced recipient or blacklisted domain -> bad, remove
- Older than STALE_DAYS and never sent -> remove
Moves them to Deleted Items (recoverable), never hard-deletes.
Dry-run by default; pass --apply to actually move.
"""

# Socket ceiling, signal handling, stall watchdog. The repo root has to
# go on sys.path first: these run as `python3 scripts/NAME.py`, so
# Python puts scripts/ on the path, not the root.
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import scripts._resilient  # noqa: E402,F401

import os, sys, json, datetime
sys.path.insert(0, os.getcwd())
from scripts import send_scheduled as ss

STALE_DAYS = 7
L = os.path.join(os.getcwd(), ".local")

def _load(f):
    try: return json.load(open(os.path.join(L, f)))
    except: return {}

def main():
    apply = "--apply" in sys.argv
    bounced = {k.lower() for k in _load("bounced_emails.json")}
    bounced |= {k.lower() for k in _load("bounce_log.json")}
    blacklist = {str(k).lower() for k in _load("pattern_blacklist.json")}

    token = ss._get_token()
    fid = ss._get_folder_id(token, ss.SCHEDULED_FOLDER)
    if not fid:
        print("No Scheduled Outreach folder"); return
    deleted_fid = ss._get_folder_id(token, "Deleted Items") or ss._ensure_folder(token, "Deleted Items")

    drafts = ss._get_drafts_in_folder(token, fid)
    print(f"Scanning {len(drafts)} drafts")
    now = datetime.datetime.now(datetime.timezone.utc)
    removed = 0
    for d in drafts:
        to_email = (ss._header(d, "To") or "").lower().strip()
        if not to_email:
            # fallback to toRecipients
            try:
                to_email = d["toRecipients"][0]["emailAddress"]["address"].lower().strip()
            except Exception:
                to_email = ""
        dom = to_email.split("@")[-1] if "@" in to_email else ""
        reason = None
        if to_email and to_email in bounced:
            reason = "bounced"
        elif dom and dom in blacklist:
            reason = "blacklisted domain"
        else:
            created = d.get("createdDateTime")
            if created:
                try:
                    cdt = datetime.datetime.fromisoformat(created.replace("Z","+00:00"))
                    if (now - cdt).days > STALE_DAYS:
                        reason = f"stale >{STALE_DAYS}d"
                except Exception:
                    pass
        if reason:
            print(f"  {'MOVED' if apply else 'WOULD MOVE'} [{reason}]: {to_email}")
            if apply:
                ss._move(token, d["id"], deleted_fid)
            removed += 1
    print(f"\n{'MOVED' if apply else 'WOULD MOVE'} {removed} bad/stale drafts to Deleted Items")
    if not apply:
        print("DRY RUN — re-run with --apply to actually move them")

if __name__ == "__main__":
    main()
