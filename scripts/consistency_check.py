#!/usr/bin/env python3
"""
Compare what the sheet says against what the gates say right now.

Every bug found on 2 and 3 November was the same shape: a default or a
branch that was correct when the pipeline targeted internships, and silently
wrong after the full time switch. None of them failed loudly.

    - LinkedIn rows defaulted to Job Type "Internship" because the override
      branch became unreachable
    - "Intern - System Engineer" was stripped to "System Engineer" before
      any gate saw it
    - the discarded auditor rescued rows the filter had just rejected
    - the season gate was short circuited and never replaced

Each one was visible as a disagreement between a stored value and a live
gate verdict. This check looks for those disagreements, so the next stale
default is caught the morning after rather than weeks later.

It only reads. Nothing is written or moved.

    python3 scripts/consistency_check.py
    python3 scripts/consistency_check.py --tab "Reviewed - Not Applied"
"""
import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

OK, BAD = "PASS", "FAIL"
_URL_SLUG = re.compile(r"/job/[^/]+/([A-Za-z0-9\-]+)_")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tab", default="Valid Entries")
    ap.add_argument("--show", type=int, default=6, help="examples per finding")
    a = ap.parse_args()

    from outreach.outreach_data import Sheets
    from aggregator.title_gates import rejection_reason, FULL_TIME_ONLY
    from aggregator.processors import TitleProcessor as TP
    from aggregator.direct_sources import _is_junk_title, job_id_from_url

    ws = Sheets().ws.spreadsheet.worksheet(a.tab)
    data = ws.get_all_values()
    if not data:
        sys.exit("%s is empty" % a.tab)
    header = data[0]
    idx = {n: i for i, n in enumerate(header) if n}

    def cell(r, n):
        i = idx.get(n)
        return r[i].strip() if i is not None and len(r) > i else ""

    rows = [r for r in data[1:] if cell(r, "Status").lower() == "not applied"]

    findings = []

    def check(name, hits, note=""):
        findings.append((name, hits, note))

    # 1. Job Type disagrees with the gate. This is the LinkedIn bug: a stored
    #    label that no live gate agrees with means the writer is stale.
    hits = []
    for r in rows:
        jt = cell(r, "Job Type").lower()
        t = cell(r, "Title")
        gate_says_intern = bool(rejection_reason(t))
        if jt in ("internship", "co-op", "coop", "intership") and not gate_says_intern:
            hits.append((cell(r, "Company"), t, "labelled %s, gate says full time" % jt))
        elif jt == "full time" and gate_says_intern:
            hits.append((cell(r, "Company"), t, "labelled Full Time, gate rejects it"))
    check("Job Type matches the gate", hits,
          "a label no gate agrees with means a stale default")

    # 2. A row that the current gates would reject. Anything here slipped in
    #    before a gate existed, or through a path that skips the gates.
    hits = [(cell(r, "Company"), cell(r, "Title"), rejection_reason(cell(r, "Title")))
            for r in rows if rejection_reason(cell(r, "Title"))]
    check("No row fails a live gate", hits, "these would be rejected today")

    # 3. The stored title disagrees with the URL slug. Cleaning runs before
    #    gating, so a cleaner can hide a word the gate needs: "Intern - X"
    #    became "X" and the term gate never saw an internship.
    hits = []
    for r in rows:
        m = _URL_SLUG.search(cell(r, "Job URL"))
        if not m:
            continue
        raw = m.group(1).replace("-", " ")
        if rejection_reason(raw) and not rejection_reason(cell(r, "Title")):
            hits.append((cell(r, "Company"), cell(r, "Title"),
                         "URL says %r" % raw[:40]))
    check("Title agrees with the URL", hits,
          "a cleaner stripped a word the gate needed")

    # 4. Junk titles from a board that returned a page rather than job data.
    hits = [(cell(r, "Company"), cell(r, "Title"), "junk")
            for r in rows if _is_junk_title(cell(r, "Title"), cell(r, "Company"))]
    check("No junk titles", hits)

    # 5. Sr. No. is a true 1..N index over the whole tab, not just the
    #    movable rows: holes mean an archive ran without a renumber.
    allrows = data[1:]
    sr = [cell(r, "Sr. No.") for r in allrows]
    want = [str(i) for i in range(1, len(allrows) + 1)]
    hits = [] if sr == want else [("", "", "%d rows, first mismatch at %d" % (
        len(allrows), next((i + 1 for i, (x, y) in enumerate(zip(sr, want)) if x != y), 0)))]
    check("Sr. No. is 1..N", hits)

    # 6. A Job ID that could be read from the URL but was stored as N/A.
    hits = [(cell(r, "Company"), cell(r, "Title"),
             "URL has %s" % job_id_from_url(cell(r, "Job URL")))
            for r in rows
            if cell(r, "Job ID").upper() in ("", "N/A")
            and job_id_from_url(cell(r, "Job URL"))]
    check("Job ID taken from the URL when present", hits)

    # 7. Sponsorship values outside the three the column is defined to hold.
    allowed = {"yes", "no", "unknown", ""}
    hits = [(cell(r, "Company"), cell(r, "Title"), cell(r, "Sponsorship"))
            for r in rows if cell(r, "Sponsorship").lower() not in allowed]
    check("Sponsorship is yes, no or unknown", hits)

    # 8. Exact duplicates among the movable rows.
    seen, dupes = {}, []
    for r in rows:
        k = (re.sub(r"[^a-z0-9]", "", cell(r, "Company").lower()),
             re.sub(r"[^a-z0-9]", "", cell(r, "Title").lower()))
        if k in seen:
            dupes.append((cell(r, "Company"), cell(r, "Title"), "duplicate"))
        seen[k] = True
    check("No duplicate company+title", dupes)

    print("=" * 78)
    print("CONSISTENCY CHECK: %s  (%d rows, %d not applied)"
          % (a.tab, len(data) - 1, len(rows)))
    print("mode: FULL_TIME_ONLY = %s" % FULL_TIME_ONLY)
    print("=" * 78)
    failed = 0
    for name, hits, note in findings:
        state = OK if not hits else BAD
        failed += bool(hits)
        print("  %-5s %-40s %s" % (state, name,
                                   "clean" if not hits else "%d row(s)" % len(hits)))
        if hits:
            if note:
                print("           %s" % note)
            for co, t, why in hits[:a.show]:
                print("           %-22s %-38s %s" % (co[:22], t[:38], str(why)[:40]))
            if len(hits) > a.show:
                print("           ... and %d more" % (len(hits) - a.show))
    print("=" * 78)
    if failed:
        print("%d CHECK(S) FAILED" % failed)
    else:
        print("ALL CHECKS OK")
    print("=" * 78)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
