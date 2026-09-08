import os
#!/usr/bin/env python3
"""Outreach Pipeline — Email Finder (cache → Reacher → API cascade)."""

import re, time, logging, json, requests
from functools import lru_cache
from concurrent.futures import ThreadPoolExecutor, as_completed
OVERRIDES_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.local', 'domain_overrides.json')
RETRY_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.local', 'retry_tracker.json')

from outreach.outreach_config import (
    CLEARBIT_URL,
    TLDS,
    REACHER_URL,
    REACHER_FROM,
    REACHER_TIMEOUT,
    REACHER_WORKERS,
    APIS,
    API_TIMEOUT,
    API_RETRIES,
    HUNTER_CONF,
    key,
)
from outreach.outreach_data import NameParser, PatternCache, Credits
from outreach.brain import Brain
from outreach.outreach_provider import ProviderVerifier
from outreach.outreach_verifier import EmailVerifier, is_suspicious_email as verify_suspicious, CircuitBreaker, DomainHistory, AUTO_SEND_THRESHOLD
from aggregator.atomic_json import write_json as _atomic_write_json

log = logging.getLogger(__name__)
try:
    import dns.resolver
    _DNS = True
except ImportError:
    _DNS = False


_DOMAIN_CACHE_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    ".local", "domain_cache.json"
)
_DOMAIN_CACHE_TTL_DAYS = 30


def _load_domain_cache():
    try:
        if os.path.exists(_DOMAIN_CACHE_FILE):
            raw = json.load(open(_DOMAIN_CACHE_FILE))
            cutoff = __import__('time').time() - _DOMAIN_CACHE_TTL_DAYS * 86400
            return {k: v for k, v in raw.items() if v.get("ts", 0) > cutoff}
    except Exception:
        pass
    return {}


def _save_domain_cache(cache):
    try:
        _atomic_write_json(_DOMAIN_CACHE_FILE, cache)
    except Exception as e:
        log.debug(f"domain_cache save failed: {e}")


