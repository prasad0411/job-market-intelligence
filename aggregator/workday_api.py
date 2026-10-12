#!/usr/bin/env python3
"""
Fetch a Workday job description via the CXS JSON API.

Workday career pages are rendered entirely in JavaScript. Fetching the HTML
returns a shell: page chars = 0. Every page based check - undergraduate only,
sponsorship, clearance, PhD, page age - therefore ran against an empty
document and passed by default. With 227 Workday tenants in discovery, that
is a large blind spot, and it is why a P&G internship that says "In process
of obtaining a Bachelors degree" and "Immigration Sponsorship is not
available for this role" reached the sheet.

Workday exposes the same posting as JSON:

    page: https://pg.wd5.myworkdayjobs.com/en-US/1000/job/SITE/Title_R123
    api:  https://pg.wd5.myworkdayjobs.com/wday/cxs/pg/1000/job/SITE/Title_R123

The transform is: drop the locale segment, insert /wday/cxs/{tenant}/ before
the site id. Verified against P&G: 8,537 chars containing every requirement
the HTML lacked.
"""
import json
import re
import ssl
import urllib.request

_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/122.0 Safari/537.36")
_CTX = ssl.create_default_context()
_CTX.check_hostname = False
_CTX.verify_mode = ssl.CERT_NONE


def to_api_url(url):
    """Convert a Workday job page URL to its CXS API URL, or None."""
    if not url or "myworkdayjobs.com" not in str(url).lower():
        return None
    m = re.match(
        r"(https?://([a-z0-9\-]+)\.wd\d+\.myworkdayjobs\.com)"      # host
        r"(?:/[a-z]{2}-[A-Z]{2})?"                                   # /en-US
        r"/([^/]+)"                                                  # site id
        r"(/job/.+?)(?:[?#].*)?$",                                   # /job/...
        str(url), re.I)
    if not m:
        return None
    host, tenant, site, path = m.groups()
    return "{}/wday/cxs/{}/{}{}".format(host, tenant, site, path)


def fetch_description(url, timeout=20):
    """Return the job description text, or '' when unavailable."""
    api = to_api_url(url)
    if not api:
        return ""
    try:
        req = urllib.request.Request(
            api, headers={"User-Agent": _UA, "Accept": "application/json"})
        raw = urllib.request.urlopen(req, timeout=timeout, context=_CTX).read()
        data = json.loads(raw.decode("utf-8", "replace"))
    except Exception:
        return ""

    info = data.get("jobPostingInfo") or {}
    parts = [
        info.get("jobDescription") or "",
        info.get("title") or "",
        info.get("jobRequisitionLocation", {}).get("descriptor", "")
        if isinstance(info.get("jobRequisitionLocation"), dict) else "",
    ]
    return "\n".join(p for p in parts if p)


def fetch_soup(url, timeout=20):
    """Description as a BeautifulSoup document, so the existing page based
    checks can consume it unchanged."""
    html = fetch_description(url, timeout=timeout)
    if not html:
        return None
    try:
        from bs4 import BeautifulSoup
        return BeautifulSoup(html, "html.parser")
    except Exception:
        return None


if __name__ == "__main__":
    CASES = [
        ("https://pg.wd5.myworkdayjobs.com/en-US/1000/job/CINCINNATI-GENERAL-OFFICES/"
         "IT-Engineering-Internship--Software--Platform----Network-_R000157503",
         "https://pg.wd5.myworkdayjobs.com/wday/cxs/pg/1000/job/"
         "CINCINNATI-GENERAL-OFFICES/IT-Engineering-Internship--Software--Platform----Network-_R000157503"),
        ("https://disney.wd5.myworkdayjobs.com/disneycareer/job/Lake-Buena-Vista-FL-USA/"
         "Env-Intern_10156341",
         "https://disney.wd5.myworkdayjobs.com/wday/cxs/disney/disneycareer/job/"
         "Lake-Buena-Vista-FL-USA/Env-Intern_10156341"),
        ("https://cadence.wd1.myworkdayjobs.com/External_Careers/job/Burlington-MA/"
         "Product-Eng_R54158-2?utm_source=x",
         "https://cadence.wd1.myworkdayjobs.com/wday/cxs/cadence/External_Careers/job/"
         "Burlington-MA/Product-Eng_R54158-2"),
        ("https://boards.greenhouse.io/x/jobs/1", None),
        ("https://www.google.com/search?q=x", None),
        ("", None),
    ]
    bad = 0
    print("  URL -> API transform")
    print("  " + "-" * 72)
    for u, want in CASES:
        got = to_api_url(u)
        ok = "PASS" if got == want else "*** FAIL ***"
        if got != want:
            bad += 1
            print("  {} \n      got  {}\n      want {}".format(ok, got, want))
        else:
            print("  {}  {}".format(ok, (got or "None")[:66]))
    print("\n  {}/{} transforms correct".format(len(CASES) - bad, len(CASES)))

    print("\n  live fetch (P&G):")
    d = fetch_description(CASES[0][0])
    print("    chars:", len(d))
    low = d.lower()
    for k in ("bachelor", "sponsorship is not available", "summer of 2027"):
        print("    contains {!r}: {}".format(k, k in low))
