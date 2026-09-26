#!/usr/bin/env python3
"""
scripts/build_auto_blacklist.py

Reads Discarded Entries from Google Sheets and auto-promotes companies
that have been rejected 3+ times for the same reason to COMPANY_BLACKLIST.

Run weekly:
    python3 scripts/build_auto_blacklist.py

Dry run (see what would be added without changing anything):
    python3 scripts/build_auto_blacklist.py --dry-run
"""

# Socket ceiling, signal handling, stall watchdog. The repo root has to
# go on sys.path first: these run as `python3 scripts/NAME.py`, so
# Python puts scripts/ on the path, not the root, and a plain
# `import scripts._resilient` raises ModuleNotFoundError.
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import scripts._resilient  # noqa: E402,F401


import sys, os, re, json
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DRY_RUN = "--dry-run" in sys.argv
MIN_REJECTIONS = 3  # promote after this many same-reason rejections

# Never auto-blacklist these — they are ATS platforms or garbage extraction artifacts
NEVER_BLACKLIST = {
    "myworkdayjobs", "corporate office", "usa", "us", "simplify",
    "unknown", "company", "careers", "jobs", "external", "portal",
    "job-boards", "worldwide", "ats", "marketing", "learning",
    "coherent",  # has both valid CS roles and hardware roles — don't blanket blacklist
}

# Reasons that qualify for auto-blacklist
AUTO_BLACKLIST_REASONS = [
    "security clearance",
    "clearance required",
    "us person",
    "citizenship required",
    "no.*sponsor",
    "hardware",
    "laser",
    "optics",
    "skillbridge",
    "military",
]

def reason_qualifies(reason):
    r = reason.lower()
    return any(re.search(pat, r) for pat in AUTO_BLACKLIST_REASONS)

def main():
    from aggregator.config import SHEETS_CREDS_FILE, SHEET_NAME, DISCARDED_WORKSHEET
    import gspread
    from oauth2client.service_account import ServiceAccountCredentials

    scope = ["https://spreadsheets.google.com/feeds",
             "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_name(SHEETS_CREDS_FILE, scope)
    client = gspread.authorize(creds)
    sheet = client.open(SHEET_NAME).worksheet(DISCARDED_WORKSHEET)
    rows = sheet.get_all_values()[1:]  # skip header
    print(f"Loaded {len(rows)} discarded entries")

    # Count: company → reason → count
    counts = defaultdict(lambda: defaultdict(int))
    for row in rows:
        if len(row) < 3:
            continue
        company = row[2].strip()
        reason = row[1].strip() if len(row) > 1 else ""
        if company and reason and reason_qualifies(reason):
            short = reason.split("(")[0].strip()[:60]
            counts[company][short] += 1

    # Load existing blacklist
    config_path = os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "aggregator", "config.py")
    with open(config_path, encoding="utf-8") as f:
        config = f.read()

    existing_match = re.search(
        r"COMPANY_BLACKLIST\s*=\s*\[([^\]]+)\]", config, re.DOTALL)
    existing = set()
    if existing_match:
        existing = set(re.findall(r'"([^"]+)"', existing_match.group(1)))
    print(f"Existing blacklist: {len(existing)} companies")

    # Find candidates
    candidates = []
    for company, reason_counts in counts.items():
        if company in existing:
            continue
        if company.lower().strip() in NEVER_BLACKLIST:
            continue
        for reason, count in reason_counts.items():
            if count >= MIN_REJECTIONS:
                candidates.append((company, reason, count))
                break

    if not candidates:
        print("No new auto-blacklist candidates found.")
        return

    print(f"\nCandidates ({len(candidates)}):")
    for company, reason, count in sorted(candidates, key=lambda x: -x[2]):
        print(f"  {company}: '{reason}' x{count}")

    if DRY_RUN:
        print("\n[DRY RUN] No changes made. Remove --dry-run to apply.")
        return

    # Append to COMPANY_BLACKLIST and COMPANY_BLACKLIST_REASONS in config.py
    new_companies = [c for c, _, _ in candidates]
    new_reasons = {c: r for c, r, _ in candidates}

    # Insert into COMPANY_BLACKLIST
    for company in new_companies:
        config = re.sub(
            r"(COMPANY_BLACKLIST\s*=\s*\[)",
            f'\\1\n    "{company}",',
            config, count=1
        )
    # Insert into COMPANY_BLACKLIST_REASONS
    for company, reason in new_reasons.items():
        config = re.sub(
            r"(COMPANY_BLACKLIST_REASONS\s*=\s*\{)",
            f'\\1\n    "{company}": "Auto-blacklisted: {reason}",',
            config, count=1
        )

    # Backup before modifying, verify syntax after
    import shutil as _sh
    backup = config_path + f".bak_{__import__('datetime').date.today().isoformat()}"
    _sh.copy2(config_path, backup)
    tmp_path = config_path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        f.write(config)
    # Syntax check before replacing
    import ast as _ast
    try:
        _ast.parse(config)
    except SyntaxError as _se:
        os.remove(tmp_path)
        print(f"  ✗ Config syntax error after edit — ABORTED: {_se}")
        print(f"  Original config preserved. Backup at: {backup}")
        return
    os.replace(tmp_path, config_path)
    os.remove(backup)  # clean up backup only after successful write

    print(f"\nAdded {len(new_companies)} companies to COMPANY_BLACKLIST:")
    for c in new_companies:
        print(f"  + {c}")
    print(f"\nConfig updated: {config_path}")
    print("Run the aggregator on next schedule — new blacklist takes effect immediately.")
    try:
        from outreach.brain import Brain
        b = Brain.get()
        for company, reason, count in candidates:
            b.record_company_rejection(company, reason)

        # Generate weekly review from Brain data
        review = b.get_blacklist_review()
        auto = review.get("auto_approved", [])
        pending = review.get("pending_review", [])

        lines = [f"BLACKLIST REVIEW — Week of {__import__('datetime').date.today().strftime('%b %d')}\n"]

        if auto:
            lines.append("AUTO-APPROVED (structural reasons — safe to approve):")
            for c in auto:
                lines.append(f"  ✓ {c['name']} — '{c['reason']}' x{c['count']} ({c['distinct_jobs']} jobs)")
            lines.append("\nTo apply: python3 -c \"from outreach.brain import Brain; Brain.get().apply_approved_blacklist(" +
                         repr([c['name'] for c in auto]) + ")\"\n")
        else:
            lines.append("AUTO-APPROVED: none this week\n")

        if pending:
            lines.append("PENDING YOUR REVIEW:")
            for c in pending:
                mixed = " (has mixed roles — DO NOT blacklist)" if c["has_mixed_roles"] else ""
                outreached = f" (outreached {c['outreach_count']}x — DO NOT blacklist)" if c["outreach_count"] > 0 else ""
                lines.append(f"  ? {c['name']} — '{c['reason']}' x{c['count']}{mixed}{outreached}")
        else:
            lines.append("PENDING REVIEW: none this week")

        if new_companies:
            lines.append(f"\nCONFIG UPDATED: {len(new_companies)} companies added to COMPANY_BLACKLIST")
            for c in new_companies:
                lines.append(f"  + {c}")

        report = "\n".join(lines)
        print("\n" + report)

        subject = f"🔍 Blacklist Review — {len(auto)} auto-approved, {len(pending)} pending"
        if auto or pending or new_companies:
            b.send_email_alert(subject, report)
            print(f"\nReview email sent to prasadckanade@gmail.com")

    except Exception as _be:
        print(f"Brain sync failed (non-fatal): {_be}")

if __name__ == "__main__":
    main()
