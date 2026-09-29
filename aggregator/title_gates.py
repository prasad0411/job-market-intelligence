"""
Title gates derived from a manual read of 314 rows collected 25-26 Sep.

Each gate exists because something specific got through. They run before the
keyword scoring in TitleProcessor.is_cs_engineering_role, since scoring cannot
help: every title below contains a legitimately technical word.

  term          Graduation is May 2027, so an internship or co-op starting
                Summer or Fall 2027 is impossible. Spring and January 2027 are
                the final semester and do work. Full-time roles are untouched.

  business      "Solutions Engineer", "Customer Engineer", "Support Engineer",
                "Technical Recruiter", "Sales Development Representative".
                All carry engineer or technical wording. None write software.

  citizenship   "Software Engineer - C#/.NET (US Citizenship Required)" states
                the blocker in the title, so the description is not needed.

  hardware      Electrical design, silicon validation, IC design, photonics,
                nuclear, thermal-hydraulics, quality and reliability
                engineering - real engineering, not software.

Rescue lists are deliberately narrow. A bare \\bengineer\\b would rescue every
entry in every list, which is exactly how these reached the sheet.
"""
import re

# ── term ─────────────────────────────────────────────────────────────────────

_INTERNISH = re.compile(r"\b(intern|internship|co-?op|summer\s+analyst)\b", re.I)

_BAD_TERM = re.compile(
    r"\b(summer|fall|autumn)\s*(?:20)?2[7-9]\b"       # Summer 2027, Fall 28
    r"|\b(?:20)?2[7-9]\s*(summer|fall|autumn)\b"      # 2027 Summer
    r"|\bsummer\s+intern",                            # Summer Intern, no year
    re.I)

# An explicit Spring or January overrides, so "Spring 2027 Summer Analyst
# Program" is kept rather than guessed at.
_GOOD_TERM = re.compile(
    r"\b(spring|january|jan|winter)\s*(?:20)?2[6-9]\b"
    r"|\b(?:20)?2[6-9]\s*(spring|january|winter)\b",
    re.I)


def is_wrong_term(title):
    """True for an internship or co-op in a term that starts after graduation.

    Only explicit terms are rejected. Most postings name no season at all, and
    guessing would cost real jobs.
    """
    t = title or ""
    # The intern check comes first, and it is not an optimisation.
    #
    # Graduation is May 2027, so a FULL-TIME role starting Summer 2027 is
    # correct: it begins after the degree finishes. Only an INTERNSHIP in
    # that term is impossible, because the candidate is no longer enrolled.
    #
    # A previous version dropped this guard and rejected on a dated season
    # alone, which threw out every full-time posting advertising a summer
    # start date.
    if not _INTERNISH.search(t):
        return False
    if _GOOD_TERM.search(t):
        return False              # an explicit Spring or January wins
    return bool(_BAD_TERM.search(t))


# ── business, sales, support ─────────────────────────────────────────────────

_BIZ = (
    "sales development representative", "sales representative",
    "account executive", "account manager", "business development",
    "technical recruiter", "recruiter", "talent acquisition",
    "solutions engineer", "solution engineer", "partner solutions",
    "solutions architect", "solution architecture",
    "customer engineer", "customer success", "customer solutions",
    "support engineer", "technical support", "help desk", "service desk",
    "investment banking", "financial reporting", "wealth management",
    "enterprise solutions specialist", "assurance intern",
    "claim professional", "socioeconomic", "actuary",
    "presales", "pre-sales", "field sales", "inside sales",
)

_BIZ_RESCUE = (
    r"\bsoftware\s+engineer", r"\bsoftware\s+develop", r"\bsde\b", r"\bswe\b",
    r"\bbackend\b", r"\bfront[\s-]?end\b", r"\bfull[\s-]?stack\b",
    r"\bdevops\b", r"\bdata\s+engineer", r"\bdata\s+scien",
    r"\bmachine\s+learning\b", r"\bml\s+engineer", r"\bsite\s+reliability\b",
    r"\bcompiler\b", r"\bembedded\b", r"\bfirmware\b",
    # "Talent Acquisition AI Product Engineering Intern" is an AI engineering
    # role; "Talent Acquisition" is the name of the university programme.
    # "Customer Solutions, AI Enabled Insights" is the same shape.
    r"(?<![a-z])ai(?![a-z])", r"\bartificial\s+intelligence\b",
    r"\bgenai\b", r"\bproduct\s+engineering\b",
)

