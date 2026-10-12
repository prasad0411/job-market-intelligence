#!/usr/bin/env python3
"""
Reconcile the feed's title against the job page's own title.

WHY
Feeds rewrite titles, and every rewrite defeats a filter that would otherwise
have fired:

  feed "Data Scientist/Engineer New Grad"      page "2027 PhD Graduate - AI/ML..."
  feed "Product Engineering Internship, Elect" page "...Electronics Hardware Design"
  feed "Geographic Information Systems Intern" page "Winter 2027 Intern, AI/ML"

The PhD filter, the hardware filter and the dedup logic all ran against text
that had already had the disqualifying words removed. Rejecting truncated
titles helps only when there is a visible ellipsis; here there was none.

PROVENANCE: the job page is authoritative, the feed is a pointer to it. So
once a page has been fetched, prefer its title and re-run the title filters
against it. A feed can no longer hide a PhD requirement by paraphrasing.

Returns the title to use plus whether it disagreed, so the caller can log
which feeds are unreliable and re-validate when it matters.
"""
import re

# Page <title> tags carry site furniture: strip it back to the role.
_SUFFIX_SEPARATORS = (" | ", " – ", " — ", " :: ", " • ")
_LOCATION_TAIL = re.compile(
    r"\s+in\s+[A-Z][A-Za-z\.\- ]+,\s*[A-Za-z\. ]+$")          # " in Laurel, Maryland"
_JOB_AT = re.compile(r"\s+(?:at|@)\s+[A-Z][\w&\.\- ]+$")       # " at Acme Corp"
_GENERIC = {
    "careers", "career", "jobs", "job", "job search", "search jobs",
    "careers home", "job listings", "all job listings", "apply",
    "application", "job details", "job description", "opportunities",
    "current openings", "open positions", "welcome", "sign in", "login",
    "page not found", "404", "error",
}


def clean_page_title(raw, company=""):
    """Reduce a page <title> to just the role, or '' if it is site furniture."""
    if not raw:
        return ""
    t = " ".join(str(raw).split())

    # Drop everything after the first separator: "Role | Company | Careers"
    for sep in _SUFFIX_SEPARATORS:
        if sep in t:
            t = t.split(sep)[0].strip()
            break

    t = _LOCATION_TAIL.sub("", t).strip()
    t = _JOB_AT.sub("", t).strip()

    # A trailing company name with no separator: "Software Engineer Twitch"
    if company:
        c = company.strip()
        if c and t.lower().endswith(c.lower()) and len(t) > len(c) + 3:
            t = t[: -len(c)].strip(" -–—|,")

    if t.strip().lower() in _GENERIC or len(t) < 5:
        return ""
    return t


def _words(t):
    STOP = {"a", "an", "the", "of", "for", "and", "or", "in", "at", "to",
            "with", "on", "our", "new"}
    return {w for w in re.split(r"[^a-z0-9]+", str(t).lower())
            if w and w not in STOP and len(w) > 1}


def reconcile(feed_title, page_title, company=""):
    """Return (title_to_use, disagreed, reason).

    Prefers the page title whenever it is usable and says something the feed
    title does not. Never replaces a good feed title with site furniture.
    """
    page = clean_page_title(page_title, company)
    feed = " ".join(str(feed_title or "").split())

    if not page:
        return feed, False, "no usable page title"
    if not feed:
        return page, True, "feed had no title"

    fw, pw = _words(feed), _words(page)
    if fw == pw:
        return feed, False, "identical"

    # Words the page has that the feed dropped. These are the ones that
    # matter: phd, hardware, summer, bachelors, senior.
    dropped = pw - fw
    if not dropped:
        # Feed has extra words but page has none of its own - feed is a
        # superset, e.g. it appended a location. Keep the feed's.
        return feed, False, "feed is a superset"

    SIGNIFICANT = {
        "phd", "ph", "doctoral", "postdoc",
        "bachelors", "bachelor", "undergraduate", "masters", "master",
        "hardware", "electrical", "mechanical", "firmware", "embedded",
        "summer", "fall", "spring", "winter",
        "senior", "sr", "staff", "principal", "lead", "manager", "director",
        "intern", "internship", "coop", "graduate",
        "clearance", "citizen",
    }
    sig = dropped & SIGNIFICANT
    if sig:
        return page, True, "feed dropped significant words: " + ",".join(sorted(sig))

    # Titles share almost nothing - the feed row may point at a different job
    overlap = len(fw & pw) / max(len(fw | pw), 1)
    if overlap < 0.3:
        return page, True, "titles barely overlap ({:.0%}) - possible wrong row".format(overlap)

    return page, True, "page title is more complete"


if __name__ == "__main__":
    CASES = [
        # (feed, page, company, expect_use_page, expect_disagreed)
        ("Data Scientist/Engineer New Grad - Analytic Capabilities",
         "2027 PhD Graduate - AI/ML Data Scientist/Engineer - Analytic Capabilities "
         "in Laurel, Maryland | Johns Hopkins Applied Physics Laboratory",
         "Johns Hopkins Applied Physics Laboratory", True, True),
        ("Product Engineering Internship, Elect...",
         "Product Engineering Internship, Electronics Hardware Design (Fall 2026)",
         "Cadence", True, True),
        ("Geographic Information Systems Intern",
         "Winter 2027 Intern, Artificial Intelligence/Machine Learning",
         "Kodiak", True, True),
        ("Software Engineer I", "Software Engineer I | Twitch", "Twitch", False, False),
        ("Software Engineer, Backend",
         "Software Engineer, Backend in San Francisco, California | Stripe",
         "Stripe", False, False),
        # site furniture must never win
        ("Software Engineer Intern", "Careers Home | Acme", "Acme", False, False),
        ("Data Engineer", "Job Search", "X", False, False),
        ("Machine Learning Engineer", "", "X", False, False),
        # feed appended a location; page is the plain role
        ("Software Engineer - Boston, MA", "Software Engineer", "X", False, False),
    ]
    bad = 0
    print("  {:<44}{:<44}{:<8}{}".format("FEED", "PAGE (cleaned)", "USED", "REASON"))
    print("  " + "-" * 118)
    for feed, page, co, want_page, want_dis in CASES:
        used, dis, why = reconcile(feed, page, co)
        got_page = used != feed
        ok = "PASS" if (got_page == want_page and dis == want_dis) else "*** FAIL ***"
        if ok != "PASS":
            bad += 1
        print("  {:<44}{:<44}{:<8}{}".format(
            feed[:43], clean_page_title(page, co)[:43], "PAGE" if got_page else "feed", why[:40]))
        if ok != "PASS":
            print("       want use_page={} disagreed={}, got {} {}".format(
                want_page, want_dis, got_page, dis))
    print("\n  {}/{} correct".format(len(CASES) - bad, len(CASES)))
