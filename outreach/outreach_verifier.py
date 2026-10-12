#!/usr/bin/env python3
"""
Multi-Source Email Verification Engine (100% Free)

Verification gates (in order):
  Gate 1: Suspicious domain check (ATS domains, too many subdomains)
  Gate 2: MX record validation (domain must accept email)
  Gate 3: Provider detection (Google / Microsoft / Other)
  Gate 4: Provider-specific verification:
           - Google: gxlu endpoint (free, definitive)
           - Microsoft: GetCredentialType (free, definitive)
  Gate 5: Catch-all domain detection (flag domains that accept everything)
  Gate 6: Reacher SMTP verification (local Docker, free)

Confidence scoring:
  95 = API verified (Apollo/Hunter confirmed person + email)
  90 = Provider verified (Google gxlu or Microsoft 365 confirmed mailbox exists)
  85 = Pattern cache hit + provider verified
  80 = Website mining + provider verified
  75 = Reacher SMTP says "safe" + domain is NOT catch-all
  60 = Reacher SMTP says "safe" + domain IS catch-all (unreliable)
  50 = Catch-all domain, no definitive verification possible
  30 = Pattern guess, no verification (NEVER auto-send)
  0  = Explicitly rejected by any verifier

Auto-send threshold: >= 75
Manual review: 50-74
Never send: < 50
"""

import os, json, time, logging, datetime
from aggregator.atomic_json import write_json as _atomic_write_json
try:
    from outreach.brain import Brain as _Brain
except Exception:
    _Brain = None

log = logging.getLogger(__name__)

_LOCAL = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".local"
)
CIRCUIT_BREAKER_FILE = os.path.join(_LOCAL, "circuit_breaker.json")
DOMAIN_HISTORY_FILE = os.path.join(_LOCAL, "domain_pattern_history.json")

# Module-level cache — loaded once, invalidated on every write
_DH_CACHE = None

AUTO_SEND_THRESHOLD = 75
MANUAL_REVIEW_THRESHOLD = 50
MAX_DAILY_BOUNCES = 2
MAX_BOUNCE_RATE = 0.03  # 3%
MAX_DAILY_SENDS = 30


# ============================================================================
# Gate 1: Suspicious Email Check
# ============================================================================
# Country TLDs that are never US company email domains
_NON_US_TLDS = {
    ".cn", ".de", ".uk", ".co.uk", ".fr", ".jp", ".in", ".br", ".ru",
    ".au", ".ca", ".mx", ".es", ".it", ".nl", ".se", ".no", ".dk",
    ".fi", ".pl", ".cz", ".hu", ".ro", ".il", ".sg", ".hk", ".tw",
    ".kr", ".co.il", ".co.in", ".co.jp", ".co.kr", ".co.nz", ".co.za",
    ".com.au", ".com.br", ".com.cn", ".com.mx", ".com.sg", ".com.tw",
    ".org.uk", ".net.au", ".co.kr", ".co.nz",
}

# Known wrong-domain patterns: company slug → real domain
_COMPANY_DOMAIN_CORRECTIONS = {
    "centerfieldmedia": "centerfieldmedia.com",
    "centerfieldhits": "centerfieldmedia.com",  # Wrong domain returned by Clearbit
    "onsemi": "onsemi.com",
    "onseminar": "onsemi.com",  # Clearbit error
    "ivo": "ivo.ai",
    "ivory": "ivo.ai",  # Wrong domain
    "gen": "gen.com",
    "gendigital": "gen.com",
    "solidigm": "solidigm.com",
    "solidigmtech": "solidigm.com",
    "guidewire-consulting": "guidewire.com",
    "vantagewest": None,  # Credit union — different company
}


