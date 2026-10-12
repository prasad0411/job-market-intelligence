#!/usr/bin/env python3
"""
Realign rows that discarded_auditor wrote one column to the right.

Discarded Entries has no Resume column, so from index 9 its layout diverges
from Valid Entries by one. The auditor read Valid's indices against a
Discarded row, so every rescued row landed shifted:

    column        held                        should hold
    Resume        the Discarded Remote? value SDE
    Remote?       a date                      the remote value
    Source        the Sponsorship value       the source
    Sponsorship   "Unknown" (hardcoded)       the sponsorship

90 rows are affected. The writer is fixed; this realigns what it already
wrote.

Recoverable: sponsorship, which the bug pushed into Source, and the remote
value, which it pushed into Resume. Not recoverable: the true source - index
11 was never read, so that value never reached the sheet. Those become
"Rescued", which is accurate.

A deliberately set Resume ("Tailored", "ML") is kept: those were chosen by
hand after the rescue and must not be overwritten.

Rows are identified by a date in the Remote? column. Nothing else writes one
there, so the signature cannot match a healthy row.

Only four cells per row change, in place. No row moves, nothing is deleted,
and a snapshot is written first.

    python3 scripts/repair_shifted_rows.py            # dry run
    python3 scripts/repair_shifted_rows.py --apply
"""
import argparse
import csv
import datetime
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

TAB = "Valid Entries"
SNAP_DIR = os.path.join(".local", "snapshots")

DATE = re.compile(r"^\d{1,2}-[A-Za-z]{3}-\d{4}$")
REMOTE_VALUES = {"remote", "on site", "onsite", "hybrid", "unknown", "no", "yes"}
RESUME_CODES = {"sde", "ml", "data", "tailored", "swe", "fullstack"}
SPONSOR_VALUES = {"yes", "no", "unknown"}


def is_shifted(remote):
    """A date in Remote? is the signature; nothing else writes one there."""
    return bool(DATE.match((remote or "").strip()))


def repair(resume, remote, source, sponsorship):
    """Corrected (resume, remote, source, sponsorship). Entry Date is already right."""
    r = (resume or "").strip()
    src = (source or "").strip()
    spon = (sponsorship or "").strip()

    # Sponsorship was pushed into Source; take it back when it looks like one.
    new_spon = src if src.lower() in SPONSOR_VALUES else (spon or "Unknown")
    # The remote value was pushed into Resume.
    new_remote = r if r.lower() in REMOTE_VALUES else "Unknown"
    # Keep a Resume code that was set deliberately.
    new_resume = r if r.lower() in RESUME_CODES else "SDE"
    # The true source never reached the sheet.
    new_source = "Rescued"
    return new_resume, new_remote, new_source, new_spon


def col_letter(i):
    """0-based index to A1 column letter."""
    s = ""
    i += 1
    while i:
        i, rem = divmod(i - 1, 26)
        s = chr(65 + rem) + s
    return s


def snapshot(rows):
    os.makedirs(SNAP_DIR, exist_ok=True)
    p = os.path.join(SNAP_DIR, "valid_entries_shiftfix_%s.csv"
                     % datetime.datetime.now().strftime("%Y%m%d_%H%M%S"))
    with open(p, "w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerows(rows)
    print("  snapshot: %s (%d rows)" % (p, len(rows)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    from outreach.outreach_data import Sheets
    ws = Sheets().ws.spreadsheet.worksheet(TAB)
    data = ws.get_all_values()
    header = data[0]
    idx = {n: i for i, n in enumerate(header) if n}

    need = ("Resume", "Remote?", "Entry Date", "Source", "Sponsorship")
    missing = [n for n in need if n not in idx]
    if missing:
        sys.exit("columns not found in %s: %s" % (TAB, missing))

    def cell(r, n):
        i = idx[n]
        return r[i].strip() if len(r) > i else ""

    fixes = []
    for row_no, r in enumerate(data[1:], start=2):
        if not is_shifted(cell(r, "Remote?")):
            continue
        before = (cell(r, "Resume"), cell(r, "Remote?"),
                  cell(r, "Source"), cell(r, "Sponsorship"))
        after = repair(before[0], before[1], before[2], before[3])
        fixes.append((row_no, cell(r, "Company"), before, after))

    print("=" * 84)
    print("SHIFT REPAIR: %d row(s) with a date in Remote?" % len(fixes))
    print("=" * 84)
    for row_no, co, b, af in fixes[:20]:
        print("  row %-5d %-22s" % (row_no, co[:22]))
        print("        resume %-12s -> %-12s   remote %-12s -> %s"
              % (b[0][:12], af[0][:12], b[1][:12], af[1]))
        print("        source %-12s -> %-12s   spons  %-12s -> %s"
              % (b[2][:12], af[2][:12], b[3][:12], af[3]))
    if len(fixes) > 20:
        print("  ... and %d more" % (len(fixes) - 20))

    if not fixes:
        print("\nnothing to repair")
        return 0
    if not a.apply:
        print("\nDRY RUN - nothing written. Re-run with --apply.")
        return 0

    snapshot(data)

    # One batch_update rather than 360 individual writes: four cells per row
    # across 90 rows would otherwise take six minutes against the write quota.
    body = []
    for row_no, _co, _b, af in fixes:
        for name, val in zip(("Resume", "Remote?", "Source", "Sponsorship"), af):
            body.append({"range": "%s%d" % (col_letter(idx[name]), row_no),
                         "values": [[val]]})

    print("\nwriting %d cells across %d rows..." % (len(body), len(fixes)))
    CHUNK = 200
    written = 0
    for i in range(0, len(body), CHUNK):
        part = body[i:i + CHUNK]
        try:
            ws.batch_update(part, value_input_option="USER_ENTERED")
            written += len(part)
            time.sleep(1.2)
        except Exception as e:
            print("  batch %d failed: %s" % (i // CHUNK + 1, str(e)[:70]))

    print("\n" + "=" * 84)
    print("wrote %d of %d cells" % (written, len(body)))
    if written != len(body):
        print("MISMATCH: re-run the dry run to see what remains.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
