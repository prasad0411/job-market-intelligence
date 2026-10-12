#!/usr/bin/env python3
"""
Recover a job ID from an ATS URL.

154 of 167 Ashby rows carry job_id "N/A" while the UUID sits in the URL:
    https://jobs.ashbyhq.com/snowflake/b33f19c8-e57e-4d9f-87d9-ec2e3e3e3a22

Job ID is the strongest dedup signal available - it is the only one that
still matches when a title is reworded or a URL gains tracking parameters.
Leaving it blank pushes those rows down to company+title matching, which is
where the false merges happen.

Handles every board the pipeline actually uses. Returns None rather than
guessing, so a bad ID never poisons the registry.
"""
import re
from urllib.parse import urlparse

_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)


def extract_job_id(url):
    """Return the posting's ID from its URL, or None."""
    if not url or not str(url).startswith("http"):
        return None
    u = str(url)
    low = u.lower()

    # Ashby: /{company}/{uuid}[/application]
    if "ashbyhq.com" in low:
        m = _UUID.search(u)
        return m.group(0).lower() if m else None

    # Greenhouse: /{company}/jobs/{digits}
    if "greenhouse.io" in low:
        m = re.search(r"/jobs/(\d{4,})", u)
        if m:
            return m.group(1)
        m = re.search(r"gh_jid=(\d{4,})", u)
        return m.group(1) if m else None

    # Lever: /{company}/{uuid}
    if "lever.co" in low:
        m = _UUID.search(u)
        return m.group(0).lower() if m else None

    # Workday: the trailing _R12345, _JR12345, _10156341, optionally with a
    # "-2" revision suffix. The letter prefix is optional: Disney uses bare
    # digits (_10156341) while Cadence uses _R54158-2.
    if "myworkdayjobs.com" in low:
        m = re.search(r"_([A-Za-z]{0,3}\d{4,})(?:-\d+)?(?:[/?#]|$)", u)
        if m:
            return m.group(1)
        m = re.search(r"/(\d{6,})(?:[/?#]|$)", u)
        return m.group(1) if m else None

    # SmartRecruiters: /{company}/{digits}-{slug}
    if "smartrecruiters.com" in low:
        m = re.search(r"/(\d{9,})", u)
        return m.group(1) if m else None

    # Workable: /j/{HEXCODE}
    if "workable.com" in low:
        m = re.search(r"/j/([A-Z0-9]{8,})", u, re.I)
        return m.group(1).upper() if m else None

    # Rippling: /{uuid}
    if "rippling.com" in low or "ripplingats.com" in low:
        m = _UUID.search(u)
        return m.group(0).lower() if m else None

    # Oracle Cloud: /job/{digits}
    if "oraclecloud.com" in low:
        m = re.search(r"/job/(\d{3,})", u)
        return m.group(1) if m else None

    # iCIMS: /jobs/{digits}/
    if "icims.com" in low:
        m = re.search(r"/jobs/(\d{3,})", u)
        return m.group(1) if m else None

    return None


if __name__ == "__main__":
    CASES = [
        ("https://jobs.ashbyhq.com/snowflake/b33f19c8-e57e-4d9f-87d9-ec2e3e3e3a22/application",
         "b33f19c8-e57e-4d9f-87d9-ec2e3e3e3a22"),
        ("https://jobs.ashbyhq.com/sentra/eb51547f-e234-42f7-ab75-ab93717579d9?utm_source=x",
         "eb51547f-e234-42f7-ab75-ab93717579d9"),
        ("https://jobs.ashbyhq.com/GigaML/aa903645-854f-4404-9d49-8a96f0dcc2cc/application",
         "aa903645-854f-4404-9d49-8a96f0dcc2cc"),
        ("https://job-boards.greenhouse.io/twitch/jobs/8459320002", "8459320002"),
        ("https://boards.greenhouse.io/figma/jobs/5691886004?gh_jid=5691886004", "5691886004"),
        ("https://job-boards.greenhouse.io/kodiak/jobs/4377407009?utm_source=Simplify",
         "4377407009"),
        ("https://jobs.lever.co/zoox/51838a63-2dde-44dc-9c3e-757f35b9690f",
         "51838a63-2dde-44dc-9c3e-757f35b9690f"),
        ("https://cadence.wd1.myworkdayjobs.com/External_Careers/job/Burlington-MA/"
         "Product-Engineering-Internship_R54158-2", "R54158"),
        ("https://disney.wd5.myworkdayjobs.com/disneycareer/job/Lake-Buena-Vista/"
         "Env-Intern_10156341", "10156341"),
        ("https://jobs.smartrecruiters.com/RedBull/744000139169859", "744000139169859"),
        ("https://emit.fa.ca3.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_2001/job/93925",
         "93925"),
        ("https://www.google.com/search?q=x", None),
        ("", None),
        ("not a url", None),
    ]
    bad = 0
    for u, want in CASES:
        got = extract_job_id(u)
        ok = "PASS" if got == want else "*** FAIL ***"
        if got != want:
            bad += 1
        print("  {:<10} {:<38} {}".format(ok, str(got)[:37], u[:56]))
    print("\n  {}/{} correct".format(len(CASES) - bad, len(CASES)))
