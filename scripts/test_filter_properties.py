#!/usr/bin/env python3
"""
Property-based testing over the title filter.

The filter now has eight gates - seniority, term, business, two hardware
lists, citizenship, keyword scoring and guaranteed phrases - and they
interact. A title can satisfy a gate and a rescue at once, and which wins
depends on ordering that was never written down.

Every real bug found in this filter came from that interaction, not from a
single rule:

  "Software Engineer III"            a guaranteed phrase returned True
                                     before the seniority gate ran
  "AI/ML Computational Toxicology"   a discipline word beat the AI rescue
  "Software Test & Validation Eng"   "validation engineer" beat "software"
  "Bulk and Railcar Unloader"        "ai" matched inside "railcar"

Hand-written cases only cover what someone thought of. This generates titles
combinatorially and asserts invariants that must hold however the pieces
combine.

    python3 scripts/test_filter_properties.py
    python3 scripts/test_filter_properties.py --count 5000
    python3 scripts/test_filter_properties.py --show-failures
"""
import argparse
import itertools
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

CORE_SWE = [
    "Software Engineer", "Software Developer", "SDE", "SWE",
    "Backend Engineer", "Frontend Engineer", "Full Stack Engineer",
    "Data Engineer", "Machine Learning Engineer", "ML Engineer",
    "Site Reliability Engineer", "DevOps Engineer", "Platform Engineer",
    "Data Scientist", "AI Engineer", "Firmware Engineer",
    "Embedded Software Engineer", "Compiler Engineer", "Data Analyst",
    "Software Development Engineer",
]

LEVEL_OK = ["", " I", " II", " 1", " 2"]
LEVEL_BAD = [" III", " IV", " V", " VI", " 3", " 4", " 5", " 6",
             "-4", ", 5", " L5", " IC4", " E4"]

TERM_OK = ["", " - Spring 2027", " (January 2027)", " Winter 2027",
           " Intern", " Co-op", " Internship", " - Spring 2026"]
TERM_BAD = [" - Summer 2027", " Summer Intern", " (Fall 2027)",
            " 2027 Summer", " Summer 2028", " Fall 2027 Co-op"]

HARDWARE = ["Thermal", "Electrical Design", "Silicon Validation", "RFIC",
            "Process Integration", "Nuclear", "Photonic", "Quality",
            "Controls", "Paint", "Civil", "Mechanical Design",
            "Analog Design", "Post-Fab", "Packaging"]

BUSINESS = ["Solutions", "Customer", "Support", "Sales Development",
            "Technical Recruiter", "Investment Banking", "Presales",
            "Account Executive", "Talent Acquisition"]

CITIZENSHIP = [" (US Citizenship Required)", " - Active Security Clearance",
               " TS/SCI", " - U.S. Citizenship Required"]

SUFFIX = ["", " - Remote", " (Hybrid)", ", Boston MA", " - Team Alpha",
          " | Acme Corp", " - 4 Months", " - 2nd Shift", " (US)",
          " - New Grad", " 2027"]


def norm(s):
    return " ".join(s.split())


def build(rnd, core, levels, terms, suffixes, prefix=""):
    t = prefix + rnd.choice(core) + rnd.choice(levels) + rnd.choice(terms) \
        + rnd.choice(suffixes)
    return norm(t)


def _fill(cases, seen, per, label, expect, make, reject=None):
    """Collect up to `per` unique titles.

    Bounded by attempts, not by reaching the target: the combination space is
    finite, so once it is exhausted an unbounded loop spins forever. The
    first version of this hung on --count 3000.
    """
    attempts = 0
    limit = per * 40 + 500
    got = 0
    while got < per and attempts < limit:
        attempts += 1
        t = make()
        if t in seen:
            continue
        if reject and reject(t):
            continue
        seen.add(t)
        cases.append((t, expect, label))
        got += 1
    return got


def generate(n, seed=0):
    """Returns [(title, expected_keep, why)]. Deduplicated and bounded."""
    rnd = random.Random(seed)
    cases, seen = [], set()
    per = max(1, n // 6)

    hw_roles = ["Engineer", "Engineer Intern", "Engineering Co-op",
                "Engineer II", "Technician", "Specialist"]
    biz_roles = ["Engineer", "Engineer Intern", "Specialist", "Analyst",
                 "Manager"]

    _fill(cases, seen, per, "core software role", True,
          lambda: build(rnd, CORE_SWE, LEVEL_OK, TERM_OK, SUFFIX))
    _fill(cases, seen, per, "seniority", False,
          lambda: build(rnd, CORE_SWE, LEVEL_BAD, [""], SUFFIX))
    _fill(cases, seen, per, "term", False,
          lambda: build(rnd, CORE_SWE, [""], TERM_BAD, SUFFIX))
    _fill(cases, seen, per, "citizenship", False,
          lambda: build(rnd, CORE_SWE, LEVEL_OK, CITIZENSHIP, SUFFIX))
    _fill(cases, seen, per, "hardware", False,
          lambda: norm("%s %s%s" % (rnd.choice(HARDWARE), rnd.choice(hw_roles),
                                    rnd.choice(SUFFIX))),
          reject=lambda t: "software" in t.lower())
    _fill(cases, seen, per, "business", False,
          lambda: norm("%s %s%s" % (rnd.choice(BUSINESS), rnd.choice(biz_roles),
                                    rnd.choice(SUFFIX))),
          reject=lambda t: any(w in t.lower() for w in
                               ("software", "data engineer", "backend")))
    return cases


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=6000,
                    help="titles to generate (default 6000)")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--show-failures", action="store_true",
                    help="print every failing title, not just a sample")
    a = ap.parse_args()

    try:
        from aggregator.processors import TitleProcessor as TP
    except Exception as e:
        print("cannot import the filter: %s" % e)
        return 1

    cases = generate(a.count, a.seed)
    print("=" * 76)
    print("PROPERTY TEST: %d generated titles across 6 categories" % len(cases))
    print("=" * 76)

    by_cat = {}
    failures = []
    for title, expect, why in cases:
        try:
            got = TP.is_cs_engineering_role(title, "")
        except Exception as e:
            failures.append((title, expect, "EXCEPTION: %s" % str(e)[:50], why))
            by_cat.setdefault(why, [0, 0])[1] += 1
            continue
        slot = by_cat.setdefault(why, [0, 0])
        if got == expect:
            slot[0] += 1
        else:
            slot[1] += 1
            failures.append((title, expect, got, why))

    print()
    print("  %-22s %8s %8s %8s" % ("CATEGORY", "PASS", "FAIL", "RATE"))
    print("  " + "-" * 50)
    for cat in sorted(by_cat):
        ok, bad = by_cat[cat]
        rate = 100.0 * ok / (ok + bad) if (ok + bad) else 0
        print("  %-22s %8d %8d %7.1f%%" % (cat, ok, bad, rate))

    if failures:
        print()
        print("  FAILURES (%d):" % len(failures))
        shown = failures if a.show_failures else failures[:20]
        for title, expect, got, why in shown:
            print("    [%s] expected %-5s got %-5s  %s"
                  % (why, "keep" if expect else "drop",
                     "keep" if got is True else "drop" if got is False else got,
                     title[:56]))
        if not a.show_failures and len(failures) > 20:
            print("    ... and %d more (--show-failures for all)"
                  % (len(failures) - 20))

    total_ok = sum(v[0] for v in by_cat.values())
    print()
    print("=" * 76)
    print("%d/%d pass (%.2f%%)" % (total_ok, len(cases),
                                   100.0 * total_ok / len(cases) if cases else 0))
    print("=" * 76)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
