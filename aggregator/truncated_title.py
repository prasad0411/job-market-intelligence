#!/usr/bin/env python3
"""
Detect titles that have been truncated by the source feed.

zapplyjobs truncates titles at roughly 38 characters and appends an ellipsis:
    "Product Engineering Internship, Elect..."
    "Grants Specialist II (Remote) - Spons..."

A truncated title defeats every title based filter, because the words that
would have caused a rejection are the ones that got cut off. The Cadence row
is the clean example: the real title is "Product Engineering Internship,
Electronics Hardware Design (Fall 2026)" and the hardware filter never saw
"Electronics Hardware Design".

So a truncated title is not a title we can judge. It must either be resolved
from the job page, or rejected. It must never be silently accepted.
"""
import re

# The ellipsis forms feeds actually emit.
_ELLIPSIS = ("...", "\u2026", ". . .")


def is_truncated(title):
    """True when the title has visibly been cut short by the source."""
    if not title:
        return False
    t = str(title).rstrip()
    if not t:
        return False

    # 1. explicit ellipsis at the end
    for e in _ELLIPSIS:
        if t.endswith(e):
            return True

    # 2. ellipsis anywhere (some feeds put it mid string before a suffix)
    if "\u2026" in t or "..." in t:
        return True

    # An earlier version also tried to detect titles cut mid word by looking
    # at the last token. It flagged "Embedded Software Engineer Intern" and
    # "Software Engineering Intern - Payments" as truncated. Real feeds always
    # emit an explicit ellipsis, so that heuristic bought nothing and cost
    # accuracy. Dropped deliberately.
    return False


def reason(title):
    return "Truncated title from source: {!r}".format(str(title)[:60])


if __name__ == "__main__":
    CASES = [
        # (title, expect_truncated)
        ("Product Engineering Internship, Elect...", True),
        ("Grants Specialist II (Remote) - Spons...", True),
        ("Research Engineer - The Diffusion LLM...", True),
        ("Cybersecurity Operations [Multiple Po...", True),
        ("Artificial Intelligence/Machine Learn\u2026", True),
        # must NOT be flagged
        ("Software Engineer I", False),
        ("Software Engineer, Backend", False),
        ("New Grad: Software Engineer", False),
        ("Embedded Software Engineer Intern", False),
        ("Data Scientist, Core Data - PhD", False),
        ("Software Engineering Intern - Payments", False),
        ("Machine Learning Engineer Co-op", False),
        ("Software Engineer II, Autonomy Behavior", False),
        ("Site Reliability Engineer", False),
        ("Backend Engineer, Payments and Risk", False),
        ("Software Development Engineer Intern", False),
        ("Product Engineering Internship, Electronics Hardware Design", False),
    ]
    bad = 0
    print("  {:<46}{:<10}{:<8}".format("TITLE", "FLAGGED", "WANT"))
    print("  " + "-" * 70)
    for t, want in CASES:
        got = is_truncated(t)
        ok = "PASS" if got == want else "*** FAIL ***"
        if got != want:
            bad += 1
        print("  {:<46}{:<10}{:<8}{}".format(t[:45], str(got), str(want), ok))
    print("\n  {}/{} correct".format(len(CASES) - bad, len(CASES)))