def is_suspicious_email(email):
    """
    Comprehensive email quality gate.
    Blocks: ATS domains, role-based, too-short, non-US TLDs,
            known wrong domains, concatenated names, single-letter locals.
    """
    if not email or "@" not in email:
        return True
    try:
        from outreach.outreach_config import SUSPICIOUS_EMAIL_DOMAINS
    except ImportError:
        SUSPICIOUS_EMAIL_DOMAINS = []

    domain = email.split("@")[1].lower().strip()
    local = email.split("@")[0].lower().strip()

    # Check against known ATS domains
    for sus in SUSPICIOUS_EMAIL_DOMAINS:
        if domain.endswith(sus):
            log.info(f"ATS domain blocked: {email}")
            return True

    # 3+ subdomains = likely internal routing (except co.uk etc)
    parts = domain.split(".")
    if len(parts) >= 4:
        log.info(f"Too many subdomains blocked: {email}")
        return True

    # Non-US country TLD check
    for tld in _NON_US_TLDS:
        if domain.endswith(tld):
            log.info(f"Non-US TLD blocked: {email} (ends with {tld})")
            return True

    # Known wrong domain check
    base = domain.split(".")[0].lower()
    if base in _COMPANY_DOMAIN_CORRECTIONS:
        correct = _COMPANY_DOMAIN_CORRECTIONS[base]
        if correct is None or correct != domain:
            log.info(f"Known wrong domain blocked: {email} (base={base})")
            return True

    # Local part sanity checks
    if len(local) < 3 or len(local) > 64:
        return True

    # Clean local: remove separators
    clean_local = local.replace(".", "").replace("_", "").replace("-", "")

    # Too short after cleaning (initials like pt@, sr@, s.n@)
    if len(clean_local) < 3:
        log.info(f"Too-short local blocked: {email}")
        return True

    # All digits
    if clean_local.isdigit():
        return True

    # Detect concatenated multi-person emails (donnell.taylormelissapearson@hp.com)
    # Heuristic: local part > 30 chars with no plausible single-person pattern
    if len(clean_local) > 30:
        log.info(f"Likely concatenated name blocked: {email} (local={local})")
        return True

    # Single-letter last name patterns: pt@, s.t@, j.s@
    # After removing separators, if length <= 3 it's initials
    if len(clean_local) <= 3 and not clean_local.isalnum():
        log.info(f"Initials-only blocked: {email}")
        return True

    # Check for pattern like "pt" (first initial + last initial) — too ambiguous
    # Only block if BOTH parts after split are 1-2 chars
    local_parts = local.replace("_", ".").replace("-", ".").split(".")
    if len(local_parts) >= 2:
        if all(len(p) <= 1 for p in local_parts):
            log.info(f"All-initials email blocked: {email}")
            return True
        # Single-char last part only blocks if first part is ALSO short
        # e.g. j.s@ or p.t@ = initials → block
        # e.g. sunny.p@ or muskaan.b@ = firstname.initial → allow
        if len(local_parts[-1]) == 1 and len(local_parts[0]) <= 2:
            log.info(f"Single-char last part blocked: {email}")
            return True

    # Role-based emails
    role_prefixes = {
        "info", "hello", "press", "sales", "support", "contact",
        "admin", "help", "team", "hr", "jobs", "careers", "office",
        "marketing", "media", "security", "privacy", "legal",
        "feedback", "billing", "noreply", "no-reply", "webmaster",
        "postmaster", "abuse", "hostmaster", "recruiting", "talent",
        "apply", "hire", "hiring", "resume", "resumes",
        "careers", "jobs", "internships", "university",
    }
    if local in role_prefixes:
        log.info(f"Role-based email blocked: {email}")
        return True

    return False


# ============================================================================
# Gate 2: MX Record Validation
# ============================================================================
def has_valid_mx(domain):
    """Check if domain has MX records (can receive email)."""
    try:
        import dns.resolver

        try:
            dns.resolver.resolve(domain, "MX", lifetime=10)
            return True
        except Exception as _swx:
            from aggregator.swallowed import swallow as _s; _s('outreach_verifier.has_valid_mx', _swx)
        try:
            dns.resolver.resolve(domain, "A", lifetime=10)
            return True
        except Exception:
            return False
    except ImportError:
        import socket

        try:
            socket.getaddrinfo(domain, None)
            return True
        except Exception:
            return False


