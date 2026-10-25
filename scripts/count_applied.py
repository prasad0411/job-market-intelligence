#!/usr/bin/env python3
"""
Count applications by job type across every sheet that holds application
history.

Applied rows are never deleted, but they can sit in Valid Entries, Reviewed,
or Internship Applications depending on when they were filed, so counting one
tab undercounts. Deduplicated on company plus title.
"""
import collections
import contextlib
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from outreach.outreach_data import Sheets                       # noqa: E402

TABS = ["Valid Entries", "Reviewed - Not Applied",
        "Internship Applications", "Senior Software Engineer"]

INTERNISH = re.compile(r"\b(intern|internship|co-?op|summer analyst)\b", re.I)


def key(co, t):
    return re.sub(r"[^a-z0-9]", "", ("%s_%s" % (co, t)).lower())


def main():
    ss = Sheets().ws.spreadsheet
    seen, rows = set(), []

    for tab in TABS:
        data = None
        with contextlib.suppress(Exception):
            # A tab may not exist in every copy of the workbook.
            data = ss.worksheet(tab).get_all_values()
        if data is None:
            continue
        if not data:
            continue
        idx = {n: i for i, n in enumerate(data[0]) if n}
        if "Status" not in idx:
            continue

        def cell(r, n):
            i = idx.get(n)
            return r[i].strip() if i is not None and len(r) > i else ""

        for r in data[1:]:
            if cell(r, "Status").lower() != "applied":
                continue
            co, t = cell(r, "Company"), cell(r, "Title")
            k = key(co, t)
            if k in seen:
                continue
            seen.add(k)
            rows.append((tab, co, t, cell(r, "Job Type"),
                         cell(r, "Date Applied"), cell(r, "Entry Date")))

    print("=" * 70)
    print("APPLIED, DEDUPLICATED: %d" % len(rows))
    print("=" * 70)

    # Job Type is the stated field; the title is the fallback when it is blank
    intern = [r for r in rows
              if "intern" in r[3].lower() or "co-op" in r[3].lower()
              or INTERNISH.search(r[2] or "")]
    full = [r for r in rows if r not in intern]

    print("\n  internships and co ops : %d" % len(intern))
    print("  full time and other    : %d" % len(full))

    print("\n  by job type field:")
    for jt, n in collections.Counter(r[3] or "(blank)" for r in rows).most_common():
        print("    %-26s %4d" % (jt[:26], n))

    print("\n  by sheet:")
    for tab, n in collections.Counter(r[0] for r in rows).most_common():
        print("    %-26s %4d" % (tab[:26], n))

    months = collections.Counter()
    for r in rows:
        d = r[4] or r[5]
        m = re.search(r"([A-Za-z]{3})-(\d{4})", d or "")
        months[m.group(0) if m else "(no date)"] += 1
    print("\n  by month:")
    for m, n in sorted(months.items()):
        print("    %-26s %4d" % (m, n))


if __name__ == "__main__":
    main()
