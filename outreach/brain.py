#!/usr/bin/env python3
"""
outreach/brain.py — Shared intelligence layer for the entire pipeline.

Every component reads from and writes to this single source of truth.
Nothing is re-learned from scratch. Nothing forgets between runs.

Usage:
    from outreach.brain import Brain
    b = Brain.get()
    b.record_pattern_success("stripe.com", "{first}.{last}", "john.smith@stripe.com")
    b.record_pattern_failure("stripe.com", "{f}{last}")
    pat = b.best_pattern_for("stripe.com")       # → "{first}.{last}"
    ranked = b.rank_patterns_for("unknown.com")  # → ["{first}.{last}", "{f}{last}", ...]
"""

import os, json, time, fcntl, logging, re
from collections import defaultdict
import datetime  # _daily_backup() used it undefined, so every
                 # backup raised NameError into save()'s except
                 # and silently wrote nothing. brain.json was
                 # corrupted today with no backup to restore.

log = logging.getLogger(__name__)

_BRAIN_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".local", "brain.json"
)

# MX provider → most likely email pattern (learned from corpus)
_PROVIDER_PATTERN_PRIORS = {
    "google": "{first}.{last}",  # Google Workspace
    "microsoft": "{first}.{last}",  # Microsoft 365 / Exchange Online
    "outlook": "{first}.{last}",
    "mimecast": "{first}.{last}",
    "proofpoint": "{first}.{last}",
    "postini": "{first}.{last}",
    "amazon": "{f}{last}",  # Amazon SES domains
    "sendgrid": "{first}.{last}",
}

# Global fallback order — updated by Brain as evidence accumulates
_DEFAULT_PATTERN_ORDER = [
    "{first}.{last}",
    "{f}{last}",
    "{first}_{last}",
    "{first}{last}",
    "{f}.{last}",
    "{first}",
]

# Rejection reasons that are permanent (never re-check same company)
_PERMANENT_REJECTION_REASONS = {
    "security clearance",
    "clearance required",
    "us person",
    "citizenship required",
    "skillbridge",
    "military only",
    "defense contract",
    "polygraph",
}

# Rejection reasons that are per-role, not per-company (don't blacklist company)
_ROLE_REJECTION_REASONS = {
    "undergraduate only",
    "phd only",
    "graduation year",
    "wrong season",
    "spring internship",
    "not internship",
}