_BIZ_RE = [re.compile(r"(?<![a-z])%s" % re.escape(w), re.I) for w in _BIZ]
_BIZ_RESCUE_RE = [re.compile(p, re.I) for p in _BIZ_RESCUE]


def is_business_role(title):
    """True for sales, support, recruiting and client-facing 'engineer' roles."""
    t = (title or "").strip()
    if not t:
        return False
    if any(r.search(t) for r in _BIZ_RESCUE_RE):
        return False
    return any(b.search(t) for b in _BIZ_RE)


# ── citizenship or clearance stated in the title ─────────────────────────────

_CITIZEN = re.compile(
    r"\b(?:us|u\.s\.?|united\s+states)\s+citizen(?:ship)?\s*(?:required|only)?"
    r"|\bcitizenship\s+required"
    r"|\bmust\s+be\s+a?\s*(?:us|u\.s\.?)\s+citizen"
    r"|\bsecurity\s+clearance\b|\bactive\s+clearance\b|\bts/sci\b"
    r"|\bpublic\s+trust\b",
    re.I)


def needs_citizenship(title):
    """True when the title itself states a citizenship or clearance bar."""
    return bool(_CITIZEN.search(title or ""))


# ── hardware disciplines ─────────────────────────────────────────────────────

# Specific enough that no rescue applies. "Silicon Validation Engineer Intern
# - AI Hardware" was being rescued by the bare "ai" pattern, but AI hardware
# validation is still hardware validation.
# "photonic" moved out of the no-rescue tier: "Photonics Data Engineer
# Intern - Data Management" is a data engineering role at a photonics
# company, and the no-rescue tier was dropping it.
_HW_NO_RESCUE = (
    "silicon validation", "ic design", "integrated circuit",
    "nuclear", "thermal-hydraulic", "thermal - hydraulic", "post-fab",
    "post - fab", "mixed signal", "braid design", "hip/knee", "metallurg",
    "welding", "piping", "hvac",
)

# Rescuable by a software or AI signal.
_HW_SOFT = (
    "photonic",
    "electrical design", "electrical systems", "electronics engineer",
    "electronics/electrical", "commissioning", "civil/environmental",
    "environmental engineer", "quality engineer", "qms",
    "reliability engineer", "quality assurance engineer",
    "mechanical design", "optics engineer",
)

_HW_RESCUE = (
    r"\bsoftware\b", r"\bsde\b", r"\bswe\b", r"\bbackend\b",
    r"\bfront[\s-]?end\b", r"\bfull[\s-]?stack\b", r"\bdevops\b",
    r"\bdata\s+engineer", r"\bdata\s+scien", r"\bmachine\s+learning\b",
    r"\bsite\s+reliability\b", r"\bsre\b", r"\bembedded\s+software\b",
    r"\bfirmware\b", r"\bcompiler\b", r"(?<![a-z])ai(?![a-z])", r"\bml\b",
    r"\bqa\s+engineer", r"\bsdet\b",
)

_HW_NR_RE = [re.compile(r"(?<![a-z])%s" % re.escape(w), re.I)
             for w in _HW_NO_RESCUE]
_HW_SOFT_RE = [re.compile(r"(?<![a-z])%s" % re.escape(w), re.I)
               for w in _HW_SOFT]
_HW_RESCUE_RE = [re.compile(p, re.I) for p in _HW_RESCUE]


def is_hardware_discipline_v2(title):
    """Hardware engineering beyond what the first discipline list caught."""
    t = (title or "").strip()
    if not t:
        return False
    if any(n.search(t) for n in _HW_NR_RE):
        return True
    if any(r.search(t) for r in _HW_RESCUE_RE):
        return False
    return any(s.search(t) for s in _HW_SOFT_RE)


# ── one entry point ──────────────────────────────────────────────────────────

def rejection_reason(title):
    """Why this title is out, or None to let it continue to the scoring."""
    if needs_citizenship(title):
        return "citizenship or clearance required"
    if is_wrong_term(title):
        return "term starts after graduation"
    if is_business_role(title):
        return "sales, support or recruiting role"
    if is_hardware_discipline_v2(title):
        return "hardware engineering discipline"
    return None
