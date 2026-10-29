#!/usr/bin/env python3
"""
Order the unapplied rows so the strongest visa sponsors sit at the top.

Applied and Rejected rows never move. They are application history and their
position is part of how the sheet reads, so they act as fixed anchors. Only
the Not Applied rows are permuted, and only across the row indices that Not
Applied rows already occupy.

Ranking, best first:

    1  strong      employer filed 100 or more H1B positions in FY2025
    2  moderate    20 to 99
    3  low         1 to 19
    4  unknown     no match in the DOL data, which is not a refusal
    5  no          the posting itself says no sponsorship

Tier five outranks everything because a posting that says it will not
sponsor is about this role, while the DOL record is about the employer
historically.

Within a tier, more filings first, then company then title so the order is
stable across runs and a rerun with no new data is a no-op.

    python3 scripts/rank_by_sponsorship.py            # dry run
    python3 scripts/rank_by_sponsorship.py --apply
"""
import argparse
import collections
import csv
import datetime
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

TAB = "Valid Entries"
MOVABLE = "not applied"
SNAP_DIR = os.path.join(".local", "snapshots")

TIER_NAME = {1: "strong 100+", 2: "moderate 20-99", 3: "low 1-19",
             4: "unknown", 5: "no sponsorship"}


def col_letter(i):
    s, i = "", i + 1
    while i:
        i, rem = divmod(i - 1, 26)
        s = chr(65 + rem) + s
    return s


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    from outreach.outreach_data import Sheets
    from aggregator.h1b_sponsors import lookup

    ws = Sheets().ws.spreadsheet.worksheet(TAB)
    data = ws.get_all_values()
    if not data:
        sys.exit("%s is empty" % TAB)
    header = data[0]
    idx = {n: i for i, n in enumerate(header) if n}
    for need in ("Status", "Company", "Title"):
        if need not in idx:
            sys.exit("column %r not found" % need)

    def cell(r, n):
        i = idx.get(n)
        return r[i].strip() if i is not None and len(r) > i else ""

    body = data[1:]
    movable_idx = [i for i, r in enumerate(body)
                   if cell(r, "Status").lower() == MOVABLE]
    if not movable_idx:
        print("nothing to reorder")
        return 0

    def score(r):
        tier, pos, _m = lookup(cell(r, "Company"))
        if cell(r, "Sponsorship").lower() == "no":
            tier, pos = 5, 0
        # -pos so more filings sort first; company and title keep it stable
        return (tier, -pos, cell(r, "Company").lower(), cell(r, "Title").lower())

    movable = sorted((body[i] for i in movable_idx), key=score)

    print("=" * 78)
    print("SPONSORSHIP RANKING: %d movable of %d rows" % (len(movable_idx), len(body)))
    print("=" * 78)
    dist = collections.Counter(score(r)[0] for r in movable)
    for t in sorted(dist):
        print("   %-16s %4d" % (TIER_NAME[t], dist[t]))

    # how far anything actually moves, so a no-op run is visible as one
    moved = sum(1 for slot, r in zip(movable_idx, movable) if body[slot] is not r)
    print("\nrows whose position changes: %d" % moved)

    print("\ntop 12 after ranking:")
    for r in movable[:12]:
        t, np_, _c, _ti = score(r)
        print("   %-15s %-22s %-38s %5d"
              % (TIER_NAME[t], cell(r, "Company")[:22], cell(r, "Title")[:38], -np_))

    if not a.apply:
        print("\nDRY RUN - nothing written. Re-run with --apply.")
        return 0
    if moved == 0:
        print("\nalready in order, nothing to write")
        return 0

    os.makedirs(SNAP_DIR, exist_ok=True)
    snap = os.path.join(SNAP_DIR, "valid_rank_%s.csv"
                        % datetime.datetime.now().strftime("%Y%m%d_%H%M%S"))
    with open(snap, "w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerows(data)
    print("\nsnapshot: %s" % snap)

    width = len(header)
    last = col_letter(width - 1)
    payload = []
    for slot, content in zip(movable_idx, movable):
        row_no = slot + 2                      # +1 for header, +1 for 1-based
        vals = list(content) + [""] * (width - len(content))
        payload.append({"range": "A%d:%s%d" % (row_no, last, row_no),
                        "values": [vals[:width]]})

    print("writing %d rows..." % len(payload))
    CHUNK = 100
    written = 0
    for i in range(0, len(payload), CHUNK):
        part = payload[i:i + CHUNK]
        try:
            ws.batch_update(part, value_input_option="USER_ENTERED")
            written += len(part)
            time.sleep(1.2)
        except Exception as e:
            print("  chunk %d failed: %s" % (i // CHUNK + 1, str(e)[:70]))

    print("wrote %d of %d rows" % (written, len(payload)))
    if written != len(payload):
        print("MISMATCH: rerun the dry run to see the current state.")
        return 1

    # verify: the anchors must be exactly where they were
    time.sleep(2)
    after = ws.get_all_values()[1:]
    bad = [i for i in range(len(body))
           if i not in movable_idx and i < len(after) and after[i] != body[i]]
    if bad:
        print("WARNING: %d anchor row(s) changed, first at sheet row %d"
              % (len(bad), bad[0] + 2))
        return 1
    print("verified: every Applied and Rejected row is unmoved")
    return 0


if __name__ == "__main__":
    sys.exit(main())