class Brain:
    _instance = None
    _lock = __import__('threading').Lock()

    @classmethod
    def get(cls) -> "Brain":
        """Always returns the same instance. Thread-safe singleton."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls):
        """Force reload from disk (call after external writes)."""
        cls._instance = None

    def __init__(self):
        self._path = _BRAIN_FILE
        os.makedirs(os.path.dirname(self._path), exist_ok=True)
        self._data = self._load()
        # One-time migration from legacy .local/ files
        try:
            self.migrate_legacy_files()
        except Exception as _me:
            log.debug(f"Brain migration failed (non-fatal): {_me}")

    # ── Persistence ──────────────────────────────────────────────────────────

    def _load(self) -> dict:
        if not os.path.exists(self._path):
            return self._default()
        try:
            with open(self._path, "r") as f:
                fcntl.flock(f, fcntl.LOCK_SH)
                d = json.load(f)
                fcntl.flock(f, fcntl.LOCK_UN)
            # Ensure all top-level keys exist (forward-compat)
            defaults = self._default()
            for k, v in defaults.items():
                if k not in d:
                    d[k] = v
            return d
        except Exception as e:
            # A transient read failure used to return _default() - an empty
            # brain - and the next save() wrote that over the real file,
            # destroying every learned key. Twice in five days. Mark the
            # load as failed so save() refuses to overwrite good data.
            log.error(f"Brain load FAILED, refusing to overwrite: {e}")
            self._load_failed = True
            return self._default()

    def save(self):
        """Atomic write with exclusive lock. Also prunes stale data on save."""
        if getattr(self, "_load_failed", False):
            log.error("Brain save aborted: load had failed, data is not trustworthy")
            return
        try:
            self._prune_stale()
            # Preserve top-level keys written by OTHER processes (e.g.
            # ats_discovery writes discovered_ats / known_ats_slugs).
            # Without this, Brain's in-memory copy silently erases them.
            _out = dict(self._data)
            try:
                if os.path.exists(self._path):
                    with open(self._path) as _cf:
                        _current = json.load(_cf)
                    for _k, _v in _current.items():
                        if _k not in _out:
                            _out[_k] = _v
            except Exception:
                pass
            tmp = self._path + ".tmp"
            with open(tmp, "w") as f:
                fcntl.flock(f, fcntl.LOCK_EX)
                json.dump(_out, f, indent=2)
                fcntl.flock(f, fcntl.LOCK_UN)
            os.replace(tmp, self._path)
            # Daily backup — keep last 7 days
            self._daily_backup()
        except Exception as e:
            log.debug(f"Brain save failed: {e}")

    def _daily_backup(self):
        """Write daily backup of brain.json, keep last 7."""
        try:
            today = datetime.datetime.now().strftime("%Y-%m-%d")
            backup_path = self._path.replace("brain.json", f"brain_backup_{today}.json")
            if not os.path.exists(backup_path):
                import shutil
                shutil.copy2(self._path, backup_path)
                log.info(f"Brain backup: {backup_path}")
            # Prune backups older than 7 days
            import glob
            backup_dir = os.path.dirname(self._path)
            backups = sorted(glob.glob(os.path.join(backup_dir, "brain_backup_*.json")))
            for old_backup in backups[:-7]:
                os.remove(old_backup)
                log.debug(f"Removed old brain backup: {old_backup}")
        except Exception as e:
            log.debug(f"Brain backup failed (non-fatal): {e}")

    def _prune_stale(self):
        """Prune unbounded keys to prevent brain.json growing forever."""
        try:
            # simplify_retry_queue: remove exhausted entries older than 7 days
            # exhausted_at is a float unix timestamp
            srq = self._data.get("simplify_retry_queue", {})
            cutoff_ts = (datetime.datetime.now() - datetime.timedelta(days=7)).timestamp()
            self._data["simplify_retry_queue"] = {
                k: v for k, v in srq.items()
                if not (v.get("exhausted", False) and float(v.get("exhausted_at", 9e12)) < cutoff_ts)
            }
            # job_id_registry: cap at 2000 most recent
            jir = self._data.get("job_id_registry", {})
            if len(jir) > 2000:
                self._data["job_id_registry"] = dict(list(jir.items())[-1500:])
            # draft_history: cap at 500
            dh = self._data.get("draft_history", [])
            if len(dh) > 500:
                self._data["draft_history"] = dh[-400:]
            # run_history in brain: cap at 30 entries (SQLite is source of truth)
            rh = self._data.get("run_history", {})
            if len(rh) > 30:
                self._data["run_history"] = dict(list(rh.items())[-20:])
        except Exception as e:
            log.debug(f"Brain prune failed: {e}")

    def _default(self) -> dict:
        return {
            "domains": {},
            "companies": {},
            "apis": {},
            "patterns": {
                "global_success_rates": {p: 0.0 for p in _DEFAULT_PATTERN_ORDER},
                "total_attempts": 0,
                "total_successes": 0,
            },
            "aggregator": {
                "source_quality": {},
                "rejection_patterns": {},
                "title_classifications": {},
            },
            "circuit_breaker": {
                "sent_today": 0,
                "bounced_today": 0,
                "tripped": False,
                "last_trip_alerted_at": None,
                "pre_warned_at": None,
            },
            "run_history": {
                "last_run_at": None,
                "consecutive_failures": 0,
                "selenium_working": True,
                "chromedriver_version": None,
                "repair_history": [],
            },
            "simplify_retry_queue": {},
            "mx_cache": {},
            "linkedin_names": {},
            "domain_corrections": {},
            "company_contacts": {},
        }

    # ── Domain / Pattern API ─────────────────────────────────────────────────

    def _domain_entry(self, domain: str) -> dict:
        d = domain.lower()
        if d not in self._data["domains"]:
            self._data["domains"][d] = {
                "email_pattern": None,
                "pattern_confidence": 0.0,
                "pattern_attempts": 0,
                "pattern_successes": 0,
                "pattern_failures": [],
                "is_catchall": False,
                "mx_valid": None,
                "mx_provider": None,
                "mx_checked_at": None,
                "last_success_at": None,
                "provider": None,
            }
        return self._data["domains"][d]

    def record_pattern_success(self, domain: str, pattern: str, email: str):
        e = self._domain_entry(domain)
        e["email_pattern"] = pattern
        e["pattern_attempts"] = e.get("pattern_attempts", 0) + 1
        e["pattern_successes"] = e.get("pattern_successes", 0) + 1
        e["last_success_at"] = time.time()
        if pattern in e.get("pattern_failures", []):
            e["pattern_failures"].remove(pattern)
        # Update confidence: successes / attempts, weighted toward recent
        att = e["pattern_attempts"]
        suc = e["pattern_successes"]
        e["pattern_confidence"] = round(suc / att, 3) if att else 0.0
        # Update global success rates
        gr = self._data["patterns"]["global_success_rates"]
        gr[pattern] = gr.get(pattern, 0.0)
        total = self._data["patterns"]["total_successes"] + 1
        # Exponential moving average (α=0.05) — slow decay so old data counts
        gr[pattern] = round(gr[pattern] * 0.95 + 1.0 * 0.05, 4)
        self._data["patterns"]["total_attempts"] = (
            self._data["patterns"].get("total_attempts", 0) + 1
        )
        self._data["patterns"]["total_successes"] = total
        log.debug(f"Brain: pattern success {domain} → {pattern}")
        self.save()

    def record_pattern_failure(self, domain: str, pattern: str):
        e = self._domain_entry(domain)
        e["pattern_attempts"] = e.get("pattern_attempts", 0) + 1
        fails = e.get("pattern_failures", [])
        if pattern not in fails:
            fails.append(pattern)
        e["pattern_failures"] = fails
        att = e["pattern_attempts"]
        suc = e.get("pattern_successes", 0)
        e["pattern_confidence"] = round(suc / att, 3) if att else 0.0
        # Decay global rate for this pattern
        gr = self._data["patterns"]["global_success_rates"]
        gr[pattern] = round(gr.get(pattern, 0.0) * 0.95, 4)
        self._data["patterns"]["total_attempts"] = (
            self._data["patterns"].get("total_attempts", 0) + 1
        )
        log.debug(f"Brain: pattern failure {domain} → {pattern}")
        self.save()

    def best_pattern_for(self, domain: str) -> str | None:
        """Return confirmed working pattern for domain, or None."""
        e = self._data["domains"].get(domain.lower(), {})
        pat = e.get("email_pattern")
        if pat and e.get("pattern_confidence", 0) >= 0.5:
            return pat
        return None

    def is_failed_pattern(self, domain: str, pattern: str) -> bool:
        e = self._data["domains"].get(domain.lower(), {})
        return pattern in e.get("pattern_failures", [])

    def rank_patterns_for(self, domain: str, candidates: list) -> list:
        """
        Rank candidate patterns by posterior probability.
        Uses: global success rate × domain provider prior × (1 - failure penalty).
        Returns sorted list, best first.
        """
        domain = domain.lower()
        e = self._data["domains"].get(domain, {})
        provider = e.get("mx_provider", "")
        fails = set(e.get("pattern_failures", []))
        provider_prior = _PROVIDER_PATTERN_PRIORS.get(provider, "")
        gr = self._data["patterns"]["global_success_rates"]

        def score(p: str) -> float:
            if p in fails:
                return -1.0
            base = gr.get(p, 0.05)
            # Provider bonus: +0.3 if provider prior matches
            bonus = 0.3 if provider_prior == p else 0.0
            # Domain evidence bonus: confirmed pattern gets +0.5
            if e.get("email_pattern") == p and e.get("pattern_confidence", 0) >= 0.5:
                bonus += 0.5
            return base + bonus

        return sorted(
            [p for p in candidates if p not in fails], key=score, reverse=True
        )

    # ── MX Cache ─────────────────────────────────────────────────────────────

    def get_mx(self, domain: str) -> dict | None:
        """Return cached MX result if < 7 days old."""
        entry = self._data["mx_cache"].get(domain.lower())
        if not entry:
            return None
        if time.time() - entry.get("ts", 0) > 7 * 86400:
            return None
        return entry

    def set_mx(self, domain: str, valid: bool, provider: str = ""):
        self._data["mx_cache"][domain.lower()] = {
            "valid": valid,
            "provider": provider,
            "ts": time.time(),
        }
        # Also update domain entry provider
        e = self._domain_entry(domain)
        e["mx_valid"] = valid
        e["mx_provider"] = provider
        e["mx_checked_at"] = time.time()
        self.save()

    def mx_provider_for(self, domain: str) -> str:
        """Return known MX provider for domain, or empty string."""
        entry = self._data["mx_cache"].get(domain.lower(), {})
        return entry.get("provider", "")

    # ── Company API ──────────────────────────────────────────────────────────

    def _company_entry(self, company: str) -> dict:
        k = re.sub(r"[^a-z0-9]", "", company.lower().strip())
        if k not in self._data["companies"]:
            self._data["companies"][k] = {
                "name": company,
                "domain": None,
                "domain_confidence": 0.0,
                "sponsorship": "Unknown",
                "sponsorship_source": None,
                "sponsorship_confidence": 0.0,
                "blacklist": False,
                "blacklist_reason": None,
                "rejection_count": 0,
                "rejection_reasons": {},
                "hq_location": None,
                "outreach_count": 0,
                "last_outreach_at": None,
                "rejection_velocity": [],
            }
        return self._data["companies"][k]

    def record_company_rejection(self, company: str, reason: str):
        e = self._company_entry(company)
        e["rejection_count"] = e.get("rejection_count", 0) + 1
        reasons = e.get("rejection_reasons", {})
        short = reason.split("(")[0].strip()[:60].lower()
        reasons[short] = reasons.get(short, 0) + 1
        e["rejection_reasons"] = reasons
        # Track velocity: timestamps of rejections for this company
        vel = e.get("rejection_velocity", [])
        vel.append(time.time())
        # Keep last 20 rejections only
        e["rejection_velocity"] = vel[-20:]
        # Auto-blacklist logic:
        # 1. Permanent reason → blacklist after 1 rejection
        # 2. 10+ same reason in any timeframe → blacklist
        # 3. NEVER blacklist for age, salary, season, or per-job reasons
        reason_lower = reason.lower()
        is_permanent = any(p in reason_lower for p in _PERMANENT_REJECTION_REASONS)
        is_role_only = any(p in reason_lower for p in _ROLE_REJECTION_REASONS)
        # Never blacklist for transient reasons
        is_transient = any(p in reason_lower for p in [
            "posted", "days ago", "too old", "salary", "low salary",
            "season", "spring", "summer", "fall", "wrong season",
            "duplicate", "quality", "parse", "http", "fetch"
        ])
        if is_transient:
            return  # Never blacklist for transient reasons
        if is_permanent and not is_role_only:
            if not e.get("blacklist"):
                e["blacklist"] = True
                e["blacklist_reason"] = f"Auto: {short}"
                log.info(f"Brain: auto-blacklisted {company} (permanent: {short})")
                self.save()
                return
        top_reason_count = max(reasons.values()) if reasons else 0
        if top_reason_count >= 10 and not is_role_only and not is_transient:
            # Check velocity: were 10+ recent rejections within 30 days?
            recent = [t for t in vel if time.time() - t < 30 * 86400]
            if len(recent) >= 10 or top_reason_count >= 15:
                if not e.get("blacklist"):
                    top_reason = max(reasons, key=reasons.get)
                    e["blacklist"] = True
                    e["blacklist_reason"] = f"Auto({top_reason_count}x): {top_reason}"
                    log.info(
                        f"Brain: auto-blacklisted {company} ({top_reason_count}x {top_reason})"
                    )
        self.save()

    def get_sponsorship(self, company: str) -> str:
        """Return known sponsorship status, or 'Unknown'."""
        k = re.sub(r"[^a-z0-9]", "", company.lower().strip())
        return self._data["companies"].get(k, {}).get("sponsorship", "Unknown")

    def set_sponsorship(
        self, company: str, status: str, source: str, confidence: float = 0.7
    ):
        e = self._company_entry(company)
        # Only upgrade confidence, never downgrade known "Yes" to "Unknown"
        current = e.get("sponsorship", "Unknown")
        current_conf = e.get("sponsorship_confidence", 0.0)
        if status == "Yes" or confidence > current_conf or current == "Unknown":
            e["sponsorship"] = status
            e["sponsorship_source"] = source
            e["sponsorship_confidence"] = confidence
            self.save()

    def is_blacklisted(self, company: str) -> tuple[bool, str]:
        k = re.sub(r"[^a-z0-9]", "", company.lower().strip())
        e = self._data["companies"].get(k, {})
        return e.get("blacklist", False), e.get("blacklist_reason", "")

    def new_blacklisted_companies(self) -> list[dict]:
        """Return companies auto-blacklisted since last config sync."""
        results = []
        for k, e in self._data["companies"].items():
            if e.get("blacklist") and e.get("blacklist_reason", "").startswith("Auto"):
                results.append(
                    {"name": e.get("name", k), "reason": e["blacklist_reason"]}
                )
        return results

    # ── API ROI Tracking ─────────────────────────────────────────────────────

    def record_api_result(self, api_name: str, credit_used: bool, email_found: bool):
        apis = self._data["apis"]
        if api_name not in apis:
            apis[api_name] = {
                "credits_used_this_month": 0,
                "valid_emails_returned": 0,
                "total_calls": 0,
                "value_per_credit": 0.0,
                "daily_usage": [],
                "last_reset": time.strftime("%Y-%m"),
            }
        e = apis[api_name]
        # Monthly reset
        if e.get("last_reset") != time.strftime("%Y-%m"):
            e["credits_used_this_month"] = 0
            e["valid_emails_returned"] = 0
            e["total_calls"] = 0
            e["daily_usage"] = []
            e["last_reset"] = time.strftime("%Y-%m")
        if credit_used:
            e["credits_used_this_month"] = e.get("credits_used_this_month", 0) + 1
        if email_found:
            e["valid_emails_returned"] = e.get("valid_emails_returned", 0) + 1
        e["total_calls"] = e.get("total_calls", 0) + 1
        used = e["credits_used_this_month"]
        found = e["valid_emails_returned"]
        e["value_per_credit"] = round(found / used, 3) if used else 0.0
        # Daily usage for rolling average
        today = time.strftime("%Y-%m-%d")
        daily = e.get("daily_usage", [])
        if daily and daily[-1]["date"] == today:
            if credit_used:
                daily[-1]["used"] = daily[-1].get("used", 0) + 1
        else:
            daily.append({"date": today, "used": 1 if credit_used else 0})
        e["daily_usage"] = daily[-30:]  # keep 30 days
        self.save()

    def api_burn_rate_alert(self, api_name: str, monthly_limit: int) -> str | None:
        """
        Returns alert string if API will exhaust before month end, else None.
        Uses 7-day rolling average for stability.
        """
        e = self._data["apis"].get(api_name, {})
        daily = e.get("daily_usage", [])
        if len(daily) < 3:
            return None
        recent = daily[-7:]
        avg_daily = sum(d.get("used", 0) for d in recent) / len(recent)
        if avg_daily <= 0:
            return None
        used = e.get("credits_used_this_month", 0)
        remaining = monthly_limit - used
        days_until_exhaustion = remaining / avg_daily if avg_daily > 0 else 999
        # Days left in month
        import calendar, datetime

        now = datetime.date.today()
        days_in_month = calendar.monthrange(now.year, now.month)[1]
        days_left = days_in_month - now.day
        if days_until_exhaustion < days_left - 2:
            return (
                f"{api_name}: {used}/{monthly_limit} used, "
                f"~{avg_daily:.1f}/day → exhausts in {days_until_exhaustion:.0f}d "
                f"({days_left}d left in month)"
            )
        return None

    def best_api_order(self, default_order: list) -> list:
        """
        Return APIs sorted by value_per_credit descending.
        APIs with no data stay in default order.
        """

        def vpc(name):
            return self._data["apis"].get(name, {}).get("value_per_credit", 0.0)

        scored = [(name, vpc(name)) for name in default_order]
        scored.sort(key=lambda x: x[1], reverse=True)
        return [name for name, _ in scored]

    # ── Simplify Retry Queue ─────────────────────────────────────────────────

    def queue_simplify_retry(self, job_id: str, url: str, failure_reason: str):
        """Add a failed Simplify URL to the retry queue with classified timing."""
        q = self._data["simplify_retry_queue"]
        if job_id in q and q[job_id].get("attempts", 0) >= 3:
            return  # Max retries reached
        reason_lower = failure_reason.lower()
        if "timeout" in reason_lower or "connection" in reason_lower:
            next_retry_in = 7200  # 2h
            category = "timeout"
        elif "404" in reason_lower or "not found" in reason_lower:
            next_retry_in = 86400  # 24h — likely expired
            category = "expired"
        elif "parse" in reason_lower or "extract" in reason_lower:
            next_retry_in = 43200  # 12h
            category = "parse_error"
        else:
            next_retry_in = 21600  # 6h default
            category = "unknown"
        existing = q.get(job_id, {})
        q[job_id] = {
            "url": url,
            "job_id": job_id,
            "category": category,
            "failure_reason": failure_reason,
            "attempts": existing.get("attempts", 0) + 1,
            "first_failed_at": existing.get("first_failed_at", time.time()),
            "last_failed_at": time.time(),
            "next_retry_at": time.time() + next_retry_in,
        }
        self.save()
        log.info(
            f"Brain: queued Simplify retry {job_id} ({category}, retry in {next_retry_in//3600}h)"
        )

    def get_simplify_retries_due(self) -> list:
        """Return list of Simplify entries ready for retry."""
        now = time.time()
        due = []
        for jid, entry in self._data["simplify_retry_queue"].items():
            if entry.get("attempts", 0) >= 3:
                continue
            if entry.get("next_retry_at", 0) <= now:
                due.append(entry)
        return due

    def mark_simplify_retry_success(self, job_id: str):
        self._data["simplify_retry_queue"].pop(job_id, None)
        self.save()

    def mark_simplify_retry_exhausted(self, job_id: str):
        if job_id in self._data["simplify_retry_queue"]:
            self._data["simplify_retry_queue"][job_id]["exhausted"] = True
            self._data["simplify_retry_queue"][job_id]["exhausted_at"] = time.time()
        self.save()

    # ── Circuit Breaker ───────────────────────────────────────────────────────

    def cb_record_send(self):
        cb = self._data["circuit_breaker"]
        cb["sent_today"] = cb.get("sent_today", 0) + 1
        self.save()

    def cb_record_bounce(self):
        cb = self._data["circuit_breaker"]
        cb["bounced_today"] = cb.get("bounced_today", 0) + 1
        self.save()

    def cb_should_pre_warn(self) -> bool:
        """True if bounce rate > 15% and we haven't warned in last 6h."""
        cb = self._data["circuit_breaker"]
        sent = cb.get("sent_today", 0)
        bounced = cb.get("bounced_today", 0)
        if sent < 5 or bounced == 0:
            return False
        rate = bounced / sent
        if rate < 0.15:
            return False
        last = cb.get("pre_warned_at")
        if last and time.time() - last < 6 * 3600:
            return False
        return True

    def cb_record_pre_warn(self):
        self._data["circuit_breaker"]["pre_warned_at"] = time.time()
        self.save()

    def cb_should_alert_trip(self) -> bool:
        """True if tripped and we haven't alerted for this trip yet."""
        cb = self._data["circuit_breaker"]
        if not cb.get("tripped"):
            return False
        last = cb.get("last_trip_alerted_at")
        if last and time.time() - last < 6 * 3600:
            return False
        return True

    def cb_record_trip_alert(self):
        self._data["circuit_breaker"]["last_trip_alerted_at"] = time.time()
        self.save()

    # ── Selenium Health ───────────────────────────────────────────────────────

    def selenium_is_working(self) -> bool:
        return self._data["run_history"].get("selenium_working", True)

    def record_selenium_ok(self, version: str = ""):
        rh = self._data["run_history"]
        rh["selenium_working"] = True
        rh["consecutive_failures"] = 0
        if version:
            rh["chromedriver_version"] = version
        self.save()

    def record_selenium_failure(self, error: str) -> int:
        """Returns consecutive failure count."""
        rh = self._data["run_history"]
        rh["selenium_working"] = False
        rh["consecutive_failures"] = rh.get("consecutive_failures", 0) + 1
        hist = rh.get("repair_history", [])
        hist.append({"ts": time.time(), "error": error[:200], "repaired": False})
        rh["repair_history"] = hist[-20:]
        self.save()
        return rh["consecutive_failures"]

    def record_selenium_repair(self, method: str, success: bool):
        rh = self._data["run_history"]
        hist = rh.get("repair_history", [])
        if hist:
            hist[-1]["repair_method"] = method
            hist[-1]["repaired"] = success
        if success:
            rh["selenium_working"] = True
            rh["consecutive_failures"] = 0
        rh["repair_history"] = hist
        self.save()

    # ── Aggregator Source Quality ─────────────────────────────────────────────

    def record_source_run(self, source: str, fetched: int, valid: int):
        sq = self._data["aggregator"]["source_quality"]
        if source not in sq:
            sq[source] = {
                "fetched": 0,
                "valid": 0,
                "runs": 0,
                "quality_rate": 0.0,
                "rate_history": [],
            }
        e = sq[source]
        e["fetched"] = e.get("fetched", 0) + fetched
        e["valid"] = e.get("valid", 0) + valid
        e["runs"] = e.get("runs", 0) + 1
        total_f = e["fetched"]
        total_v = e["valid"]
        rate = round(total_v / total_f, 3) if total_f else 0.0
        e["quality_rate"] = rate
        # Rolling rate history (last 30 runs)
        hist = e.get("rate_history", [])
        run_rate = round(valid / fetched, 3) if fetched else 0.0
        hist.append({"ts": time.time(), "rate": run_rate})
        e["rate_history"] = hist[-30:]
        # Decay detection: if last 7 runs avg < 50% of lifetime avg → flag
        if len(hist) >= 7:
            recent_avg = sum(h["rate"] for h in hist[-7:]) / 7
            lifetime_avg = rate
            if lifetime_avg > 0 and recent_avg < lifetime_avg * 0.5:
                log.warning(
                    f"Brain: source quality decay detected for {source}: "
                    f"recent={recent_avg:.2%} vs lifetime={lifetime_avg:.2%}"
                )
                try:
                    self.send_email_alert(
                        f"⚠️ Source quality decay: {source}",
                        f"Valid rate for {source} dropped to {recent_avg:.0%} "
                        f"(lifetime avg: {lifetime_avg:.0%}).\n\n"
                        f"Check if the source changed format or went stale."
                    )
                except Exception:
                    pass
        self.save()

    # ── Job ID Dedup Registry ─────────────────────────────────────────────────

    def normalize_job_id(self, job_id: str) -> str:
        """Normalize job ID for comparison: strip leading zeros, lowercase, alphanum only."""
        if not job_id or job_id in ("N/A", ""):
            return ""
        n = re.sub(r"[^a-z0-9]", "", job_id.lower())
        n = n.lstrip("0") or n
        return n

    def is_duplicate_job_id(
        self, job_id: str, company: str = "", title: str = ""
    ) -> bool:
        """Check if normalized job_id was seen in last 90 days."""
        nid = self.normalize_job_id(job_id)
        if not nid:
            return False
        registry = self._data.get("job_id_registry", {})
        entry = registry.get(nid)
        if not entry:
            return False
        # Expire after 90 days
        if time.time() - entry.get("ts", 0) > 90 * 86400:
            return False
        # Fuzzy title check: same company + similar title = dupe even with different ID format
        if company and title and entry.get("company"):
            if re.sub(r"[^a-z0-9]", "", company.lower()) == re.sub(
                r"[^a-z0-9]", "", entry["company"].lower()
            ):
                t1 = re.sub(r"[^a-z0-9 ]", "", title.lower())
                t2 = re.sub(r"[^a-z0-9 ]", "", entry.get("title", "").lower())
                if (
                    t1
                    and t2
                    and (t1 in t2 or t2 in t1 or self._levenshtein(t1, t2) <= 5)
                ):
                    return True
        return True

    def register_job_id(self, job_id: str, company: str = "", title: str = ""):
        nid = self.normalize_job_id(job_id)
        if not nid:
            return
        if "job_id_registry" not in self._data:
            self._data["job_id_registry"] = {}
        self._data["job_id_registry"][nid] = {
            "ts": time.time(),
            "company": company,
            "title": title,
            "raw": job_id,
        }
        # Prune + save used to run on EVERY registration - roughly 1,200 per
        # run, each one rewriting a 523KB file AND rebuilding the whole
        # registry dict. Measured at 10ms per call: ~12s of pure disk I/O per
        # run, and it got worse as the registry grew.
        #
        # Now: prune only when the registry is genuinely large, and throttle
        # saves to once per minute. run_aggregator already calls Brain.save()
        # once at the end of the run, so nothing is lost on a clean exit.
        if len(self._data["job_id_registry"]) > 5000:
            cutoff = time.time() - 90 * 86400
            self._data["job_id_registry"] = {
                k: v
                for k, v in self._data["job_id_registry"].items()
                if v.get("ts", 0) > cutoff
            }
        if time.time() - getattr(self, "_last_jid_save", 0) > 60:
            self._last_jid_save = time.time()
            self.save()

    @staticmethod
    def _levenshtein(s1: str, s2: str) -> int:
        if len(s1) < len(s2):
            s1, s2 = s2, s1
        if not s2:
            return len(s1)
        prev = list(range(len(s2) + 1))
        for i, c1 in enumerate(s1):
            curr = [i + 1]
            for j, c2 in enumerate(s2):
                curr.append(min(prev[j + 1] + 1, curr[j] + 1, prev[j] + (c1 != c2)))
            prev = curr
        return prev[-1]


    # ── Domain Corrections (learned wrong→right mappings) ────────────────────

    def learn_domain_correction(self, wrong_domain: str, correct_domain: str, company: str = ""):
        """Record that wrong_domain should be corrected to correct_domain."""
        if "domain_corrections" not in self._data:
            self._data["domain_corrections"] = {}
        wrong_base = wrong_domain.split(".")[0].lower()
        self._data["domain_corrections"][wrong_base] = {
            "correct": correct_domain,
            "wrong": wrong_domain,
            "company": company,
            "learned_at": time.time(),
        }
        log.info(f"Brain: domain correction learned: {wrong_domain} → {correct_domain}")
        self.save()

    def record_source_quality(self, source: str, valid: int, rejected: int):
        """Track daily source quality for monitoring."""
        import datetime
        today = datetime.date.today().isoformat()
        sq = self._data.setdefault("source_quality", {})
        if source not in sq:
            sq[source] = []
        sq[source].append({"date": today, "valid": valid, "rejected": rejected})
        # Keep last 30 days only
        sq[source] = sq[source][-30:]
        self.save()

    def get_source_quality_report(self) -> str:
        """Return a summary of source quality over last 7 days."""
        import datetime
        cutoff = (datetime.date.today() - datetime.timedelta(days=7)).isoformat()
        sq = self._data.get("source_quality", {})
        lines = ["Source Quality (last 7 days):"]
        for source, records in sq.items():
            recent = [r for r in records if r["date"] >= cutoff]
            if not recent:
                continue
            total_valid = sum(r["valid"] for r in recent)
            total_rejected = sum(r["rejected"] for r in recent)
            total = total_valid + total_rejected
            pct = f"{100*total_valid//total}%" if total > 0 else "N/A"
            lines.append(f"  {source}: {total_valid} valid / {total} total ({pct}) over {len(recent)} runs")
        return "\n".join(lines) if len(lines) > 1 else "No source quality data yet"

    def store_verified_contact(self, company: str, role: str, name: str, 
                                email: str, linkedin: str = "", confidence: float = 0):
        """Store a verified contact for a company permanently."""
        key = re.sub(r"[^a-z0-9]", "", company.lower().strip())
        if "company_contacts" not in self._data:
            self._data["company_contacts"] = {}
        if key not in self._data["company_contacts"]:
            self._data["company_contacts"][key] = {}
        self._data["company_contacts"][key][role] = {
            "name": name,
            "email": email,
            "linkedin": linkedin,
            "confidence": confidence,
            "verified_at": time.time(),
            "bounced": False,
            "bounce_count": 0,
        }
        log.info(f"Brain: stored contact {name} ({email}) for {company} [{role}]")
        self.save()

    def get_verified_contact(self, company: str, role: str) -> dict | None:
        """Get a previously verified contact for a company."""
        key = re.sub(r"[^a-z0-9]", "", company.lower().strip())
        contacts = self._data.get("company_contacts", {})
        contact = contacts.get(key, {}).get(role)
        if not contact:
            return None
        if contact.get("bounced") and contact.get("bounce_count", 0) >= 2:
            log.info(f"Brain: contact {contact.get('email')} for {company} is bounced, skipping")
            return None
        return contact

    def mark_contact_bounced(self, company: str, role: str, email: str):
        """Mark a contact email as bounced."""
        key = re.sub(r"[^a-z0-9]", "", company.lower().strip())
        contacts = self._data.get("company_contacts", {})
        if key in contacts and role in contacts[key]:
            if contacts[key][role].get("email") == email:
                contacts[key][role]["bounced"] = True
                contacts[key][role]["bounce_count"] = contacts[key][role].get("bounce_count", 0) + 1
                contacts[key][role]["bounced_at"] = time.time()
                log.info(f"Brain: marked {email} as bounced for {company} [{role}]")
                self.save()

    def get_domain_correction(self, domain: str) -> str | None:
        """Return corrected domain if known, else None."""
        base = domain.split(".")[0].lower()
        corrections = self._data.get("domain_corrections", {})
        entry = corrections.get(base)
        if entry:
            return entry.get("correct")
        return None

    # ── Notifications ─────────────────────────────────────────────────────────


    def migrate_legacy_files(self):
        if self.data.get("_migration_v2_done"):
            return
        """
        One-time migration: read legacy .local/ JSON files into Brain.
        Runs at startup, idempotent — safe to call multiple times.
        Records completion so it never re-runs.
        """
        # Always re-sync pattern files — they update each run
        # Only skip full migration if already done AND pattern files haven't changed
        _already_done = self._data.get("_legacy_migration_done", False)
        import os as _os, json as _json, time as _t
        _local = _os.path.join(_os.path.dirname(_os.path.dirname(
            _os.path.abspath(__file__))), ".local")

        migrated = []

        # 1. domain_pattern_history.json → Brain domains
        _dph = _os.path.join(_local, "domain_pattern_history.json")
        if _os.path.exists(_dph):
            try:
                _d = _json.load(open(_dph))
                for domain, entry in _d.items():
                    pat = entry.get("confirmed_pattern")
                    fails = entry.get("failed_patterns", [])
                    if pat:
                        self.record_pattern_success(domain, pat, "migrated")
                    for fp in fails:
                        self.record_pattern_failure(domain, fp)
                migrated.append("domain_pattern_history.json")
            except Exception as e:
                log.debug(f"Migration domain_pattern_history failed: {e}")

        # 2. outreach_patterns.json → Brain domains (PatternCache file)
        _op = _os.path.join(_local, "outreach_patterns.json")
        if _os.path.exists(_op):
            try:
                _d = _json.load(open(_op))
                for domain, pat in _d.items():
                    if domain != "_global_best" and pat and not self.best_pattern_for(domain):
                        self.record_pattern_success(domain, pat, "migrated_patterns")
                migrated.append("outreach_patterns.json")
            except Exception as e:
                log.debug(f"Migration outreach_patterns failed: {e}")

        # 3. failed_patterns.json → Brain domain failures
        _fp = _os.path.join(_local, "failed_patterns.json")
        if _os.path.exists(_fp):
            try:
                _d = _json.load(open(_fp))
                for domain, fails in _d.items():
                    for f in fails:
                        if f and not f.startswith("{"):
                            # It's a local part, not a pattern template — skip
                            pass
                        elif f:
                            self.record_pattern_failure(domain, f)
                migrated.append("failed_patterns.json")
            except Exception as e:
                log.debug(f"Migration failed_patterns failed: {e}")

        # 4. mx_cache.json → Brain MX cache
        _mx = _os.path.join(_local, "mx_cache.json")
        if _os.path.exists(_mx):
            try:
                _d = _json.load(open(_mx))
                for domain, provider in _d.items():
                    if domain.startswith("mined_"):
                        # Website mining result
                        real_domain = domain[6:]
                        if provider:
                            self.record_pattern_success(real_domain, provider, "mined_migrated")
                    elif isinstance(provider, str) and provider:
                        if domain not in self._data["mx_cache"]:
                            self.set_mx(domain, provider != "other", provider if provider != "other" else "")
                migrated.append("mx_cache.json")
            except Exception as e:
                log.debug(f"Migration mx_cache failed: {e}")

        # 5. circuit_breaker.json → Brain circuit_breaker
        _cb = _os.path.join(_local, "circuit_breaker.json")
        if _os.path.exists(_cb):
            try:
                _d = _json.load(open(_cb))
                if _d.get("date") == _t.strftime("%Y-%m-%d"):
                    self._data["circuit_breaker"]["sent_today"] = _d.get("sent", 0)
                    self._data["circuit_breaker"]["bounced_today"] = _d.get("bounced", 0)
                    self._data["circuit_breaker"]["tripped"] = _d.get("tripped", False)
                migrated.append("circuit_breaker.json")
            except Exception as e:
                log.debug(f"Migration circuit_breaker failed: {e}")

        # 6. domain_cache.json → Brain company domains
        _dc = _os.path.join(_local, "domain_cache.json")
        if _os.path.exists(_dc):
            try:
                _d = _json.load(open(_dc))
                for company_key, entry in _d.items():
                    domains = entry.get("domains", []) if isinstance(entry, dict) else entry
                    if domains and isinstance(domains, list):
                        e = self._company_entry(company_key)
                        if not e.get("domain") and domains:
                            e["domain"] = domains[0]
                            e["domain_confidence"] = 0.7
                migrated.append("domain_cache.json")
            except Exception as e:
                log.debug(f"Migration domain_cache failed: {e}")

        if migrated:
            log.info(f"Brain: migrated legacy files: {migrated}")
            self._data["_migration_v2_done"] = True
            self.save()
        # Always re-sync outreach_patterns.json regardless of migration state
        import os as _os3, json as _json3
        _local3 = _os3.path.join(_os3.path.dirname(_os3.path.dirname(
            _os3.path.abspath(__file__))), ".local")
        _op3 = _os3.path.join(_local3, "outreach_patterns.json")
        if _os3.path.exists(_op3):
            try:
                _d3 = _json3.load(open(_op3))
                for _dom3, _pat3 in _d3.items():
                    if _dom3 != "_global_best" and _pat3:
                        if not self.best_pattern_for(_dom3):
                            self.record_pattern_success(_dom3, _pat3, "resync")
                log.debug(f"Brain: re-synced outreach_patterns.json ({len(_d3)} entries)")
            except Exception as _re3:
                log.debug(f"Pattern resync failed: {_re3}")

        self._data["_legacy_migration_done"] = True
        self._data["_legacy_migration_ts"] = _t.time()
        self._data["_legacy_migrated_files"] = migrated
        self.save()


    def get_blacklist_review(self) -> dict:
        """
        Generate weekly blacklist review.
        Returns:
          auto_approved: companies with structural reasons, 2+ rejections, no outreach
          pending_review: companies with 3+ same reason, may have mixed roles
          clean: nothing to report
        """
        auto_approved = []
        pending_review = []

        _STRUCTURAL = {"security clearance", "clearance required", "us person",
                       "citizenship required", "skillbridge", "military only"}

        for k, e in self._data["companies"].items():
            if e.get("blacklist"):
                continue  # Already blacklisted
            if e.get("outreach_count", 0) > 0:
                continue  # We've outreached here — never propose blacklist

            reasons = e.get("rejection_reasons", {})
            if not reasons:
                continue

            total = e.get("rejection_count", 0)
            if total < 2:
                continue

            top_reason = max(reasons, key=reasons.get) if reasons else ""
            top_count = reasons.get(top_reason, 0)
            name = e.get("name", k)

            # Check if structural
            is_structural = any(s in top_reason.lower() for s in _STRUCTURAL)

            # Count distinct job postings (velocity list has timestamps)
            vel = e.get("rejection_velocity", [])
            distinct_recent = len([t for t in vel if time.time() - t < 30 * 86400])

            if is_structural and top_count >= 2:
                auto_approved.append({
                    "name": name,
                    "reason": top_reason,
                    "count": top_count,
                    "distinct_jobs": distinct_recent,
                })
            elif top_count >= 3 and not is_structural:
                # Check if company also has valid CS roles (mixed company)
                has_mixed = top_count < total  # other rejection reasons exist too
                pending_review.append({
                    "name": name,
                    "reason": top_reason,
                    "count": top_count,
                    "total_rejections": total,
                    "has_mixed_roles": has_mixed,
                    "distinct_jobs": distinct_recent,
                    "outreach_count": e.get("outreach_count", 0),
                })

        return {
            "auto_approved": sorted(auto_approved, key=lambda x: -x["count"]),
            "pending_review": sorted(pending_review, key=lambda x: -x["count"]),
        }

    def apply_approved_blacklist(self, approved_names: list):
        """Apply auto-approved blacklist entries. Called after user confirms."""
        for name in approved_names:
            k = re.sub(r"[^a-z0-9]", "", name.lower().strip())
            e = self._data["companies"].get(k, {})
            if e:
                e["blacklist"] = True
                e["blacklist_reason"] = f"Approved: {e.get('_proposed_reason', 'structural')}"
                log.info(f"Brain: applied approved blacklist for {name}")
        self.save()

    def send_email_alert(self, subject: str, body: str):
        """Send alert email from kanade.pra@northeastern.edu to prasadckanade@gmail.com."""
        try:
            import msal, requests as _req

            _root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            _env = os.path.join(_root, ".env")
            cfg = {}
            if os.path.exists(_env):
                for ln in open(_env):
                    ln = ln.strip()
                    if "=" in ln and not ln.startswith("#"):
                        k, v = ln.split("=", 1)
                        cfg[k.strip()] = v.strip()
            client_id = cfg.get("MS_CLIENT_ID", "d3590ed6-52b3-4102-aeff-aad2292ab01c")
            authority = cfg.get(
                "MS_AUTHORITY", "https://login.microsoftonline.com/common"
            )
            scopes = ["https://graph.microsoft.com/Mail.Send"]
            token_file = os.path.join(_root, ".local", "ms_token.json")
            cache = msal.SerializableTokenCache()
            if os.path.exists(token_file):
                cache.deserialize(open(token_file).read())
            app = msal.PublicClientApplication(
                client_id, authority=authority, token_cache=cache
            )
            accts = app.get_accounts()
            result = (
                app.acquire_token_silent(scopes, account=accts[0]) if accts else None
            )
            if not result or "access_token" not in result:
                log.warning("Brain alert: MS token not available — skipping email")
                return
            if cache.has_state_changed:
                open(token_file, "w").write(cache.serialize())
            sender = cfg.get("MS_SENDER_EMAIL", "kanade.pra@northeastern.edu")
            payload = {
                "message": {
                    "subject": subject,
                    "body": {"contentType": "Text", "content": body},
                    "toRecipients": [
                        {"emailAddress": {"address": "prasadckanade@gmail.com"}}
                    ],
                    "from": {"emailAddress": {"address": sender}},
                },
                "saveToSentItems": "false",
            }
            resp = _req.post(
                f"https://graph.microsoft.com/v1.0/users/{sender}/sendMail",
                headers={
                    "Authorization": f"Bearer {result['access_token']}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=15,
            )
            if resp.status_code in (200, 202):
                log.info(f"Brain: alert sent — {subject}")
            else:
                log.warning(f"Brain: alert failed {resp.status_code}")
        except Exception as e:
            log.debug(f"Brain alert failed: {e}")
