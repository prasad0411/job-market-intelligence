#!/usr/bin/env python3
"""
Find a recruiter/HM email from PUBLIC, NON-LINKEDIN sources, then verify it.
Safe: no LinkedIn scraping (ban risk). Low-yield by design — most companies
do not publish recruiter emails. Every candidate is run through the existing
verifier; only definitively-verified emails are returned.
"""
import re, time, requests
from outreach.outreach_verifier import EmailVerifier

_UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}
_EMAIL_RE = r"[a-zA-Z0-9._%+-]+@%s"

def _search_public(name, domain):
    """Web-search public pages for name@domain-style emails. Returns candidates."""
    cands = set()
    queries = [f'"{name}" "@{domain}"', f'{name} email {domain}']
    for q in queries:
        try:
            r = requests.get("https://duckduckgo.com/html/",
                             params={"q": q}, headers=_UA, timeout=10)
            if r.status_code == 200:
                for m in re.findall(_EMAIL_RE % re.escape(domain), r.text):
                    if "noreply" not in m.lower() and "example" not in m.lower():
                        cands.add(m.lower())
        except Exception:
            pass
        time.sleep(1)
    return cands

def _scan_company_site(domain):
    """Fetch company site + common contact pages, scan for @domain emails."""
    cands = set()
    for path in ["", "/contact", "/about", "/team", "/careers"]:
        for scheme in ("https://", "https://www."):
            try:
                r = requests.get(f"{scheme}{domain}{path}", headers=_UA, timeout=8)
                if r.status_code == 200:
                    for m in re.findall(_EMAIL_RE % re.escape(domain), r.text):
                        if all(x not in m.lower() for x in ("noreply","example","support","info","sales","press")):
                            cands.add(m.lower())
                break
            except Exception:
                continue
        time.sleep(0.5)
    return cands

def find_public_email(name, company, domain, verifier=None):
    """
    Try public sources for a real email, verify each, return first VERIFIED one.
    Returns dict {email, confidence, source} or None.
    """
    if verifier is None:
        verifier = EmailVerifier()
    cands = _search_public(name, domain) | _scan_company_site(domain)
    if not cands:
        return None
    for email in cands:
        try:
            res = verifier.verify(email, domain, source_hint="public_web")
            src = str(res.get("source","")).lower()
            conf = res.get("confidence", 0)
            definitive = any(x in src for x in ("google","gxlu","microsoft","365"))
            if conf >= 85 and definitive:
                return {"email": email, "confidence": conf, "source": f"public+{src}"}
        except Exception:
            continue
    return None

if __name__ == "__main__":
    import sys
    name = sys.argv[1] if len(sys.argv) > 1 else "Satya Nadella"
    domain = sys.argv[2] if len(sys.argv) > 2 else "microsoft.com"
    print(f"Searching public sources for {name} @ {domain}...")
    r = find_public_email(name, name.split()[-1], domain)
    print("RESULT:", r if r else "No verified public email found")