# ============================================================================
# Circuit Breaker — Protects Gmail Reputation
# ============================================================================
class CircuitBreaker:
    """
    Tracks daily sends and bounces. If bounce rate exceeds threshold,
    stops all sending for the rest of the day.
    """

    @staticmethod
    def load():
        try:
            if os.path.exists(CIRCUIT_BREAKER_FILE):
                data = json.load(open(CIRCUIT_BREAKER_FILE))
                if data.get("date") != _today():
                    data = CircuitBreaker._fresh()
                    CircuitBreaker.save(data)
                # Sync Brain circuit_breaker counts from file (file is source of truth for daily)
                try:
                    if _Brain:
                        b = _Brain.get()
                        b._data["circuit_breaker"]["sent_today"] = data.get("sent", 0)
                        b._data["circuit_breaker"]["bounced_today"] = data.get("bounced", 0)
                        b._data["circuit_breaker"]["tripped"] = data.get("tripped", False)
                except Exception as _swx:
                    from aggregator.swallowed import swallow as _s; _s('outreach_verifier.load', _swx)
                return data
        except Exception as _swx:
            from aggregator.swallowed import swallow as _s; _s('outreach_verifier.load', _swx)
        return CircuitBreaker._fresh()

    @staticmethod
    def _fresh():
        return {
            "date": _today(),
            "sent": 0,
            "bounced": 0,
            "tripped": False,
            "trip_reason": "",
        }

    @staticmethod
    def save(data):
        try:
            os.makedirs(_LOCAL, exist_ok=True)
            _atomic_write_json(CIRCUIT_BREAKER_FILE, data)
        except Exception as e:
            log.error(f"Circuit breaker save failed: {e}")

    @staticmethod
    def can_send():
        """Check if we're allowed to send right now."""
        cb = CircuitBreaker.load()

        if cb.get("tripped"):
            log.warning(f"Circuit breaker TRIPPED: {cb.get('trip_reason')}")
            return False, cb.get("trip_reason", "Circuit breaker tripped")

        if cb["sent"] >= MAX_DAILY_SENDS:
            reason = f"Daily send limit reached ({MAX_DAILY_SENDS})"
            cb["tripped"] = True
            cb["trip_reason"] = reason
            CircuitBreaker.save(cb)
            return False, reason

        if cb["tripped"]:
            return False, cb.get("trip_reason", "Circuit breaker tripped")
        if cb["bounced"] >= MAX_DAILY_BOUNCES and cb["sent"] > 0:
            rate = cb["bounced"] / cb["sent"]
            if rate >= MAX_BOUNCE_RATE:
                reason = f"Bounce rate {rate:.0%} exceeds {MAX_BOUNCE_RATE:.0%} ({cb['bounced']}/{cb['sent']})"
                cb["tripped"] = True
                cb["trip_reason"] = reason
                CircuitBreaker.save(cb)
                return False, reason

        return True, ""

    @staticmethod
    def record_send():
        cb = CircuitBreaker.load()
        cb["sent"] += 1
        CircuitBreaker.save(cb)

    @staticmethod
    def record_bounce():
        cb = CircuitBreaker.load()
        cb["bounced"] += 1
        # Check if we should trip
        if cb["sent"] > 0:
            rate = cb["bounced"] / cb["sent"]
            if cb["bounced"] >= MAX_DAILY_BOUNCES and rate >= MAX_BOUNCE_RATE:
                cb["tripped"] = True
                cb["trip_reason"] = (
                    f"Auto-tripped: {cb['bounced']} bounces / {cb['sent']} sent"
                )
                log.warning(f"Circuit breaker AUTO-TRIPPED: {cb['trip_reason']}")
        CircuitBreaker.save(cb)

    @staticmethod
    def status():
        cb = CircuitBreaker.load()
        return (
            f"Sends: {cb['sent']}/{MAX_DAILY_SENDS} | "
            f"Bounces: {cb['bounced']}/{MAX_DAILY_BOUNCES} | "
            f"Status: {'TRIPPED' if cb.get('tripped') else 'OK'}"
        )


