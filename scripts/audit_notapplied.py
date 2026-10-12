#!/usr/bin/env python3
"""
Three questions about the Not Applied rows:

  1. would the current filter still accept them
  2. why are rows older than the 3 day expiry still there
  3. which locations are malformed, and could they have been caught earlier

Read only. Writes nothing.
"""
import collections
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from outreach.outreach_data import Sheets                      # noqa: E402
from aggregator.processors import TitleProcessor as TP         # noqa: E402

try:
    from aggregator.title_gates import rejection_reason
except Exception:
    def rejection_reason(_t):
        return None


def getter(header):
    idx = {n: i for i, n in enumerate(header) if n}

    def get(row, name):
        i = idx.get(name)
        return row[i].strip() if i is not None and len(row) > i else ""
    return get


# A location is suspect when it is not a place: a requisition id, a job
# title, an all caps site code, a bare number.
BAD_LOCATION = [
    (re.compile(r"^[A-Z]{1,3}\d{3,}$"), "requisition id"),
    (re.compile(r"^R?\d{4,}$"), "bare number"),
    (re.compile(r"\b(engineer|intern|analyst|developer|scientist)\b", re.I),
     "job title in location"),
    (re.compile(r"^[A-Z0-9]{2,}\s*,?\s*(VIRTUAL|REMOTE)", re.I), "site code"),
    (re.compile(r"https?://"), "url"),
    (re.compile(r"^[A-Za-z]{3,}[A-Z]{2}$"), "missing separator"),
    (re.compile(r",\s*$"), "trailing comma"),
    (re.compile(r"^\s*,"), "leading comma"),
    (re.compile(r"[A-Za-z]{3,}\d"), "run together"),
]


def location_problem(loc):
    v = (loc or "").strip()
    if not v or v.lower() in ("unknown", "n/a", "remote"):
        return None
    for rx, why in BAD_LOCATION:
        if rx.search(v):
            return why
    if len(v) > 44:
        return "too long"
    return None


def main():
    v = Sheets().ws.spreadsheet.worksheet("Valid Entries").get_all_values()
    g = getter(v[0])
    rows = [(n, r) for n, r in enumerate(v[1:], start=2)
            if g(r, "Status").strip().lower() == "not applied"]

    print("=" * 78)
    print("NOT APPLIED ROWS: %d" % len(rows))
    print("=" * 78)

    # ---- 1. would the filter still take them ----
    rejects = []
    for n, r in rows:
        t = g(r, "Title")
        if t and not TP.is_cs_engineering_role(t, ""):
            rejects.append((n, g(r, "Company"), t, rejection_reason(t) or "scoring"))
    print("\n1. WOULD FAIL THE CURRENT FILTER: %d" % len(rejects))
    for n, co, t, why in rejects[:30]:
        print("   row %-5d %-22s %-40s %s" % (n, co[:22], t[:40], why[:26]))
    if len(rejects) > 30:
        print("   ... and %d more" % (len(rejects) - 30))

    # ---- 2. age ----
    print("\n2. AGE OF NOT APPLIED ROWS")
    by_date = collections.Counter(g(r, "Entry Date") for _n, r in rows)
    for d, c in sorted(by_date.items(), key=lambda x: x[0]):
        print("   %-14s %4d" % (d or "(blank)", c))

    # ---- 3. locations ----
    bad = []
    for n, r in rows:
        why = location_problem(g(r, "Location"))
        if why:
            bad.append((n, g(r, "Company"), g(r, "Location"), why, g(r, "Source")))
    print("\n3. SUSPECT LOCATIONS: %d of %d" % (len(bad), len(rows)))
    for n, co, loc, why, src in bad[:40]:
        print("   row %-5d %-20s %-30s %-20s %s"
              % (n, co[:20], loc[:30], why, src[:16]))
    if len(bad) > 40:
        print("   ... and %d more" % (len(bad) - 40))
    print("\n   by reason: %s"
          % dict(collections.Counter(b[3] for b in bad).most_common()))
    print("   by source: %s"
          % dict(collections.Counter(b[4] for b in bad).most_common(6)))


if __name__ == "__main__":
    main()