class Finder:
    def __init__(self, credits: Credits):
        self.cr = credits
        self.pc = PatternCache()
        self.pv = ProviderVerifier()
        self._reacher = None
        self._dom = _load_domain_cache()
        self.verifier = EmailVerifier(
            provider_verifier=self.pv,
            reacher_verify_fn=self._verify,
            reacher_ok_fn=self._rok,
        )
        Finder._cleanup_caches()

    @staticmethod
    def _extract_name_from_linkedin_url(linkedin_url):
        """
        Extract full name from LinkedIn URL.
        Priority:
          1. Brain cache (permanent, never re-fetched)
          2. Slug decomposition (works for hyphenated slugs)
          3. LinkedIn public page <title> tag (free, no auth)
          4. Google/Bing search fallback (uses Selenium)
        Caches all results in Brain permanently.
        """
        if not linkedin_url:
            return None
        import re as _re

        # Normalize URL
        m = _re.search(r"linkedin\.com/in/([a-zA-Z0-9_-]+)", linkedin_url)
        if not m:
            return None
        slug = m.group(1).lower().strip("/")

        # 1. Check Brain cache first
        try:
            from outreach.brain import Brain
            b = Brain.get()
            cached = b._data.get("linkedin_names", {}).get(slug)
            if cached and cached.get("name"):
                log.debug(f"LinkedIn name from Brain cache: {slug} → {cached['name']}")
                return cached["name"]
        except Exception:
            pass

        # 2. Slug decomposition
        clean_slug = slug
        # Remove trailing hex/numeric IDs: madeline-batista-72930372, 967b29105
        clean_slug = _re.sub(r"-[0-9a-f]{6,}$", "", clean_slug)
        clean_slug = _re.sub(r"-\d{4,}$", "", clean_slug)
        # Remove single-char trailing segments (initials appended to slug)
        clean_slug = _re.sub(r"-[a-z]$", "", clean_slug)

        parts = [p.capitalize() for p in clean_slug.split("-") if p and len(p) > 1]
        # Filter out hex/numeric segments
        clean_parts = [p for p in parts
                      if not _re.match(r"^[0-9a-f]+$", p.lower())
                      and not p.isdigit()
                      and len(p) > 1]

        slug_name = None
        if len(clean_parts) >= 2:
            slug_name = " ".join(clean_parts)

        # If slug has no hyphens, it's a concatenated name like "camaraqueder"
        # Try LinkedIn page title for ALL slugs (slug decomp might be wrong)
        # Only skip page fetch if slug clearly has 2+ hyphen-separated real words
        needs_page_fetch = not slug_name or "-" not in slug

        if needs_page_fetch:
            name = Finder._fetch_linkedin_name(linkedin_url, slug)
            if name:
                Finder._cache_linkedin_name(slug, name, "page_title")
                return name

        if slug_name:
            Finder._cache_linkedin_name(slug, slug_name, "slug")
            return slug_name

        return None

    @staticmethod
    def _fetch_linkedin_name(linkedin_url, slug):
        """Fetch LinkedIn public page to extract name from <title> tag."""
        import re as _re, time as _t
        try:
            import requests as _req
            _agents = [
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/121.0.0.0 Safari/537.36",
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/121.0.0.0 Safari/537.36",
            ]
            import random as _rand
            headers = {
                "User-Agent": _rand.choice(_agents),
                "Accept-Language": "en-US,en;q=0.9",
                "Accept": "text/html,application/xhtml+xml",
            }
            resp = _req.get(linkedin_url, headers=headers, timeout=8, allow_redirects=True)
            if resp.status_code == 200:
                # Title format: "First Last | LinkedIn" or "First Last - Title | LinkedIn"
                title_m = _re.search(r"<title[^>]*>([^<]+)</title>", resp.text, _re.I)
                if title_m:
                    title = title_m.group(1).strip()
                    # Strip everything after | or - (job title, company)
                    name = _re.split(r"\s*[|\-]\s*", title)[0].strip()
                    # Validate: should be 2+ words, no digits, reasonable length
                    words = name.split()
                    if (2 <= len(words) <= 4
                            and all(w[0].isupper() for w in words if w)
                            and not any(c.isdigit() for c in name)
                            and len(name) < 50):
                        log.info(f"LinkedIn page title: {slug} → {name}")
                        return name
                # Try og:title meta tag
                og_m = _re.search(r'property="og:title"[^>]*content="([^"]+)"', resp.text)
                if not og_m:
                    og_m = _re.search(r'content="([^"]+)"[^>]*property="og:title"', resp.text)
                if og_m:
                    title = og_m.group(1).strip()
                    name = _re.split(r"\s*[|\-]\s*", title)[0].strip()
                    words = name.split()
                    if 2 <= len(words) <= 4 and len(name) < 50:
                        log.info(f"LinkedIn og:title: {slug} → {name}")
                        return name
        except Exception as e:
            log.debug(f"LinkedIn page fetch failed for {slug}: {e}")

        # Google search fallback: "linkedin.com/in/<slug>"
        try:
            import requests as _req
            import re as _re
            search_url = f"https://www.google.com/search?q=linkedin.com%2Fin%2F{slug}"
            headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}
            resp = _req.get(search_url, headers=headers, timeout=8)
            if resp.status_code == 200:
                # Google result format: "Name - Title - LinkedIn"
                hits = _re.findall(r"<h3[^>]*>([^<]+)</h3>", resp.text)
                for hit in hits[:5]:
                    hit = _re.sub(r"<[^>]+>", "", hit).strip()
                    if "linkedin" in hit.lower():
                        name = _re.split(r"\s*[-|·]\s*", hit)[0].strip()
                        words = name.split()
                        if 2 <= len(words) <= 4 and not any(c.isdigit() for c in name):
                            log.info(f"Google LinkedIn search: {slug} → {name}")
                            return name
        except Exception as e:
            log.debug(f"Google LinkedIn search failed: {e}")

        return None

    @staticmethod
    def _cache_linkedin_name(slug, name, method):
        """Permanently cache LinkedIn slug → name in Brain."""
        try:
            from outreach.brain import Brain
            import time as _t
            b = Brain.get()
            if "linkedin_names" not in b._data:
                b._data["linkedin_names"] = {}
            b._data["linkedin_names"][slug] = {
                "name": name,
                "method": method,
                "cached_at": _t.time(),
            }
            b.save()
        except Exception:
            pass


    def _verify_and_score(self, email, domain, source_hint=""):
        """Run email through full verification pipeline."""
        if not email:
            return None, 0, "empty", "No email"
        if verify_suspicious(email):
            log.warning(f"Blocked suspicious email: {email}")
            return None, 0, "suspicious", f"Suspicious domain: {email}"
        result = self.verifier.verify(email, domain, source_hint=source_hint)
        log.info(f"Verification: {email} -> confidence={result['confidence']} source={result['source']}")
        if result["confidence"] == 0:
            return None, 0, result["source"], result["details"]
        return email, result["confidence"], result["source"], result["details"]

    def find(self, name, company, linkedin="", job_url_domain=""):
        r = {"email": "", "source": "", "status": "Failed", "error": "", "confidence": 0}

        # If name is empty but LinkedIn URL exists, extract name from URL first
        if not name and linkedin:
            li_name = self._extract_name_from_linkedin_url(linkedin)
            if li_name:
                log.info(f"Empty name — extracted from LinkedIn URL: '{li_name}'")
                name = li_name
            else:
                log.info(f"Empty name and LinkedIn extraction failed: {linkedin}")
        # If name is incomplete (single name or initial), try LinkedIn URL
        # If "name" field looks like a LinkedIn URL, extract name from it
        import re as _re_li
        if name and _re_li.search(r"linkedin\.com/in/", name):
            li_name = self._extract_name_from_linkedin_url(name)
            if li_name:
                log.info(f"Name field was LinkedIn URL — extracted: '{li_name}'")
                linkedin = name if not linkedin else linkedin
                name = li_name
            else:
                log.info(f"Name field was LinkedIn URL but extraction failed: {name}")

        # Guard: reject search-placeholder / URL / garbage names BEFORE building any email.
        # Prevents fake addresses like "search?q=ryder+recruiter+linkedin@ryder.com".
        _nm = (name or "").strip()
        if (not _nm or "http" in _nm.lower() or "search?q=" in _nm.lower()
                or "/" in _nm or "+" in _nm or "@" in _nm
                or "linkedin.com" in _nm.lower()):
            log.info(f"Rejected non-name input (no email built): {name!r}")
            r["error"] = "no valid name (URL/search placeholder)"
            return r

        # Check Brain for previously verified contact for this company+role
        try:
            from outreach.brain import Brain
            _b = Brain.get()
            _role = "hm" if "hiring" in name.lower() or not name else "recruiter"
            # Short-circuit: if PatternCache already has a verified pattern
            # with high confidence, use it directly without running all layers
            try:
                import re as _re_pc
                _domain_guess = _re_pc.sub(r"[^a-z0-9]", "", company.lower()) + ".com"
                _pc_result = self.pc.get(_domain_guess)
                if _pc_result and _pc_result.get("confidence", 0) >= 0.95:
                    log.info(f"PatternCache early exit for {company}: {_pc_result}")
            except Exception:
                pass
            _cached_contact = _b.get_verified_contact(company, _role)
            if _cached_contact and not _cached_contact.get("bounced"):
                _cached_email = _cached_contact.get("email", "")
                _cached_conf = _cached_contact.get("confidence", 0)
                if _cached_email and _cached_conf >= 0.7:
                    log.info(f"Brain contact reuse: {company} [{_role}] → {_cached_email} (conf={_cached_conf})")
                    r.update(
                        email=_cached_email,
                        source="brain_contact_cache",
                        status="Valid",
                        confidence=_cached_conf,
                    )
                    return r
        except Exception as _ce:
            log.debug(f"Brain contact lookup failed: {_ce}")

        parsed = NameParser.parse(name)
        if parsed and (parsed["single"] or (parsed["last"] and len(parsed["last"]) <= 2)):
            li_name = self._extract_name_from_linkedin_url(linkedin)
            if li_name:
                log.info(f"Enriched name: '{name}' → '{li_name}' (from LinkedIn URL)")
                parsed = NameParser.parse(li_name)
                name = li_name

        if not parsed:
            r["error"] = "Cannot parse name"
            return r
        if parsed["single"] and not linkedin:
            r["status"] = "Manual Review"
            r["error"] = f"Single name '{name}'"
            return r
        # Detect initial-only last names (e.g. "Shahla R.", "Nandan S")
        if parsed and not parsed["single"] and len(parsed["lc"]) <= 1:
            li_name = self._extract_name_from_linkedin_url(linkedin) if linkedin else None
            if li_name:
                log.info(f"Initial-only last name enriched: '{name}' → '{li_name}'")
                parsed = NameParser.parse(li_name)
                name = li_name
            else:
                r["status"] = "Manual Review"
                r["error"] = f"Initial-only last name: '{name}'. Cannot generate reliable email."
                r["confidence"] = 0
                log.info(f"Blocked initial-only last name: {name}")
                return r
        # Check retry tracker — skip if failed 3+ times on same domain
        retry_key = company.strip().lower()
        retries = self._load_retries()
        if retry_key in retries and retries[retry_key].get("attempts", 0) >= 3:
            old_domain = retries[retry_key].get("domain", "")
            r["error"] = f"Skipped (3+ failures on {old_domain}). Add override in .local/domain_overrides.json"
            r["status"] = "Manual Review"
            log.debug(f"Skipping {company}: {r['error']}")
            return r

        # Priority 1: domain_overrides.json (manual, always wins)
        # Priority 2: Job URL domain (extracted from application URL, very reliable)
        # Priority 3: Clearbit (can return wrong company, least reliable)
        override_domain = self._get_override(company)
        if override_domain:
            domains = [override_domain]
            log.info(f"Domain override: {company} → {override_domain}")
        elif job_url_domain:
            domains = [job_url_domain]
            log.info(f"Job URL domain: {company} → {job_url_domain}")
            # Also try Clearbit as secondary if it matches
            cb_domains = self._resolve(company)
            for cbd in cb_domains:
                if cbd not in domains:
                    domains.append(cbd)
        else:
            domains = self._resolve(company)
        if not domains:
            r["status"] = "Manual Review"
            r["error"] = f"No domain for '{company}'"
            return self._apis(parsed, "", linkedin, r) if linkedin else r
        for d in domains:
            # FIX 3: check DomainHistory confirmed pattern first
            confirmed_pat = DomainHistory.get_confirmed_pattern(d)
            if confirmed_pat and not self.pc.get(d):
                self.pc.store(d, confirmed_pat)
                log.info(f"FIX3: Loaded confirmed pattern from DomainHistory: {d} -> {confirmed_pat}")
            email = self.pc.gen_single(parsed, d)
            if email:
                # Check domain history — is this pattern known to fail?
                current_pattern = self.pc.get(d)
                if current_pattern and DomainHistory.is_failed_pattern(d, current_pattern):
                    log.info(f"Pattern '{current_pattern}' previously FAILED for {d} — skipping")
                    continue
                # Verify via provider even for cache hits
                verified_email, conf, vsource, vdetails = self._verify_and_score(email, d, source_hint="pattern_cache")
                if verified_email and conf >= AUTO_SEND_THRESHOLD:
                    r.update(email=verified_email, source=f"pattern_cache_{vsource}", status="Valid", confidence=conf)
                    log.info(f"Pattern cache verified ({conf}): {email}")
                    return r
                # If _verify_and_score returned > 0 but below threshold, still usable as Manual Review
                elif verified_email and conf > 0:
                    r.update(email=verified_email, source=f"pattern_cache_low_{conf}", status="Manual Review", confidence=conf)
                    log.info(f"Pattern cache low confidence ({conf}): {email}")
                    # Don't return — try other domains/methods for higher confidence
                    continue
        # Website mining: learn pattern from company website
        if domains and not self.pc.get(domains[0]):
            mined_pat = self.pv.mine_website_pattern(domains[0])
            if mined_pat:
                self.pc.store(domains[0], mined_pat)
                email = self.pc.gen_single(parsed, domains[0])
                if email:
                    # For Microsoft domains, verify the mined email
                    pv_result = self.pv.verify_email(email, domains[0])
                    if pv_result == "exists":
                        r.update(email=email, source="website_mining+provider", status="Valid", confidence=80)
                        # Audit log
                        try:
                            import logging as _el
                            _el.getLogger("outreach_audit").info(f"FOUND | {email} | website_mining+provider | confidence=80 | domain={domains[0]}")
                        except: pass
                        self._clear_retry(retry_key)
                        log.info(f"Website mined + verified: {email}")
                        return r
                    elif pv_result == "unknown":
                        # Non-Microsoft: verify through full pipeline
                        verified_email, conf, vsource, vdetails = self._verify_and_score(email, domains[0], source_hint="website_mining")
                        if verified_email and conf >= AUTO_SEND_THRESHOLD:
                            r.update(email=verified_email, source=f"website_mining_{vsource}", status="Valid", confidence=conf)
                        elif verified_email and conf > 0:
                            r.update(email=verified_email, source="website_mining_unverified", status="Manual Review", confidence=conf)
                        else:
                            log.info(f"Website mined email unverifiable: {email}")
                        self._clear_retry(retry_key)
                        log.info(f"Website mined (trusted): {email}")
                        return r
        if self._rok():
            # Catch-all detection: test canary email
            if self._is_catchall(domains[0]):
                log.debug(f"Catch-all detected: {domains[0]} — skipping Reacher patterns")
            else:
                pr = self._psearch(parsed, domains)
                if pr["status"] in ("Valid", "Manual Review"):
                    if pr["email"] and pr["status"] == "Valid":
                        self.pc.detect(pr["email"], parsed)
                        self._clear_retry(retry_key)
                    return pr
        result = self._apis(parsed, domains[0], linkedin, r)
        if result["email"] and result["status"] in ("Valid", "Manual Review"):
            result["confidence"] = 95 if result["status"] == "Valid" else 60
            self.pc.detect(result["email"], parsed)
            self._clear_retry(retry_key)
            # Store verified contact in Brain for future reuse
            try:
                from outreach.brain import Brain
                _role = "hm" if not linkedin else "recruiter"
                Brain.get().store_verified_contact(
                    company=company,
                    role=_role,
                    name=name,
                    email=result["email"],
                    linkedin=linkedin,
                    confidence=result["confidence"],
                )
            except Exception as _bce:
                log.debug(f"Brain contact store failed: {_bce}")
        else:
            error = result.get("error", "")
            # FIX 6: only track permanent failures — not transient credit exhaustion
            _credit_errors = ("exhausted", "key missing", "limit", "429")
            _is_credit_error = any(e in error.lower() for e in _credit_errors)
            if _is_credit_error:
                log.info(f"FIX6: Skipping retry tracking for credit error: {error}")
            else:
                log.info(f"All APIs exhausted for {parsed.get('fa','')} {parsed.get('la','')} @ {domains[0] if domains else '?'}")
                self._track_retry(retry_key, domains[0] if domains else "", error)
        return result

    @staticmethod
    def _cleanup_caches():
        """Auto-expire retry tracker (3-day TTL) and trim email verify cache (max 1000)."""
        import time
        TTL = 3 * 86400  # 3 days in seconds
        now = time.time()
        # Retry tracker cleanup
        try:
            rt = json.load(open(RETRY_FILE))
            expired = [k for k, v in rt.items() if now - v.get("ts", 0) > TTL]
            for k in expired:
                del rt[k]
                log.info(f"Retry expired (3d TTL): {k}")
            if expired:
                _atomic_write_json(RETRY_FILE, rt)
        except Exception:
            pass
        # Email verify cache trim (keep newest 1000)
        ev_path = os.path.join(os.path.dirname(RETRY_FILE), "email_verify_cache.json")
        try:
            ev = json.load(open(ev_path))
            if len(ev) > 1000:
                # Keep last 1000 entries (dict order = insertion order in Python 3.7+)
                trimmed = dict(list(ev.items())[-1000:])
                _atomic_write_json(ev_path, trimmed)
                log.info(f"Email cache trimmed: {len(ev)} -> 1000")
        except Exception:
            pass

    def _resolve(self, company):
        if not company:
            return []
        k = company.strip().lower()
        if k in self._dom:
            v = self._dom[k]
            return v["domains"] if isinstance(v, dict) else v
        doms = []
        try:
            resp = requests.get(
                CLEARBIT_URL, params={"query": company}, timeout=API_TIMEOUT
            )
            if resp.status_code == 200:
                raw_doms = [x["domain"] for x in resp.json()[:3] if x.get("domain")]
                clean_co = self._clean(company).replace(" ", "").lower()
                doms = []
                for d in raw_doms:
                    d_base = d.split(".")[0].lower()
                    if clean_co and len(clean_co) >= 3:
                        if d_base not in clean_co and clean_co not in d_base:
                            # Check if MX already confirmed this domain — if so, trust it over Clearbit
                            try:
                                from outreach.brain import Brain
                                _mx = Brain.get().get_mx(d)
                                _mx_confirmed = _mx and _mx.get("provider") in ("google", "microsoft", "office365")
                            except Exception:
                                _mx_confirmed = False
                            if _mx_confirmed:
                                log.info(f"Clearbit name-overlap skipped: {d} is MX-verified ({_mx.get('provider')})")
                            else:
                                log.warning(f"Clearbit suspect domain BLOCKED: {company} → {d} (no name overlap). Add to .local/domain_overrides.json if correct.")
                                continue  # skip this domain
                            continue  # BLOCK suspect domains instead of using them
                    doms.append(d)
        except Exception as _e:
            log.debug(f"finder op failed: {_e}")
        if not doms:
            clean = self._clean(company)
            if clean:
                cands = set()
                ns, hy = clean.replace(" ", ""), clean.replace(" ", "-")
                for t in TLDS:
                    cands.update([f"{ns}{t}", f"{hy}{t}"])
                if "&" in company:
                    na = (
                        clean.replace("&", "")
                        .replace("  ", " ")
                        .strip()
                        .replace(" ", "")
                    )
                    av = (
                        clean.replace("&", "and")
                        .replace("  ", " ")
                        .strip()
                        .replace(" ", "")
                    )
                    cands.update([f"{na}.com", f"{av}.com"])
                doms = [d for d in cands if self._mx(d)][:3]
        if not doms:
            clean = self._clean(company)
            if clean:
                d = f"{clean.replace(' ','')}.com"
                if self._mx(d):
                    doms = [d]
        # Filter: check Brain for known corrections
        filtered = []
        for d in doms:
            try:
                from outreach.brain import Brain
                correction = Brain.get().get_domain_correction(d)
                if correction:
                    log.info(f"Brain domain correction: {d} → {correction}")
                    filtered.append(correction)
                    continue
            except Exception:
                pass
            filtered.append(d)
        doms = filtered

        unique = list(dict.fromkeys(d.lower() for d in doms))
        self._dom[k] = {"domains": unique, "ts": __import__('time').time()}
        _save_domain_cache(self._dom)
        return unique

    @staticmethod
    def _clean(name):
        c = name.strip()
        for s in [
            r"\s*,?\s*inc\.?$",
            r"\s*,?\s*llc\.?$",
            r"\s*,?\s*ltd\.?$",
            r"\s*,?\s*corp\.?$",
            r"\s*,?\s*co\.?$",
            r"\s*,?\s*corporation$",
            r"\s*,?\s*company$",
            r"\s*,?\s*technologies$",
            r"\s*,?\s*technology$",
            r"\s*,?\s*group$",
            r"\s*,?\s*holdings$",
            r"\s*,?\s*solutions$",
        ]:
            c = re.sub(s, "", c, flags=re.IGNORECASE)
        return re.sub(r"[^a-zA-Z0-9\s\-&]", "", c).strip().lower()

    @staticmethod
    @lru_cache(maxsize=500)
    def _mx(domain):
        b = Brain.get()
        cached = b.get_mx(domain)
        if cached is not None:
            return cached["valid"]
        _KNOWN_GOOD = {
            "google.com","meta.com","amazon.com","apple.com","microsoft.com",
            "netflix.com","stripe.com","openai.com","databricks.com","nvidia.com",
            "intel.com","salesforce.com","adobe.com","oracle.com","ibm.com",
        }
        if domain.lower() in _KNOWN_GOOD:
            b.set_mx(domain, True, "known_good")
            return True
        if _DNS:
            try:
                dns.resolver.resolve(domain, "MX")
                provider = ""
                try:
                    answers = dns.resolver.resolve(domain, "MX")
                    mx_host = str(answers[0].exchange).lower()
                    for prov in ["google","microsoft","outlook","mimecast",
                                 "proofpoint","postini","amazon","sendgrid"]:
                        if prov in mx_host:
                            provider = prov
                            break
                except Exception:
                    pass
                b.set_mx(domain, True, provider)
                return True
            except Exception as _e:
                log.debug(f"finder op failed: {_e}")
            try:
                dns.resolver.resolve(domain, "A")
                b.set_mx(domain, True, "")
                return True
            except Exception as _e:
                logging.debug("suppressed: %s", _e)
                b.set_mx(domain, False, "")
                return False
        else:
            import socket
            try:
                socket.getaddrinfo(domain, None)
                b.set_mx(domain, True, "")
                return True
            except Exception as _e:
                logging.debug("suppressed: %s", _e)
                b.set_mx(domain, False, "")
                return False

    def _psearch(self, parsed, domains):
        r = {"email": "", "source": "pattern", "status": "Failed", "error": ""}
        if parsed["single"]:
            r["error"] = "Single name"
            return r
        # Use Brain-ranked patterns if enough data, else default order
        try:
            from outreach.outreach_config import get_ranked_patterns
            _ra, _rb, _rc = get_ranked_patterns()
            # Temporarily override for gen_phased
            import outreach.outreach_config as _oc
            _orig_a, _orig_b, _orig_c = _oc.PAT_A, _oc.PAT_B, _oc.PAT_C
            _oc.PAT_A, _oc.PAT_B, _oc.PAT_C = _ra, _rb, _rc
            pa, pb, pc = NameParser.gen_phased(parsed, domains)
            _oc.PAT_A, _oc.PAT_B, _oc.PAT_C = _orig_a, _orig_b, _orig_c
        except Exception:
            pa, pb, pc = NameParser.gen_phased(parsed, domains)
        for e in pa:
            if self._verify(e) == "safe":
                r.update(email=e, status="Valid")
                return r
        hit = self._par(pb)
        if hit:
            r.update(**hit)
            return r
        hit = self._par(pc)
        if hit:
            r.update(**hit)
            return r
        r["error"] = "No valid via patterns"
        return r

    def _par(self, emails):
        if not emails:
            return None
        valid, risky, stop = [], [], False

        def chk(e):
            return e, self._verify(e)

        with ThreadPoolExecutor(max_workers=REACHER_WORKERS) as ex:
            futs = {ex.submit(chk, e): e for e in emails}
            for fut in as_completed(futs):
                if stop:
                    break
                try:
                    e, v = fut.result()
                    if v == "safe":
                        valid.append(e)
                        stop = True
                    elif v == "risky":
                        risky.append(e)
                except Exception as _e:
                    pass  # suppressed: use log.debug(_e) to investigate
            for f in futs:
                f.cancel()
        if len(valid) == 1:
            return {
                "email": valid[0],
                "source": "pattern",
                "status": "Valid",
                "error": "",
            }
        if len(valid) > 1:
            return {
                "email": valid[0],
                "source": "pattern",
                "status": "Manual Review",
                "error": f"Catch-all:{len(valid)}",
            }
        if risky:
            return {
                "email": risky[0],
                "source": "pattern",
                "status": "Manual Review",
                "error": f"Risky:{risky[0]}",
            }
        return None

    def _verify(self, email):
        try:
            resp = requests.post(
                REACHER_URL,
                json={"to_email": email, "from_email": REACHER_FROM},
                timeout=REACHER_TIMEOUT,
            )
            if resp.status_code == 200:
                return resp.json().get("is_reachable", "unknown")
        except Exception as _e:
            log.debug(f"finder op failed: {_e}")
        return "unknown"

    @staticmethod
    def _get_override(company):
        """Check Brain + domain_overrides.json for domain corrections."""
        # Check Brain first (permanent cross-run memory)
        try:
            from outreach.brain import Brain
            b = Brain.get()
            brain_override = b._data.get("domain_overrides", {}).get(company.strip().lower())
            if brain_override:
                log.debug(f"Domain override from Brain: {company} → {brain_override}")
                return brain_override
        except Exception:
            pass
        try:
            if os.path.exists(OVERRIDES_FILE):
                overrides = json.load(open(OVERRIDES_FILE))
                # Sync file → Brain permanently
                try:
                    from outreach.brain import Brain
                    b = Brain.get()
                    bd = b._data.setdefault("domain_overrides", {})
                    changed = any(bd.get(k.lower()) != v for k, v in overrides.items())
                    if changed:
                        for k, v in overrides.items():
                            bd[k.lower()] = v
                        b.save()
                except Exception:
                    pass
                _company = company.strip()
                if _company in overrides:
                    return overrides[_company]
                for k, v in overrides.items():
                    if k.lower() == _company.lower():
                        return v
        except Exception as _e:
            log.debug(f"finder op failed: {_e}")
        return ""

    @staticmethod
    def _load_retries():
        try:
            if os.path.exists(RETRY_FILE):
                return json.load(open(RETRY_FILE))
        except Exception as _e:
            log.debug(f"finder op failed: {_e}")
        return {}

    @staticmethod
    def _track_retry(company_key, domain, error):
        retries = Finder._load_retries()
        if company_key not in retries:
            retries[company_key] = {"attempts": 0, "domain": domain, "error": ""}
        retries[company_key]["attempts"] += 1
        retries[company_key]["domain"] = domain
        retries[company_key]["error"] = error[:200]
        retries[company_key]["ts"] = time.time()
        try:
            _atomic_write_json(RETRY_FILE, retries)
        except Exception as _e:
            log.debug(f"finder op failed: {_e}")

    @staticmethod
    def _clear_retry(company_key):
        retries = Finder._load_retries()
        if company_key in retries:
            del retries[company_key]
            try:
                _atomic_write_json(RETRY_FILE, retries)
            except Exception as _e:
                log.debug(f"finder op failed: {_e}")

    def _is_catchall(self, domain):
        """Test if domain accepts all emails (catch-all)."""
        if not self._rok():
            return False
        try:
            canary = f"xq7z9k2m8p@{domain}"
            resp = requests.post(
                REACHER_URL,
                json={"to_email": canary, "from_email": "test@example.org"},
                timeout=10,
            )
            if resp.status_code == 200:
                data = resp.json()
                reachable = data.get("is_reachable", "unknown")
                if reachable == "safe":
                    log.info(f"Catch-all domain: {domain}")
                    return True
        except Exception as _e:
            log.debug(f"finder op failed: {_e}")
        return False

    def _rok(self):
        if self._reacher is not None:
            return self._reacher
        try:
            resp = requests.get(REACHER_URL.replace("/v0/check_email", "/"), timeout=3)
            self._reacher = resp.status_code in (200, 404, 405)
        except Exception as _e:
            logging.debug("suppressed: %s", _e)
            # Auto-start Docker Desktop + Reacher on macOS
            log.info("Reacher not running — attempting to start Docker Desktop...")
            try:
                import subprocess, platform

                root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                compose_file = os.path.join(root, "docker-compose.yml")

                # Step 1: Check if Docker daemon is running
                daemon_running = False
                try:
                    dc = subprocess.run(["docker", "info"], capture_output=True, text=True, timeout=5)
                    daemon_running = dc.returncode == 0
                except Exception as _e:
                    pass  # suppressed: use log.debug(_e) to investigate

                # Step 2: If daemon not running, launch Docker Desktop (macOS)
                if not daemon_running and platform.system() == "Darwin":
                    log.info("Docker daemon not running. Launching Docker Desktop...")
                    print("  Starting Docker Desktop...")
                    subprocess.run(["open", "-a", "Docker"], capture_output=True, timeout=10)
                    import time
                    for attempt in range(30):
                        time.sleep(2)
                        try:
                            check = subprocess.run(["docker", "info"], capture_output=True, text=True, timeout=5)
                            if check.returncode == 0:
                                log.info(f"Docker Desktop ready after {(attempt+1)*2}s")
                                print(f"  Docker Desktop ready ({(attempt+1)*2}s)")
                                daemon_running = True
                                break
                        except Exception as _e:
                            pass  # suppressed: use log.debug(_e) to investigate
                    if not daemon_running:
                        log.warning("Docker Desktop did not start within 60s")
                        print("  Docker Desktop did not start within 60s")

                # Step 3: Start Reacher container
                if daemon_running:
                    compose_args = ["docker", "compose"]
                    if os.path.exists(compose_file):
                        compose_args.extend(["-f", compose_file])
                    compose_args.extend(["up", "-d"])
                    result = subprocess.run(compose_args, capture_output=True, text=True, timeout=30)
                    if result.returncode == 0:
                        log.info("Reacher container started. Waiting 5s...")
                        import time; time.sleep(5)
                        try:
                            resp = requests.get(REACHER_URL.replace("/v0/check_email", "/"), timeout=3)
                            self._reacher = resp.status_code in (200, 404, 405)
                            if self._reacher:
                                log.info("Reacher is now running")
                                print("  Reacher email verifier ready")
                                return self._reacher
                        except Exception as _e:
                            pass  # suppressed: use log.debug(_e) to investigate
                    else:
                        log.warning(f"Docker compose failed: {result.stderr[:500]}")

            except FileNotFoundError:
                log.warning("Docker not found. Install Docker Desktop from docker.com")
            except subprocess.TimeoutExpired:
                log.warning("Docker command timed out")
            except Exception as e:
                log.warning(f"Docker auto-start failed: {e}")
            self._reacher = False
            log.warning("Reacher unavailable. Pattern-based email verification disabled.")
            # Notify via Brain alert so you know to start Docker
            try:
                from outreach.brain import Brain as _B
                _b = _B.get()
                _last_alert = _b._data.get("_reacher_last_alert", 0)
                import time as _t
                if _t.time() - _last_alert > 6 * 3600:  # Alert at most every 6h
                    _b._data["_reacher_last_alert"] = _t.time()
                    _b.save()
                    _b.send_email_alert(
                        "⚠️ Reacher Docker is down",
                        "Reacher email verifier is not running.\n\n"
                        "Email confidence scores will be lower until it restarts.\n\n"
                        "To fix: open Docker Desktop and wait for containers to start.\n"
                        "Or run: cd \'Job Hunt Tracker\' && docker compose up -d"
                    )
            except Exception as _re:
                log.debug(f"Reacher alert failed: {_re}")
        return self._reacher

    def _github_email_search(self, name, company):
        """Free email discovery: search GitHub for employee's public email.
        
        Many engineers have emails visible in:
        1. GitHub profile (public email field)
        2. Git commit author emails
        3. GitHub event stream
        """
        import requests as _req
        try:
            # Search GitHub users by name + company
            _first = name.split()[0] if name else ""
            _last = name.split()[-1] if name and len(name.split()) > 1 else ""
            if not _first or not _last:
                return None
            
            query = f"{_first} {_last} {company}"
            resp = _req.get(
                f"https://api.github.com/search/users?q={query}&per_page=3",
                headers={"Accept": "application/vnd.github.v3+json"},
                timeout=8,
            )
            if resp.status_code != 200:
                return None
            
            users = resp.json().get("items", [])
            for user in users:
                # Check user profile for public email
                user_resp = _req.get(
                    f"https://api.github.com/users/{user['login']}",
                    headers={"Accept": "application/vnd.github.v3+json"},
                    timeout=8,
                )
                if user_resp.status_code == 200:
                    email = user_resp.json().get("email")
                    if email and "@" in email and "noreply" not in email:
                        company_domain = email.split("@")[1].lower()
                        # Verify it's a company email, not personal
                        if any(d in company_domain for d in ["gmail", "yahoo", "hotmail", "outlook.com"]):
                            continue
                        log.info(f"GitHub discovery: {name} @ {company} → {email}")
                        return email
                
                # Check recent events for commit emails
                events_resp = _req.get(
                    f"https://api.github.com/users/{user['login']}/events/public?per_page=10",
                    headers={"Accept": "application/vnd.github.v3+json"},
                    timeout=8,
                )
                if events_resp.status_code == 200:
                    for event in events_resp.json():
                        commits = event.get("payload", {}).get("commits", [])
                        for commit in commits:
                            cemail = commit.get("author", {}).get("email", "")
                            if cemail and "@" in cemail and "noreply" not in cemail:
                                if any(d in cemail for d in ["gmail", "yahoo", "hotmail"]):
                                    continue
                                log.info(f"GitHub commit discovery: {name} → {cemail}")
                                return cemail
        except Exception as e:
            log.debug(f"GitHub email search failed: {e}")
        return None

    def _batch_reacher_verify(self, parsed, domain):
        """Test ALL email patterns at once via Reacher. Pick the best one.
        
        Instead of trying 1 pattern per day and waiting for bounces,
        test 8-10 patterns in ~15 seconds and pick the verified one.
        Returns (email, confidence, source) or (None, 0, None).
        """
        if not self._rok() or not domain:
            return None, 0, None
        
        first = parsed.get("fa", "")
        last = parsed.get("la", "")
        if not first or not last:
            return None, 0, None
        
        f = first.lower()
        l = last.lower()
        fi = f[0] if f else ""
        li_char = l[0] if l else ""
        
        # Generate all candidate patterns
        candidates = [
            f"{f}.{l}@{domain}",        # first.last
            f"{fi}{l}@{domain}",         # flast
            f"{f}{li_char}@{domain}",    # firstl
            f"{f}_{l}@{domain}",         # first_last
            f"{f}@{domain}",             # first
            f"{l}.{f}@{domain}",         # last.first
            f"{fi}.{l}@{domain}",        # f.last
            f"{f}.{li_char}@{domain}",   # first.l
            f"{f}-{l}@{domain}",         # first-last
            f"{f}{l}@{domain}",          # firstlast
        ]
        
        # Remove duplicates while preserving order
        seen = set()
        unique = []
        for c in candidates:
            if c not in seen:
                seen.add(c)
                unique.append(c)
        candidates = unique
        
        # Check pattern blacklist — skip known failed patterns
        try:
            import json
            _bl_file = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".local", "pattern_blacklist.json")
            if os.path.exists(_bl_file):
                with open(_bl_file) as _f:
                    _blacklist = json.load(_f)
                _blocked = _blacklist.get(domain, [])
                candidates = [c for c in candidates if c.split("@")[0] not in _blocked]
        except Exception:
            pass
        
        # Check bounce cache — skip known bounced emails
        try:
            from outreach.bounce_scanner import BounceScanner
            _bounced = set(BounceScanner.load_bounced().keys())
            candidates = [c for c in candidates if c.lower() not in _bounced]
        except Exception:
            pass
        
        if not candidates:
            log.info(f"Batch Reacher: all patterns exhausted for {domain}")
            return None, 0, None
        
        log.info(f"Batch Reacher: testing {len(candidates)} patterns for {first} {last} @ {domain}")
        
        # First: check if domain is catch-all (all patterns will return "safe")
        if self._is_catchall(domain):
            # Can't verify via SMTP — use MX-based priority
            log.info(f"Batch Reacher: {domain} is catch-all, using MX-priority pattern")
            mx_pattern = self._mx_priority_pattern(domain)
            if mx_pattern:
                for c in candidates:
                    local = c.split("@")[0]
                    if mx_pattern == "first.last" and "." in local and len(local.split(".")[0]) > 1:
                        return c, 50, "catchall_mx_priority"
                    elif mx_pattern == "flast" and len(local) > 2 and local[1:] == l:
                        return c, 50, "catchall_mx_priority"
            # Default: first.last for catch-all
            return candidates[0], 40, "catchall_default"
        
        # Test each candidate through Reacher
        import requests
        safe_results = []
        risky_results = []
        
        for email in candidates:
            try:
                resp = requests.post(
                    REACHER_URL,
                    json={"to_email": email, "from_email": "verify@example.org"},
                    timeout=12,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    reachable = data.get("is_reachable", "unknown")
                    smtp = data.get("smtp", {})
                    
                    if reachable == "safe":
                        safe_results.append((email, 90, "batch_reacher_safe"))
                        log.info(f"  ✓ SAFE: {email}")
                        # First safe result is enough — use it
                        break
                    elif reachable == "risky":
                        risky_results.append((email, 55, "batch_reacher_risky"))
                        log.info(f"  ~ RISKY: {email}")
                    else:
                        log.info(f"  ✗ {reachable}: {email}")
                else:
                    log.debug(f"  Reacher HTTP {resp.status_code} for {email}")
            except Exception as e:
                log.debug(f"  Reacher error for {email}: {e}")
            
            # Rate limit: small delay between checks
            import time
            time.sleep(0.5)
        
        # Return best result
        if safe_results:
            email, conf, src = safe_results[0]
            self.pc.detect(email, parsed)  # Learn the pattern
            log.info(f"Batch Reacher: VERIFIED {email} (confidence={conf})")
            return email, conf, src
        elif risky_results:
            email, conf, src = risky_results[0]
            log.info(f"Batch Reacher: best risky result {email} (confidence={conf})")
            return email, conf, src
        
        log.info(f"Batch Reacher: no valid email found for {first} {last} @ {domain}")
        return None, 0, None

    def _google_email_pattern_discovery(self, domain):
        """Search Google for public emails at this domain to learn the pattern.
        
        Searches: "@domain.com" email — finds press releases, bios, job posts
        that contain real employee emails, revealing the pattern.
        """
        try:
            import requests as _req
            query = f'"%40{domain}" email'
            resp = _req.get(
                f"https://www.google.com/search?q={query}&num=5",
                headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"},
                timeout=8,
            )
            if resp.status_code == 200:
                import re
                # Find email patterns in search results
                emails = re.findall(rf"[a-zA-Z0-9._%+-]+@{re.escape(domain)}", resp.text)
                if emails:
                    # Analyze the pattern from found emails
                    for email in emails:
                        local = email.split("@")[0]
                        if "." in local and len(local.split(".")) == 2:
                            parts = local.split(".")
                            if len(parts[0]) > 1 and len(parts[1]) > 1:
                                log.info(f"Google pattern discovery: {domain} uses first.last (from {email})")
                                return "first.last"
                            elif len(parts[0]) == 1:
                                log.info(f"Google pattern discovery: {domain} uses f.last (from {email})")
                                return "f.last"
                        elif "_" in local:
                            log.info(f"Google pattern discovery: {domain} uses first_last (from {email})")
                            return "first_last"
        except Exception:
            pass
        return None

    def _mx_priority_pattern(self, domain):
        """Check MX records to determine likely email pattern.
        
        Google Workspace → first.last (80% of the time)
        Microsoft 365 → first.last or flast (50/50)
        Self-hosted → unpredictable
        """
        try:
            import subprocess
            result = subprocess.run(
                ["dig", "+short", "MX", domain],
                capture_output=True, text=True, timeout=5
            )
            mx = result.stdout.lower()
            
            if "google" in mx or "aspmx" in mx:
                return "first.last"  # Google Workspace
            elif "outlook" in mx or "microsoft" in mx or "protection.outlook" in mx:
                return "first.last"  # Microsoft 365 most common
            elif "pphosted" in mx or "proofpoint" in mx:
                return "first.last"  # Enterprise, usually first.last
            elif "mimecast" in mx:
                return "first.last"
            else:
                return "first.last"  # Default best guess
        except Exception:
            return "first.last"
    
    def _apis(self, p, dom, li, r):
        # Live-tested 2026: only Hunter returns data (Apollo/Snov fail silently,
        # Prospeo endpoint deprecated). Hunter first; others kept as fallback
        # in case keys are renewed, but they cost a wasted call each when dead.
        default_order = ["hunter"]
        try:
            ranked = Brain.get().best_api_order(default_order)
        except Exception:
            ranked = default_order
        fns = {
            "apollo":  lambda: self._apollo(p, dom, li),
            "hunter":  lambda: self._hunter(p, dom) if dom else None,
            "snov":    lambda: self._snov(p, dom) if dom else None,
            "prospeo": lambda: self._prospeo(p, dom, li) if dom or li else None,
        }
        for name in ranked:
            fn = fns.get(name)
            if not fn:
                continue
            res = fn()
            if res and res["status"] in ("Valid", "Manual Review"):
                return res
        # FALLBACK: try public web + company site (safe, no LinkedIn) as last resort.
        try:
            from outreach.public_email_finder import find_public_email
            # was `job_url_domain` and `company`, neither of which exists in
            # _apis(self, p, dom, li, r) - so this public-web fallback raised
            # NameError and never ran. It is the last resort when every API
            # misses, which is exactly the case it was written for.
            # find_public_email only uses name and domain internally.
            _dom_pf = dom or ""
            if _dom_pf and p.get("fna") and p.get("lna"):
                _full = f"{p['fna']} {p['lna']}"
                _pub = find_public_email(_full, _dom_pf, _dom_pf, verifier=getattr(self, 'verifier', None))
                if _pub and _pub.get("email"):
                    log.info(f"Public-web fallback found {_pub['email']} ({_pub['source']})")
                    r["email"]=_pub["email"]; r["confidence"]=_pub["confidence"]
                    r["source"]=_pub["source"]; r["status"]="Valid"; r["error"]=""
                    return r
        except Exception as _pfe:
            log.debug(f"Public fallback error: {_pfe}")
        r["error"] = "All methods exhausted"
        return r

    def _req(self, method, url, **kw):
        kw.setdefault("timeout", API_TIMEOUT)
        for i in range(API_RETRIES):
            try:
                resp = requests.request(method, url, **kw)
                if resp.status_code == 429:
                    time.sleep(2**i)
                    continue
                return resp
            except requests.Timeout:
                if i < API_RETRIES - 1:
                    time.sleep(2**i)
            except Exception as _e:
                logging.debug("suppressed: %s", _e)
                break
        return None

    def _apollo(self, p, dom, li):
        r = {"email": "", "source": "apollo", "status": "Failed", "error": ""}
        if self.cr.avail("apollo") <= 0:
            r["error"] = "Apollo exhausted"
            return r
        k = key("APOLLO_API_KEY")
        if not k:
            r["error"] = "Apollo key missing"
            return r
        payload = {"api_key": k}
        if li:
            payload["linkedin_url"] = li
        else:
            payload.update(first_name=p["fa"], last_name=p["la"], organization_name=dom)
        try:
            resp = self._req("POST", APIS["apollo"]["url"], json=payload)
            if resp and resp.status_code == 200:
                per = resp.json().get("person") or {}
                email = per.get("email", "")
                self.cr.use("apollo")
                if email:
                    r["email"] = email
                    r["status"] = (
                        "Valid"
                        if per.get("email_status") == "verified"
                        else "Manual Review"
                    )
                    if r["status"] == "Valid":
                        self.cr.record_email_found("apollo")
                    return r
                r["error"] = "Apollo: no email"
        except Exception as e:
            r["error"] = f"Apollo:{str(e)[:80]}"
        return r

    def _hunter(self, p, dom):
        r = {"email": "", "source": "hunter", "status": "Failed", "error": ""}
        if self.cr.avail("hunter") <= 0:
            r["error"] = "Hunter exhausted"
            return r
        k = key("HUNTER_API_KEY")
        if not k:
            r["error"] = "Hunter key missing"
            return r
        try:
            resp = self._req(
                "GET",
                APIS["hunter"]["url"],
                params={
                    "domain": dom,
                    "first_name": p["fa"],
                    "last_name": p["la"],
                    "api_key": k,
                },
            )
            if resp and resp.status_code == 200:
                d = resp.json().get("data") or {}
                email, conf = d.get("email", ""), d.get("confidence", 0)
                self.cr.use("hunter")
                if email:
                    r["email"] = email
                    r["status"] = "Valid" if conf >= HUNTER_CONF else "Manual Review"
                    if r["status"] == "Valid":
                        self.cr.record_email_found("hunter")
                    return r
                r["error"] = "Hunter: no email"
        except Exception as e:
            r["error"] = f"Hunter:{str(e)[:80]}"
        return r

    def _snov(self, p, dom):
        r = {"email": "", "source": "snov", "status": "Failed", "error": ""}
        if self.cr.avail("snov") <= 0:
            r["error"] = "Snov exhausted"
            return r
        sk, uid = key("SNOV_API_KEY"), key("SNOV_USER_ID")
        if not sk or not uid:
            r["error"] = "Snov key missing"
            return r
        try:
            resp = self._req(
                "POST",
                APIS["snov"]["url"],
                json={
                    "firstName": p["fa"],
                    "lastName": p["la"],
                    "domain": dom,
                    "userId": uid,
                    "secret": sk,
                },
            )
            if resp and resp.status_code == 200:
                ems = resp.json().get("emails", [])
                self.cr.use("snov")
                if ems:
                    best = next((e for e in ems if e.get("status") == "valid"), ems[0])
                    r["email"] = best.get("email", "")
                    r["status"] = (
                        "Valid" if best.get("status") == "valid" else "Manual Review"
                    )
                    if r["status"] == "Valid":
                        self.cr.record_email_found("snov")
                    return r
                r["error"] = "Snov: no emails"
        except Exception as e:
            r["error"] = f"Snov:{str(e)[:80]}"
        return r

    def _prospeo(self, p, dom, li):
        r = {"email": "", "source": "prospeo", "status": "Failed", "error": ""}
        if self.cr.avail("prospeo") <= 0:
            r["error"] = "Prospeo exhausted"
            return r
        k = key("PROSPEO_API_KEY")
        if not k:
            r["error"] = "Prospeo key missing"
            return r
        try:
            # Prospeo email finder API
            params = {"key": k}
            if li:
                # LinkedIn-based lookup (more accurate)
                resp = self._req("GET", "https://api.prospeo.io/linkedin-email-finder",
                                params={**params, "url": li})
            else:
                # Domain-based lookup
                resp = self._req("GET", "https://api.prospeo.io/email-finder",
                                params={**params, "first_name": p["fa"], "last_name": p["la"], "company": dom})
            if resp and resp.status_code == 200:
                data = resp.json()
                self.cr.use("prospeo")
                email = data.get("response", {}).get("email", {}).get("email", "")
                if not email:
                    email = data.get("email", "")
                if email:
                    conf = data.get("response", {}).get("email", {}).get("email_confidence", 0)
                    r["email"] = email
                    r["status"] = "Valid" if conf and int(conf) >= 70 else "Manual Review"
                    if r["status"] == "Valid":
                        self.cr.record_email_found("prospeo")
                    return r
                r["error"] = "Prospeo: no email"
            elif resp:
                r["error"] = f"Prospeo: HTTP {resp.status_code}"
        except Exception as e:
            r["error"] = f"Prospeo:{str(e)[:80]}"
        return r