# ============================================================================
# Domain Pattern History — Learn from Successes and Failures
# ============================================================================
class DomainHistory:
    """
    Tracks which email patterns succeeded/failed for each domain.
    Patterns that bounced are permanently blacklisted for that domain.
    Patterns that delivered are permanently trusted.
    """

    @staticmethod
    def load():
        global _DH_CACHE
        if _DH_CACHE is not None:
            return _DH_CACHE
        try:
            if os.path.exists(DOMAIN_HISTORY_FILE):
                _DH_CACHE = json.load(open(DOMAIN_HISTORY_FILE))
                return _DH_CACHE
        except Exception as _swx:
            from aggregator.swallowed import swallow as _s; _s('outreach_verifier.load', _swx)
        _DH_CACHE = {}
        return _DH_CACHE

    @staticmethod
    def save(data):
        global _DH_CACHE
        _DH_CACHE = data  # invalidate in-memory cache with new data
        try:
            os.makedirs(_LOCAL, exist_ok=True)
            _atomic_write_json(DOMAIN_HISTORY_FILE, data)
        except Exception as e:
            log.debug(f"DomainHistory save failed: {e}")

    @staticmethod
    def record_success(domain, pattern, email):
        """Record a successfully delivered email pattern for a domain."""
        data = DomainHistory.load()
        domain = domain.lower().strip()
        if domain not in data:
            data[domain] = {
                "confirmed_pattern": None,
                "failed_patterns": [],
                "confirmed_at": None,
            }
        data[domain]["confirmed_pattern"] = pattern
        data[domain]["confirmed_at"] = _today()
        data[domain]["last_success_email"] = email
        # Remove from failed if it was there
        if pattern in data[domain].get("failed_patterns", []):
            data[domain]["failed_patterns"].remove(pattern)
        DomainHistory.save(data)
        log.info(f"Domain history: {domain} confirmed pattern '{pattern}' (from {email})")
        try:
            if _Brain:
                _Brain.get().record_pattern_success(domain, pattern, email)
        except Exception as _swx:
            from aggregator.swallowed import swallow as _s; _s('outreach_verifier.record_success', _swx)

    @staticmethod
    def record_failure(domain, pattern, email):
        """Record a bounced email pattern for a domain."""
        data = DomainHistory.load()
        domain = domain.lower().strip()
        if domain not in data:
            data[domain] = {
                "confirmed_pattern": None,
                "failed_patterns": [],
                "confirmed_at": None,
            }
        if pattern and pattern not in data[domain].get("failed_patterns", []):
            data[domain]["failed_patterns"].append(pattern)
        # If the confirmed pattern just failed, invalidate it
        if data[domain].get("confirmed_pattern") == pattern:
            data[domain]["confirmed_pattern"] = None
            data[domain]["confirmed_at"] = None
            log.warning(
                f"Domain history: {domain} invalidated pattern '{pattern}' (bounce)"
            )
        DomainHistory.save(data)
        try:
            if _Brain:
                _Brain.get().record_pattern_failure(domain, pattern)
        except Exception as _swx:
            from aggregator.swallowed import swallow as _s; _s('outreach_verifier.record_failure', _swx)

    @staticmethod
    def get_confirmed_pattern(domain):
        """Get the confirmed working pattern for a domain. Brain is source of truth."""
        domain = domain.lower().strip()
        # Check Brain first — it has higher confidence and more data
        try:
            if _Brain:
                brain_pat = _Brain.get().best_pattern_for(domain)
                if brain_pat:
                    return brain_pat
        except Exception as _swx:
            from aggregator.swallowed import swallow as _s; _s('outreach_verifier.get_confirmed_pattern', _swx)
        # Fall back to local file cache
        data = DomainHistory.load()
        entry = data.get(domain, {})
        pattern = entry.get("confirmed_pattern")
        confirmed_at = entry.get("confirmed_at")
        if pattern and confirmed_at:
            try:
                confirmed_date = datetime.datetime.strptime(confirmed_at, "%Y-%m-%d")
                age_days = (datetime.datetime.now() - confirmed_date).days
                if age_days > 90:
                    log.info(f"Domain history: {domain} pattern stale ({age_days}d)")
                    return None
            except Exception as _swx:
                from aggregator.swallowed import swallow as _s; _s('outreach_verifier.get_confirmed_pattern', _swx)
        return pattern

    @staticmethod
    def is_failed_pattern(domain, pattern):
        """Check if this pattern has previously bounced for this domain."""
        domain = domain.lower().strip()
        # Check Brain first
        try:
            if _Brain:
                if _Brain.get().is_failed_pattern(domain, pattern):
                    return True
        except Exception as _swx:
            from aggregator.swallowed import swallow as _s; _s('outreach_verifier.is_failed_pattern', _swx)
        data = DomainHistory.load()
        entry = data.get(domain, {})
        return pattern in entry.get("failed_patterns", [])


