"""
Term filter — drop Summer 2027 internships, never anything else.

Design priority: NEVER lose a full-time role, a Fall 2026, a Spring 2027 or
a Winter 2027 posting. Letting some summer internships through is acceptable
(they get skipped by hand); dropping a wanted role is not.

Five rules, in order:
  1. Full-time is untouchable — returns keep before any term logic runs.
  2. Allowlist wins — any Fall/Spring/Winter/Off-Season signal = keep,
     immediately, even if "Summer" also appears (postings list multiple terms).
  3. Drop only on an UNAMBIGUOUS summer phrase ("Summer 2027", "2027 Summer").
     Never on a bare year, never on a bare "Summer".
  4. Ambiguous = keep. No term signal is not evidence of summer.
  5. Every drop is logged, not silently discarded, so the decision is auditable.
"""
import logging
import re

log = logging.getLogger(__name__)

# Rule 2 — if ANY of these appear, the job is kept no matter what else it says.
_KEEP_TERMS = re.compile(
    r"\b("
    r"fall|autumn|"
    r"spring|"
    r"winter|"
    r"off[\s-]?season|"
    r"co[\s-]?op|coop|"          # co-ops are usually Fall/Spring
    r"year[\s-]?round|"
    r"january|jan\s+20\d\d|"
    r"september|sept?\s+20\d\d|"
    r"october|november|december"
    r")\b",
    re.I,
)

# Rule 3 — only these explicit phrasings count as "definitely summer".
# A bare "2027" or a bare "Summer" is NOT enough.
_SUMMER_PATTERNS = [
    re.compile(r"\bsummer\s*'?\s*20?27\b", re.I),     # Summer 2027 / Summer '27
    re.compile(r"\b20?27\s*summer\b", re.I),          # 2027 Summer
    re.compile(r"\bsummer\s+analyst\s+20?27\b", re.I),
    re.compile(r"\bsummer\s+intern(ship)?\s+20?27\b", re.I),
    re.compile(r"\b20?27\s+summer\s+intern", re.I),
    # Year and "summer" separated by other words, either order:
    #   "2027 Software Engineering Summer Internship"  <- Advanced Space
    #   "Summer Software Engineering Internship 2027"
    # Capped at 4 intervening words so an unrelated year elsewhere in a long
    # title cannot pair with an unrelated "summer". Rule 2 still runs first,
    # so anything mentioning Fall/Spring/Winter/co-op is kept regardless.
    re.compile(r"\b20?27\b(?:\s+\w+){0,4}\s+\bsummer\b", re.I),
    re.compile(r"\bsummer\b(?:\s+\w+){0,4}\s+\b20?27\b", re.I),
]

_INTERN_TYPES = {"internship", "intern", "co-op", "coop"}


def should_drop_summer(title, job_type="", company="", source="", extra_text=""):
    """True only when this is unambiguously a Summer 2027 internship.

    Returns False (keep) for full-time, for anything with a Fall/Spring/Winter
    signal, and for anything ambiguous.
    """
    # Rule 1: full-time never enters the filter
    if (job_type or "").strip().lower() not in _INTERN_TYPES:
        return False

    haystack = " ".join(str(x) for x in (title, extra_text) if x)
    if not haystack.strip():
        return False

    # Rule 2: allowlist wins outright
    if _KEEP_TERMS.search(haystack):
        return False

    # Rule 3: explicit summer phrasing only
    for pat in _SUMMER_PATTERNS:
        if pat.search(haystack):
            # Rule 5: auditable
            log.info(
                "SUMMER DROP | %s | %s | %s | matched=%s",
                company or "?", str(title)[:60], source or "?", pat.pattern
            )
            return True

    # Rule 4: ambiguous stays
    return False