# ============================================================================
# Master Verification Pipeline
# ============================================================================
class EmailVerifier:
    """
    Runs an email through all verification gates.
    Returns (confidence_score, verification_source, details).
    """

    def __init__(
        self, provider_verifier=None, reacher_verify_fn=None, reacher_ok_fn=None
    ):
        """
        Args:
            provider_verifier: ProviderVerifier instance (Google/Microsoft checks)
            reacher_verify_fn: function(email) -> "safe"/"risky"/"unknown"
            reacher_ok_fn: function() -> bool (is Reacher Docker running?)
        """
        self.pv = provider_verifier
        self._reacher_verify = reacher_verify_fn
        self._reacher_ok = reacher_ok_fn

    def verify(self, email, domain=None, source_hint=""):
        """
        Full verification pipeline.

        Returns dict:
            {
                "confidence": int (0-95),
                "status": "verified" | "manual_review" | "rejected",
                "source": str (what verified it),
                "details": str (human-readable explanation),
                "send_ok": bool (confidence >= effective threshold for this domain),
            }
        """
        if not email or "@" not in email:
            return self._result(0, "rejected", "invalid", "No valid email provided")

        email = email.lower().strip()
        if domain is None:
            domain = email.split("@")[1]
        domain = domain.lower().strip()

        # ── Domain-specific threshold from Brain (raised on each bounce) ──
        # Brain.record_bounce raises threshold by +10 per bounce, max 95.
        # Domains with history of bounces require higher confidence before sending.
        _effective_threshold = AUTO_SEND_THRESHOLD  # default 75
        try:
            if _Brain:
                _dt = _Brain.get()._data.get("domain_thresholds", {})
                _domain_thresh = _dt.get(domain)
                if _domain_thresh and _domain_thresh > AUTO_SEND_THRESHOLD:
                    _effective_threshold = _domain_thresh
                    log.debug(f"Domain threshold {domain}: {_effective_threshold} (raised from bounces)")
        except Exception as _swx:
            from aggregator.swallowed import swallow as _s; _s('outreach_verifier.verify', _swx)

        # ── Gate 1: Suspicious domain check ──
        if is_suspicious_email(email):
            return self._result(
                0,
                "rejected",
                "suspicious_domain",
                f"ATS/internal domain or role-based email: {email}",
            )

        # ── Gate 2: MX record validation ──
        if not has_valid_mx(domain):
            return self._result(
                0,
                "rejected",
                "no_mx",
                f"Domain {domain} has no MX records — cannot receive email",
            )

        # ── Gate 3+4: Provider-specific verification ──
        if self.pv:
            provider = self.pv.get_provider(domain)

            if provider == "google":
                # gxlu verification — re-enabled with timeout and fallback
                try:
                    result = self.pv._verify_google(email, domain)
                    if result == "exists":
                        conf = 90 if source_hint not in ("pattern_guess",) else 85
                        return self._result(
                            conf, "verified", "google_gxlu",
                            f"Google gxlu confirmed: {email}"
                        )
                    elif result == "not_exists":
                        return self._result(
                            0, "rejected", "google_gxlu",
                            f"Google gxlu rejected: {email}"
                        )
                    # "unknown" → fall through to Reacher
                except Exception as _gxlu_e:
                    log.debug(f"gxlu error (falling through): {_gxlu_e}")

            elif provider == "microsoft":
                result = self.pv._verify_microsoft(email)
                if result == "exists":
                    conf = 90 if source_hint != "pattern_guess" else 85
                    return self._result(
                        conf,
                        "verified",
                        f"microsoft_365",
                        f"Microsoft 365 confirmed: {email}",
                    )
                elif result == "not_exists":
                    return self._result(
                        0,
                        "rejected",
                        "microsoft_365",
                        f"Microsoft 365 rejected: {email}",
                    )
                # "unknown" — fall through to Reacher

        # ── Gate 5: Catch-all detection ──
        is_catchall = False
        if self.pv:
            # Use existing catch-all detection from provider verifier
            is_catchall = domain in getattr(
                self.pv, "_catchall", {}
            ) and self.pv._catchall.get(domain, False)

        # ── Gate 6: Reacher SMTP verification ──
        if self._reacher_ok and self._reacher_ok():
            reacher_result = (
                self._reacher_verify(email) if self._reacher_verify else "unknown"
            )

            if reacher_result == "safe":
                if is_catchall:
                    # Catch-all domain: Reacher says safe but that's meaningless
                    return self._result(
                        50,
                        "manual_review",
                        "reacher_catchall",
                        f"Reacher safe BUT domain is catch-all: {domain}",
                    )
                else:
                    conf = 75
                    if source_hint in (
                        "pattern_cache",
                        "api_verified",
                        "website_mining",
                    ):
                        conf = 80
                    # Use domain-specific threshold for send_ok decision
                    return {
                        "confidence": conf,
                        "status": "verified",
                        "source": "reacher_smtp",
                        "details": f"Reacher SMTP verified: {email}",
                        "send_ok": conf >= _effective_threshold,
                    }

            elif reacher_result == "risky":
                return self._result(
                    40, "manual_review", "reacher_risky", f"Reacher says risky: {email}"
                )

            # "unknown" — Reacher couldn't determine

        # ── No verification possible ──
        # Check if we at least know the provider
        provider_name = self.pv.get_provider(domain) if self.pv else "unknown"

        if source_hint == "api_verified":
            # API (Apollo) confirmed this email — trust it even without provider verification
            # Use domain threshold for send_ok — if domain has bounced before, require higher conf
            return {
                "confidence": 85,
                "status": "verified",
                "source": "api_verified",
                "details": f"API-confirmed email, provider ({provider_name}) unverifiable",
                "send_ok": 85 >= _effective_threshold,
            }

        if source_hint in ("pattern_cache", "website_mining"):
            # Known pattern but can't verify — manual review
            return self._result(
                50,
                "manual_review",
                f"{source_hint}_unverified",
                f"Known pattern but provider ({provider_name}) unverifiable: {email}",
            )

        # Pure guess with no verification — NEVER send
        return self._result(
            30,
            "manual_review",
            "unverified",
            f"Cannot verify: {email} (provider: {provider_name})",
        )

    @staticmethod
    def _result(confidence, status, source, details):
        return {
            "confidence": confidence,
            "status": status,
            "source": source,
            "details": details,
            "send_ok": confidence >= AUTO_SEND_THRESHOLD,
        }


# ============================================================================
# Helpers
# ============================================================================
def confidence_label(score):
    """Convert numeric confidence to High/Medium/Low label."""
    if score >= AUTO_SEND_THRESHOLD:
        return "High"
    elif score >= MANUAL_REVIEW_THRESHOLD:
        return "Medium"
    return "Low"


def _today():
    return datetime.datetime.now().strftime("%Y-%m-%d")
