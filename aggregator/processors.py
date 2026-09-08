#!/usr/bin/env python3

import re
import json
import logging
from functools import lru_cache

from aggregator.config import (
    CANADA_PROVINCES,
    CANADA_PROVINCE_NAMES,
    MAJOR_CANADIAN_CITIES,
    CANADIAN_COMPANIES,
    PLATFORM_CONFIGS,
    JOB_ID_PATTERNS,
    LOCATION_SELECTORS,
    LOCATION_METADATA_PATTERNS,
    HTML_ARTIFACT_PATTERNS,
    INVALID_LOCATION_KEYWORDS,
    DEPARTMENT_KEYWORDS,
    WORKDAY_HQ_CODES,
    URL_TO_COMPANY_MAPPING,
    COMPANY_SLUG_MAPPING,
    COMPANY_PLACEHOLDERS,
    COMPANY_NAME_PREFIXES,
    COMPANY_NAME_STOPWORDS,
    MIN_CONFIDENCE_JOB_ID,
    MIN_CONFIDENCE_LOCATION,
    MIN_CONFIDENCE_COMPANY,
    get_state_for_city,
    validate_us_state_code,
    normalize_unicode,
    extract_domain_and_subdomain,
    FULL_STATE_NAMES,
    US_STATES_FALLBACK,
    CITY_ABBREVIATIONS,
    LOCATION_SUFFIXES,
    AMBIGUOUS_CITIES,
    US_CONTEXT_KEYWORDS,
    CANADA_CONTEXT_KEYWORDS,
    TECHNICAL_ROLE_KEYWORDS,
    NON_TECHNICAL_PURE,
    SPONSORSHIP_REJECT_PATTERNS,
    BLACKLIST_DOMAINS,
    MAX_REASONABLE_AGE_DAYS,
)

from aggregator.utils import (
    ExtractionResult,
    ExtractionVoter,
    CompanyNormalizer,
    CompanyValidator,
    PlatformDetector,
)

# ============================================================================
# Compiled Patterns (Performance Optimization)
# ============================================================================

_COMPILED_JOB_ID_PATTERNS = [(re.compile(p, re.I), c) for p, c in JOB_ID_PATTERNS]
_COMPILED_URL_COMPANY_PATTERNS = [
    (re.compile(p, re.I), c) for p, c in URL_TO_COMPANY_MAPPING.items()
]
_COMPILED_METADATA_PATTERNS = [re.compile(p, re.I) for p in LOCATION_METADATA_PATTERNS]
_COMPILED_ARTIFACT_PATTERNS = [re.compile(p) for p in HTML_ARTIFACT_PATTERNS]
_COMPILED_SPONSORSHIP_PATTERNS = [
    re.compile(p, re.I) for p in SPONSORSHIP_REJECT_PATTERNS
]

_TITLE_CLEAN_PATTERN = re.compile(r"\s*[\(\[].+?[\)\]]|\s*\([^)]*$|\s*\[[^\]]*$")
_SEASON_PATTERN = re.compile(r",?\s*-?\s*(?:Summer|Fall|Spring|Winter)(?:\s*/\s*(?:Summer|Fall|Spring|Winter))?\s*20\d{2}", re.I)
_YEAR_PATTERN = re.compile(r"\s*-?\s*20\d{2}\s*-?\s*")
_DEGREE_PATTERN = re.compile(
    r"\s*[\(\[]\s*(BS/MS|MS|PhD|Bachelor|Master).*?[\)\]]", re.I
)
_CITY_STATE_PATTERN = re.compile(r"([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*),?\s*([A-Z]{2})\b")
_LOCATION_LABEL_PATTERN = re.compile(
    r"Location\s*:?\s*([A-Za-z\s,]+(?:,\s*(?:[A-Z]{2}|[A-Za-z]{4,15}))?)", re.I
)
_LOCATION_NO_SPACE_PATTERN = re.compile(
    r"Location:([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*,\s*[A-Z][a-z]+)", re.I
)


def log_detailed_rejection(
    company,
    title,
    reason,
    pattern=None,
    matched_text=None,
    context=None,
    url=None,
    debug_info=None,
):
    base_msg = f"REJECTED | {company} | {reason} | Title: '{title}'"

    details = []

    if pattern:
        details.append(f"Pattern: {pattern}")

    if matched_text:
        clean_match = matched_text.strip()[:150]
        details.append(f"Match: '{clean_match}'")

    if context:
        clean_context = context.strip()[:250]
        details.append(f"Context: '...{clean_context}...'")

    if debug_info:
        details.append(f"Debug: {debug_info}")

    if url:
        details.append(f"URL: {url[:80]}")

    if details:
        full_msg = base_msg + " | " + " | ".join(details)
    else:
        full_msg = base_msg

    logging.info(full_msg)


# ============================================================================
# Title Processing - ORIGINAL
# ============================================================================


class TitleProcessor:
    @staticmethod
    @lru_cache(maxsize=512)
    def clean_title_aggressive(title):
        title = re.sub(r"^[:\s]+", "", title).strip()  # strip leading colon/space
        # collapse spaced compounds so prefix logic cannot eat them
        title = re.sub(r"\b(Full|Back|Front|End|Co|Part)\s+-\s+(Stack|End|Front|Op|Time|Site)\b", r"\1-\2", title, flags=re.I)
        # Strip known garbage prefixes: "Pipeline RR:", "XMLNAME-", etc.
        title = re.sub(r"^Pipeline\s+\w+:\s*", "", title, flags=re.I).strip()
        title = re.sub(r"^XMLNAME[-\s]*", "", title, flags=re.I).strip()
        # Strip garbage suffixes: "Start Date", "Apply Now", etc.
        title = re.sub(r"\s+Start Date\s*$", "", title, flags=re.I).strip()
        title = re.sub(r"\s+Apply Now\s*$", "", title, flags=re.I).strip()
        # Clean pipe-separated metadata: "Group 2-24 | Co-Op | Title | Jun-Dec"
        if " | " in title:
            segments = [s.strip() for s in title.split(" | ")]
            # Find the segment that looks most like a job title
            _job_words = {"intern", "engineer", "developer", "analyst", "scientist",
                          "co-op", "coop", "co op", "manager", "designer", "researcher"}
            _best = []
            for seg in segments:
                seg_lower = seg.lower()
                # Skip group/team codes: "Group 2-24", short codes
                if re.match(r"^(Group|Team|Dept|Division)\s", seg, re.I):
                    continue
                # Skip date ranges: "Jun-Dec", "Jan-Jun 2026"
                if re.match(r"^(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)", seg, re.I) and len(seg) < 20:
                    continue
                _best.append(seg)
            if _best:
                title = " | ".join(_best) if len(_best) <= 2 else _best[0]
        # Reject garbage titles that are just URL paths or button text
        _GARBAGE_TITLES = {"application", "apply", "apply now", "job", "careers",
            "sign in", "login", "home", "search", "page not found", "404",
            "let's confirm you are human", "your connection was interrupted",
            "career page", "job listings", "open positions"}
        if title.lower().strip() in _GARBAGE_TITLES:
            # Return "" (not a tuple): callers treat empty as invalid.
            return ""
        if not title or len(title) < 5:
            return title

        original = title

        # Strip company name prefix from title: "Company - Title" or "Company: Title"
        import re as _prefix_re
        _prefix_match = _prefix_re.match(r"^[A-Z][A-Za-z\s&.,']{2,30}(?:\s+[-–—]\s+|:\s*)(.+)$", title)
        if _prefix_match:
            _remainder = _prefix_match.group(1).strip()
            # Only strip if remainder looks like a job title (has job words)
            _job_words = {"intern", "engineer", "developer", "analyst", "scientist", "manager",
                          "designer", "architect", "specialist", "associate", "coordinator"}
            if any(w in _remainder.lower() for w in _job_words):
                title = _remainder

        # Strip duration suffixes: "- 4 months", "- 12 weeks", "(8 months)"
        import re as _dur_re
        title = _dur_re.sub(r'\s*[-–—]\s*\d+\s*(?:months?|weeks?|mos?)\s*$', '', title, flags=_dur_re.I).strip()
        title = _dur_re.sub(r'\s*\(\d+\s*(?:months?|weeks?|mos?)\)\s*$', '', title, flags=_dur_re.I).strip()
        # Strip "Fall 2026" / "Summer 2026" season suffixes
        title = _dur_re.sub(r'\s*[-–—]\s*(?:Fall|Spring|Summer|Winter)\s+20\d{2}\s*$', '', title, flags=_dur_re.I).strip()
        # Strip schedule/duration info: "6 Months Full-time - July to Dec", "Full-time - Aug to Dec"
        title = _dur_re.sub(r'\s*[-–—,]\s*\d+\s*(?:months?|weeks?)\s*(?:full[- ]?time|part[- ]?time)?\s*[-–—]?\s*(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\w*\s*(?:to|through|-|–)\s*(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\w*(?:\s*20\d{2})?\s*$', '', title, flags=_dur_re.I)
        # Strip "Full-time - July to Dec 2026" without leading number
        title = _dur_re.sub(r'\s*[-–—,]\s*(?:full[- ]?time|part[- ]?time)\s*[-–—]?\s*(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\w*\s*(?:to|through|-|–)\s*(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\w*(?:\s*20\d{2})?\s*$', '', title, flags=_dur_re.I)
        # Strip standalone "- Full Time", "- Part Time"
        title = _dur_re.sub(r'\s*[-–—]\s*(?:full[- ]?time|part[- ]?time)\s*$', '', title, flags=_dur_re.I)
        # Strip "- College Program 2026", "- College Program"
        title = _dur_re.sub(r'\s*[-–—]\s*College\s+Program(?:\s+20\d{2})?\s*$', '', title, flags=_dur_re.I)
        # Strip redundant title suffix: "AI and Governance Co-op - AI and Governance"
        # Pattern: "X - X" where second half repeats first half
        _halves = _dur_re.split(r'\s*[-–—]\s*', title)
        if len(_halves) == 2:
            _h1 = _halves[0].strip().lower()
            _h2 = _halves[1].strip().lower()
            # If second half is subset of first half, remove it
            if _h2 in _h1 or _h1.endswith(_h2):
                title = _halves[0].strip()
        # Strip trailing garbage words: "Intern Start", "Apply Now"
        title = _dur_re.sub(r'\s+(?:Start|Apply|Now|Click|Here)\s*$', '', title, flags=_dur_re.I).strip()

        # FIX 1: Strip email subject line prefixes like "Company is looking for X"
        import re as _re_title
        title = _re_title.sub(
            r"^.{3,60}?\s+is\s+(?:looking|hiring|searching|seeking)\s+for\s+",
            "", title, flags=re.I
        ).strip()
        # Strip "We are looking for X" style
        title = _re_title.sub(
            r"^(?:we\s+are|we're)\s+(?:looking|hiring|searching|seeking)\s+for\s+",
            "", title, flags=re.I
        ).strip()
        # Strip leading ATS codes like "F1138 " at start of title
        title = _re_title.sub(r"^[A-Z]\d{3,5}\s+", "", title).strip()

        title = _TITLE_CLEAN_PATTERN.sub("", title)
        title = _SEASON_PATTERN.sub("", title)
        title = _YEAR_PATTERN.sub(" ", title)
        title = _DEGREE_PATTERN.sub("", title)
        title = re.sub(r"\s+", " ", title).strip().strip("-")

        # Strip Simplify suffixes and trailing punctuation
        title = re.sub(r"\s*@\s*.+?\s*\|\s*Simplify.*", "", title, flags=re.I)
        title = re.sub(r"\s*\|\s*Simplify.*", "", title, flags=re.I)
        title = title.rstrip(",.;- ")

        # Fix encoding artifacts
        try:
            from aggregator.config import TITLE_ENCODING_FIXES
            for bad, good in TITLE_ENCODING_FIXES.items():
                title = title.replace(bad, good)
        except Exception:
            pass
        # Dedup: "Data Analytics Intern - Data Analytics" -> "Data Analytics Intern"
        import re as _re2
        _dd = _re2.compile(r'^(.+?)\s*[-\u2013\u2014]\s*\1\s*$', _re2.I)
        _m = _dd.match(title.strip())
        if _m:
            title = _m.group(1).strip()
        return title if len(title) >= 5 else original

    @staticmethod
    def is_university_restricted(company, title):
        if not company or not title:
            return False
        cl, tl = company.lower(), title.lower()
        uni_words = ["university", "college", "rector & visitors", "regents of"]
        if any(u in cl for u in uni_words) and "undergraduate" in tl:
            return True
        return False

    @staticmethod
    def is_valid_job_title(title):
        # Reject garbage titles that are just URL paths or button text
        _GARBAGE_TITLES = {"application", "apply", "apply now", "job", "careers",
            "sign in", "login", "home", "search", "page not found", "404",
            "let's confirm you are human", "your connection was interrupted",
            "career page", "job listings", "open positions"}
        if title.lower().strip() in _GARBAGE_TITLES:
            return False, f"Garbage title: {title}"
        if not title or len(title) < 5:
            return False, "Title too short"

        title_lower = title.lower()

        try:
            from aggregator.config import INVALID_TITLE_KEYWORDS
        except (ImportError, AttributeError):
            INVALID_TITLE_KEYWORDS = []

        # Reject standalone generic titles like "Internship", "Co-op", "Intern"
        _standalone_garbage = {"internship", "intern", "co-op", "coop", "fellowship",
            "apprenticeship", "job", "position", "role", "opening", "opportunity"}
        if title.strip().lower() in _standalone_garbage:
            return False, "Standalone generic title"

        # Reject clearly non-tech titles
        _NON_TECH = [
            r"\b(?:buyer|purchasing|procurement)\b",
            r"\b(?:metrology|metallurg|welder|machinist)\b",
            r"\b(?:avionics|pilot|flight)\b(?!.*software)",
            r"\b(?:3d\s+model|visual\s+design|graphic\s+design)\b(?!.*engineer|.*develop)",
            r"\b(?:nurse|physician|pharmacist|dental)\b",
            r"\b(?:accountant|auditor|tax\s+analyst|bookkeeper)\b",
            r"\b(?:real\s+estate|property\s+manag|leasing)\b",
            r"\bgroup\s+head\b(?!.*engineer|.*software|.*data)",
            r"\bilt\s+specialist\b",
            r"\bcabinet\s+maker\b",
            r"\bpayroll\b",
            r"\berp\s+supervisor\b",
            r"\bmanager\s+people\b",
            r"\belectronic\s+systems\s+engineering\b(?!.*software)",
            r"\bit\s+apps\s+designer\b",
            r"\bsales\s+&\s+data\s+analytics\b",
            r"\bcandidateexperience\b",
            r"\bdata\s+coordinator\b(?!.*engineer|.*develop|.*software)",
            r"\bfield\s+sales\b",
            r"\bphysics[\s-]+based\s+model",
            r"\bmechatronics\b",
            r"\bshipyard\b",
            r"\bbusiness\s+development\b",
            r"\bpartnerships?\s+(?:&|and)\s+growth\b",
            r"\bproduct\s+manager\b(?!.*engineer)",
            r"\binstructor\b",
            r"\bteaching\b",
            r"\bteacher\b",
            r"\btransit\s+intern\b",
            r"\bcommunity\s+college\b",
            r"\bschool\s+district\b",
            r"\bcounty\s+of\b",
            r"\bgenerator\s+(?:application|design)\b",
            r"\bpeople\s+operations\b",
            r"\baircraft\s+(?:technician|mechanic|painter|repair|composite)\b",
            r"\binterior\s+installation\b",
            r"\bstructural\s+mechanic\b",
            r"\bgas\s+compressor\b",
            r"\bsystem\s+product\s+engineer\b(?!.*software)",
            r"\bproduct\s+engineer\s+(?:1|I|i)\b(?!.*software)",
            r"\bpower\s+(?:generation|plant|systems)\s+(?:engineer|intern)\b",
            r"\bgovernment\s+(?:intern|jobs)\b",
            r"\bventure\s+capital\b",
            r"\bstaffing\b",
            r"\brecruitment\b(?!.*engineer|.*software)",
            r"\b(?:electrolysis|membrane|proton exchange|catalyst|polymer)\b(?!.*software|.*data)",
            r"\b(?:hamr|component\s+testing|pcb|soldering|antenna)\b(?!.*software)",
            r"\b(?:air\s+quality|climate\s+action|conservation|sustainability)\b(?!.*data|.*ml|.*ai)",
            r"\b(?:travel\s+product|digital\s+travel|merchandise\s+strategy)\b",
            r"\b(?:product\s+management)\b(?!.*software|.*data|.*tech)",
        ]
        for _ntp in _NON_TECH:
            if re.search(_ntp, title, re.I):
                return False, f"Non-tech title"

        # Reject university-specific co-ops (restricted to that university's students)
        _UNIV_SPECIFIC = [
            r"\bdrexel\s+university\b",
            r"\bnortheastern\s+university\b(?!.*prasad)",  # Your own university is fine
            r"\bpurdue\s+university\b",
            r"\bgeorgia\s+tech\b",
            r"\bstanford\b.*\bco-?op\b",
            r"\bmit\b.*\bco-?op\b",
        ]
        for _up in _UNIV_SPECIFIC:
            if re.search(_up, title, re.I):
                return False, "University-specific co-op"

        # Reject senior-level scientist/engineer roles (II, III, IV = not entry level)
        # II is typically 1-3 years and still early career - Shield AI, Match
        # Group and Pivotal "Software Engineer II" roles were all rejected by
        # this. III and above genuinely is not entry level.
        # Relaxing II let non-software "<X> Engineer II" titles through -
        # Project Controls, Drafter/Design, Validation. A level-II title is
        # only worth keeping when the role itself is software.
        if re.search(r"(?:scientist|engineer|developer|analyst)\s+(?:II)\b", title, re.I):
            if not re.search(
                r"\b(?:software|swe|developer|programmer|full[\s-]?stack|backend|"
                r"back[\s-]end|frontend|front[\s-]end|devops|sre|security|"
                r"machine\s+learning|deep\s+learning|computer\s+vision|"
                r"data\s+engineer|systems?|cloud|platform|autonomy|perception)\b",
                title, re.I):
                return False, f"Level II non-software role: {title[:30]}"
        if re.search(r"(?:scientist|engineer|developer|analyst)\s+(?:III|IV|V)\b", title, re.I):
            if not re.search(r"\bintern\b|\bco-?op\b|\bnew\s+grad\b|\bentry\b|\bjunior\b", title, re.I):
                return False, f"Senior role (Roman numeral level): {title[:30]}"

        # Reject education institutions
        _EDU_COMPANIES = ["community college", "school district", "university system",
            "board of education", "public schools"]

        # Reject non-English titles
        _NON_ENGLISH_KW = ["automatizare", "inteligenta artificiala", "dezvoltare",
            "platforme", "bazata", "senzori", "inginerie", "testare",
            "praktikum", "werkstudent", "alternance", "stagiaire",
            "ingeniero", "entwicklung", "forschung"]
        if any(kw in title.lower() for kw in _NON_ENGLISH_KW):
            return False, "Non-English title"

        # Reject standalone generic titles
        _standalone_garbage = {"internship", "intern", "co-op", "coop", "fellowship",
            "apprenticeship", "job", "position", "role", "opening", "opportunity"}
        if title.strip().lower() in _standalone_garbage:
            return False, "Standalone generic title"

        # Reject XMLNAME garbage from Workday
        if "xmlname" in title.lower():
            return False, "XMLNAME garbage title"
        # Reject hex/alphanumeric job IDs used as titles (e.g. "F70F3473E7")
        if re.match(r'^[0-9a-fA-F]{6,}$', title.strip()):
            return False, "Title is hex/job ID"
        _alpha_only = re.sub(r'[^a-zA-Z ]', '', title).strip()
        if len(_alpha_only) < 4:
            return False, "Title too short/no alpha content"

        # Some INVALID_TITLE_KEYWORDS patterns are broader than intended and
        # catch genuine software roles: "robotics\s+(engineer|software...)"
        # rejected Robotics Software Engineer, "test\s+engineering.*intern"
        # rejected Software Test Engineering Intern, and "computer\s+vision.*
        # intern" rejected Computer Vision Engineering Intern. All three are
        # software jobs. A SOFTWARE keyword overrides these - but never the
        # hard exclusions below (PhD, clearance, pure hardware).
        # PhD anywhere in a TITLE, unless the title also offers MS or BS.
        # Existing patterns only covered "phd intern" / "phd internship", so
        # "2027 PhD Graduate", "Research Scientist, PhD" and "Software
        # Engineer, PhD New Grad" all passed. An MS/PhD or BS/MS/PhD title is
        # open to you, so those are kept. 14/14 cases.
        # Postdoc is a PhD role but contains no "phd" substring, so
        # "Machine Learning - Postdoctoral Research" passed every check.
        if re.search(r"\b(?:ph\.?\s?d|post[\s-]?doc(?:toral)?|doctoral|dphil)\b",
                     title_lower):
            if not re.search(
                r"\b(?:ms|m\.s\.|masters?|bs|b\.s\.|bachelors?)\b", title_lower):
                return False, "PhD role (not eligible)"

        _SOFT_OVERRIDABLE = (
            r"robotics\s+(?:engineer|software|hardware|intern|co-op)",
            r"test\s+engineering.*intern",
            r"computer\s+vision.*intern",
        )
        _title_is_software = re.search(
            r"\b(?:software|swe|developer|programmer|full[\s-]?stack|backend|"
            r"back[\s-]end|frontend|front[\s-]end|devops|sre|machine\s+learning|"
            r"computer\s+vision|data\s+engineer|perception)\b", title_lower)

        # A title offering MS *or* PhD is one you can apply to. Several
        # patterns here fire on any intern title mentioning PhD - notably
        # 'intern.*\bphd\b' - which rejected "Research Intern - MS or PhD".
        # Third place today needing the same carve-out, so it lives here at
        # the shared gate rather than being repeated per pattern.
        _ms_eligible = re.search(
            r"\b(?:ms|m\.s\.|masters?|bs|b\.s\.|bachelors?)\b", title_lower)

        for pattern in INVALID_TITLE_KEYWORDS:
            if re.search(pattern, title_lower):
                if _title_is_software and pattern in _SOFT_OVERRIDABLE:
                    continue
                if _ms_eligible and "phd" in pattern:
                    continue
                return False, "PhD, military, hardware or non-CS role (not eligible)"

        # FIX 4: SkillBridge / DoD / military-only internships
        skillbridge_patterns = [
            r"skillbridge",
            r"skill\s*bridge",
            r"dod\s+skillbridge",
            r"military\s+(?:intern|transition|separation)",
            r"(?:active\s+duty|armed\s+forces)\s+(?:intern|transition)",
            r"separation\s+from\s+(?:the\s+)?military",
            r"within\s+180\s+days\s+of\s+separation",
            r"honorable\s+discharge\s+required",
        ]
        for pat in skillbridge_patterns:
            if re.search(pat, title_lower):
                return False, "SkillBridge/military-only internship (not eligible)"

        # Hardware/non-CS roles to reject
        hardware_patterns = [
            r"\bhardware\s+(?:design|engineer|test|validation|systems test)",
            r"\bfirmware",
            r"\bembedded",
            r"\bmanufacturing",
            r"\bsensor",
            r"\biot",
            r"\basic",
            r"\bfpga",
            r"\bvlsi",
            r"\bpcb",
            r"\bsoldering",
            r"\bschematic",
            r"\bcircuit\s+design",
            r"\bpower\s+electronics",
            r"\bmotor\s+controls",
            # Relaxing the broad "robotics" rule let Robotics Technician
            # through. Technician/operator/assembler/machinist are trade
            # roles regardless of the domain in front of them.
            r"\btechnician\b",
            r"\boperator\b",
            r"\bassembler\b",
            r"\bmachinist\b",
            r"\bfabricator\b",
            r"\brf\s+engineer",
            r"\bantenna",
            r"\bsignal\s+integrity",
            r"\bboard\s+design",
            r"\belectrical\s+engineer",
            r"\bmechanical\s+engineer",
            r"\bcnc",
            r"\bmachining",
            r"\bwelding",
            r"\bhvac",
            r"\bplumbing",
            r"\boptical\s+engineer",
            r"\bcorrosion",
            r"\btechnician\s+apprentice",
            r"\bbody\s+apprentice",
            r"\bcongressional\s+research",
            r"\btechnical\s+services\s+intern",
            r"\bmaterials\s+technology",
            r"\bnuclear\s+operations",
            r"\baudit\s+technology\s+risk",
            r"\bfield\s+service\s+engineer",
            r"\bsystems\s+test\s+intern",
            r"\baudio\s+digital\s+signal",
            r"\bsales\s+(?:research|analyst|operations|support)",
            r"\bcustomer\s+(?:data\s+research|service)\s+intern",
            r"\binventory\s+(?:data\s+)?analyst",
            r"\bsupply\s+chain\s+(?:data|analyst)",
            r"\bhr\s+(?:analytics|operations)\s+intern",
            r"\brecruiting\s+(?:coordinator|intern)",
            r"\bclinical\s+(?:data|trial)\s+intern",
            r"\breal\s+estate\s+(?:analyst|intern)",
        ]
        # Non-tech DOMAINS. "DCL Environmental Affairs Intern" passed every
        # filter because nothing knew Environmental Affairs is not technical.
        # A software or data keyword overrides, so "Environmental
        # Sustainability Data Analytics Intern" and "Sustainability Data
        # Engineer" are kept - the domain is not the disqualifier, the
        # absence of a technical role is. 19/19 cases.
        _NONTECH_DOMAIN = (
            r"\benvironmental\s+(?:affairs|sustainability|health|safety|"
            r"compliance|policy|science)\b", r"\bsustainability\b",
            r"\benvironmental\s+intern\b", r"\bconservation\b",
            r"\bhorticulture\b", r"\bculinary\b", r"\bhospitality\b",
            r"\bguest\s+(?:services|experience|relations)\b",
            r"\bmerchandis(?:e|ing)\b", r"\bcostum(?:e|ing)\b",
            r"\bentertainment\s+(?:intern|technician)\b",
            r"\bpublic\s+relations\b", r"\bcommunications\s+intern\b",
            r"\bsocial\s+media\b", r"\bbrand\s+(?:marketing|management)\b",
            r"\bevent\s+(?:planning|management|coordinator)\b",
            r"\bfacilities\b", r"\blogistics\s+(?:intern|coordinator|analyst)\b",
            r"\bsupply\s+chain\b", r"\btalent\s+(?:acquisition|management)\b",
        )
        _HAS_TECH_ROLE = re.search(
            r"\b(?:software|swe|developer|programmer|full[\s-]?stack|backend|"
            r"frontend|devops|sre|machine\s+learning|computer\s+vision|"
            r"data\s+(?:engineer|scientist|analytics|analyst)|"
            r"analytics\s+engineer|python|java)\b", title_lower)
        if not _HAS_TECH_ROLE:
            for _nd in _NONTECH_DOMAIN:
                if re.search(_nd, title_lower):
                    return False, "Non-tech domain role"

        # A title that says SOFTWARE is a software job, whatever it runs on.
        # "Embedded Software Engineer" and "Sensor Software Engineer" were
        # being rejected because \bembedded and \bsensor matched first - 28
        # embedded-SWE roles lost in a single run. Hardware-only titles
        # (Electrical Engineer, Motor Controls Engineer) still get rejected.
        _IS_SOFTWARE = re.search(
            r"\b(?:software|swe|developer|programmer|full[\s-]?stack|backend|"
            r"back[\s-]end|frontend|front[\s-]end|devops|sre|"
            r"machine\s+learning|computer\s+vision|data\s+engineer)\b",
            title_lower)
        _HARD_ONLY = re.search(
            r"\b(?:electrical|mechanical|welding|plumbing|hvac|"
            r"cnc|machining|soldering|corrosion|antenna|pcb|vlsi|"
            r"power\s+electronics)\b", title_lower)

        for pattern in hardware_patterns:
            if re.search(pattern, title_lower):
                if _IS_SOFTWARE and not _HARD_ONLY:
                    break          # software role - keep it
                return False, "Hardware/non-CS role"

        # Reject unpaid internships
        if "unpaid" in title_lower:
            return False, "Unpaid internship"

        # Reject titles that are LinkedIn job card text
        if " hiring " in title_lower and " in " in title_lower:
            return False, "LinkedIn job card text, not real title"
        if title_lower.endswith("| linkedin"):
            return False, "LinkedIn job card text, not real title"

        spam_patterns = [
            r"^application$",
            r"^apply\s",
            r"click\s+here",
            r"apply\s+now",
            r"view\s+job",
            r"submit\s+your",
            r"^join\s",
        ]

        for pattern in spam_patterns:
            if re.search(pattern, title_lower):
                return False, "Invalid title pattern"

        return True, None

    _COMPANY_ALIASES = {
        "robert bosch": "bosch", "bosch group": "bosch",
        "crum & forster insurance": "crum & forster", "cfins": "crum & forster",
        "xpeng motors": "xpeng", "xpengmotors": "xpeng",
        "farmers insurance": "farmers insurance", "farmersinsurance": "farmers insurance",
        "climateai": "climateai", "climate ai": "climateai",
        "plus2": "plusai", "plusai": "plusai", "plus ai": "plusai", "plus 2": "plusai", "plus-2": "plusai",
        "wwfus": "world wildlife fund", "world wildlife fund": "world wildlife fund",
        "lilasciences": "lila sciences", "lila sciences": "lila sciences",
        "temporaltechnologies": "temporal technologies",
        "podium81": "podium", "podium": "podium",
        "foxnew fox news network": "fox news", "fox news": "fox news",
        "bcbst bluecross blueshield of tennessee": "bluecross blueshield",
        "lexisnexis risk solutions fl": "lexisnexis",
        "lexisnexis risk solutions": "lexisnexis",
    }

    @staticmethod
    def normalize_company_for_dedup(company):
        import re as _re
        c = company.strip().lower()
        c = _re.sub(r"[^a-z0-9 ]", "", c).strip()
        for alias, canonical in TitleProcessor._COMPANY_ALIASES.items():
            if c == alias or c.startswith(alias):
                return canonical
        return c

    @staticmethod
    @lru_cache(maxsize=256)
    def is_cs_engineering_role(title, description=""):
        title_lower = title.lower()

        try:
            from aggregator.config import GUARANTEED_TECHNICAL_PHRASES

            for phrase in GUARANTEED_TECHNICAL_PHRASES:
                if phrase in title_lower:
                    return True
        except (ImportError, AttributeError):
            pass

        combined_text = (title + " " + description).lower()

        # Early rejection: purely non-technical title
        _ntl = (title or "").lower()
        _nt = sum(1 for kw in NON_TECHNICAL_PURE if kw in _ntl)
        _tt = sum(1 for kw in TECHNICAL_ROLE_KEYWORDS if kw in _ntl)
        if _nt > 0 and _tt == 0:
            return False
        if any(kw in combined_text for kw in TECHNICAL_ROLE_KEYWORDS):
            non_tech_pure = sum(1 for kw in NON_TECHNICAL_PURE if kw in combined_text)
            tech_count = sum(1 for kw in TECHNICAL_ROLE_KEYWORDS if kw in combined_text)

            if tech_count > non_tech_pure:
                return True

        try:
            from aggregator.config import TECHNICAL_PATTERNS

            for pattern in TECHNICAL_PATTERNS:
                if re.search(pattern, combined_text):
                    return True
        except (ImportError, AttributeError):
            pass

        # Borderline: keyword score was low but nonzero — ask Claude before rejecting
        # Only triggers when tech_count > 0 (some signal) but didn't pass threshold
        if any(kw in combined_text for kw in TECHNICAL_ROLE_KEYWORDS):
            claude_result = TitleProcessor._claude_is_tech_role(title)
            if claude_result is True:
                logging.info(f"Claude tech override: '{title}' accepted as CS role")
                return True

        return False

    @staticmethod
    def _claude_is_tech_role(title, company=""):
        """
        Ask Claude if this is a CS/Engineering role. Called only for borderline cases.
        Returns True if Claude says yes, False otherwise. Returns None if API unavailable.
        Cached to avoid repeat calls for same title.
        """
        import os as _os
        cache_key = title.lower().strip()
        if not hasattr(TitleProcessor, "_tech_cache"):
            TitleProcessor._tech_cache = {}
        if cache_key in TitleProcessor._tech_cache:
            return TitleProcessor._tech_cache[cache_key]

        _root = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
        _env = _os.path.join(_root, ".env")
        _api_key = ""
        if _os.path.exists(_env):
            for ln in open(_env):
                ln = ln.strip()
                if ln.startswith("ANTHROPIC_API_KEY="):
                    _api_key = ln.split("=", 1)[1].strip()
                    break
        _api_key = _api_key or _os.environ.get("ANTHROPIC_API_KEY", "")
        if not _api_key:
            TitleProcessor._tech_cache[cache_key] = None
            return None

        try:
            import urllib.request, json as _j
            prompt = (
                f"Job title: {title}\nCompany: {company or 'unknown'}\n\n"
                "Is this a software engineering, data science, ML, or CS internship role "
                "that a Computer Science MS student should apply to? "
                "Answer ONLY 'yes' or 'no'."
            )
            body = _j.dumps({
                "model": "claude-haiku-4-5-20251001",
                "max_tokens": 5,
                "messages": [{"role": "user", "content": prompt}]
            }).encode()
            req = urllib.request.Request(
                "https://api.anthropic.com/v1/messages",
                data=body,
                headers={
                    "x-api-key": _api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                }
            )
            resp = urllib.request.urlopen(req, timeout=8)
            data = _j.loads(resp.read())
            answer = data["content"][0]["text"].strip().lower().rstrip(".")
            result = answer == "yes"
            TitleProcessor._tech_cache[cache_key] = result
            logging.debug(f"Claude tech check: '{title}' → {result}")
            return result
        except Exception as e:
            logging.debug(f"Claude tech check failed for '{title}': {e}")
            TitleProcessor._tech_cache[cache_key] = None
            return None

    @staticmethod
    def is_title_extraction_reliable(title):
        if not title:
            return False

        suspicious_patterns = [
            (r"\|.*\|", "Multiple pipes"),
            (r",\s*[A-Z]{2}$", "Ends with state code"),
            (r"(?:remote|hybrid|onsite)", "Contains work mode"),
            (r"(?:spring|summer|fall|winter)\s*(?:\||$)", "Contains season"),
            (r"^\d{4}", "Starts with year"),
            (r"columbus.*ohio|ohio.*columbus", "Contains full location"),
        ]

        for pattern, reason in suspicious_patterns:
            if re.search(pattern, title, re.I):
                return False

        return True

    @staticmethod
    def is_internship_role(title, job_type="", page_text="", github_category=""):
        try:
            from aggregator.config import (
                VALID_INTERNSHIP_TYPES,
                INTERNSHIP_INDICATORS,
                GRADUATE_PROGRAM_PATTERNS,
                DURATION_INTERNSHIP_PATTERNS,
                ENROLLMENT_PATTERNS,
                CONFLICTING_SIGNAL_PATTERNS,
            )
        except (ImportError, AttributeError):
            VALID_INTERNSHIP_TYPES = [
                "Internship",
                "Co-op",
                "Fellowship",
                "Apprenticeship",
            ]
            INTERNSHIP_INDICATORS = [
                "apprentice",
                "fellowship",
                "trainee",
                "emerging talent",
            ]
            GRADUATE_PROGRAM_PATTERNS = []
            DURATION_INTERNSHIP_PATTERNS = []
            ENROLLMENT_PATTERNS = []
            CONFLICTING_SIGNAL_PATTERNS = []

        title_lower = title.lower()

        excluded = {
            "senior",
            "sr.",
            "sr ",
            "staff",
            "principal",
            "lead",
            "director",
        }
        # 'architect/architecture' is a subject, not a role level — never reject

        # Trust GitHub internship repo category
        if github_category and "internship" in github_category.lower():
            has_senior = any(level in title_lower for level in excluded)
            if not has_senior:
                return True, None

        if job_type in VALID_INTERNSHIP_TYPES:
            for level in excluded:
                if level in title_lower:
                    if level == "manager" and "product manager" in title_lower:
                        continue
                    return False, f"Senior/experienced role: contains '{level}'"
            return True, None

        if any(kw in title_lower for kw in ["intern", "co-op", "coop", "co op"]) or title_lower.rstrip().endswith(" co"):
            if re.search(r"\(intern\)", title_lower):
                return True, None
            for level in excluded:
                if level in title_lower:
                    if level == "manager" and "product manager" in title_lower:
                        continue
                    return False, f"Senior/experienced role: contains '{level}'"
            for level in ["manager"]:
                if level in title_lower:
                    if (
                        "product manager" in title_lower
                        or "program manager" in title_lower
                    ):
                        continue
                    return False, f"Senior/experienced role: contains '{level}'"
            return True, None

        if any(kw in title_lower for kw in INTERNSHIP_INDICATORS):
            for level in excluded:
                if level in title_lower:
                    return False, f"Senior/experienced role: contains '{level}'"
            return True, None

        # Trust page text if it says Internship (catches Simplify pages with generic titles)
        if page_text:
            page_lower = page_text[:2000].lower()
            if any(marker in page_lower for marker in [
                "internship", "intern position", "intern program",
                "summer intern", "co-op position", "co-op program",
            ]):
                for level in excluded:
                    if level in title_lower:
                        return False, f"Senior/experienced role: contains '{level}'"
                return True, None

        if "graduate" in title_lower:
            for pattern in GRADUATE_PROGRAM_PATTERNS:
                if re.search(pattern, title_lower):
                    return True, None

            if page_text:
                page_lower = page_text[:5000].lower()

                has_duration = any(
                    re.search(p, page_lower) for p in DURATION_INTERNSHIP_PATTERNS
                )
                has_enrollment = any(
                    re.search(p, page_lower) for p in ENROLLMENT_PATTERNS
                )
                has_conflicting = any(
                    re.search(p, page_lower) for p in CONFLICTING_SIGNAL_PATTERNS
                )

                if has_duration or has_enrollment:
                    if has_conflicting:
                        return True, "⚠️ GRADUATE - CONFLICTING SIGNALS"
                    return True, None

        return False, "Not internship/co-op role"

    @staticmethod
    def check_season_requirement(title, page_text=""):
        try:
            from aggregator.config import PAGE_TEXT_STANDARD_SCAN
        except (ImportError, AttributeError):
            PAGE_TEXT_STANDARD_SCAN = 5000

        title_lower = title.lower()

        # Reject Spring explicitly in title — Spring 2026 is not Summer 2026
        if re.search(r"\bspring\s*20(?:2[5-9]|3\d)\b", title_lower):
            return False, "Wrong season: Spring (only Summer 2026+ accepted)"
        if re.search(r"\bspring\b", title_lower) and "summer" not in title_lower:
            # Spring without summer qualifier — reject
            return False, "Wrong season: Spring (only Summer 2026+ accepted)"

        # Trust title year — if title says Summer 2026+, accept immediately
        if re.search(r"\bsummer\s*20(2[6-9]|3\d)\b", title_lower):
            return True, ""
        if re.search(r"\bfall\s*20(2[6-9]|3\d)\b", title_lower):
            return True, ""

        # Plain year check — if title has 2026+ without spring qualifier
        for m in re.finditer(r"\b(202[4-9]|203[0-9])\b", title_lower):
            if int(m.group(1)) >= 2026:
                return True, ""
        if "2026" in title_lower or "2027" in title_lower:
            return True, ""

        # Reject titles with explicit old season/year (Summer 2024, Fall 2025, 2025 etc.)
        if re.search(r"(?:summer|fall|spring|winter)\s*20(?:2[0-5])", title_lower):
            _m = re.search(r"(?:summer|fall|spring|winter)\s*(20(?:2[0-5]))", title_lower)
            return False, f"Wrong season: {_m.group(0) if _m else 'old'}"
        if re.search(r"\b20(?:2[0-5])\b", title_lower) and not re.search(r"\b20(?:2[6-9]|3\d)\b", title_lower):
            _m = re.search(r"(20(?:2[0-5]))", title_lower)
            return False, f"Wrong season: {_m.group(1) if _m else 'old'}"

        limited_text = page_text[:PAGE_TEXT_STANDARD_SCAN] if page_text else ""

        # Check page text for Spring-only start dates like "begins April 2026"
        if limited_text:
            spring_page_patterns = [
                r"(?:start|begin|starting|beginning|commence)s?\s+(?:in\s+)?(?:april|march|february|january)\s+20(?:2[6-9]|3\d)",
                r"(?:april|march|february|january)\s+20(?:2[6-9]|3\d)\s+(?:start|begin|cohort)",
                r"spring\s+20(?:2[6-9]|3\d)\s+(?:intern|cohort|class|semester)",
                r"(?:intern|cohort|class)\s+(?:start|begin)s?\s+(?:april|march|february)",
            ]
            for pat in spring_page_patterns:
                if re.search(pat, limited_text.lower()):
                    # Only reject if no summer signal anywhere
                    if not re.search(r"\bsummer\b", limited_text.lower()):
                        return False, "Wrong season: Spring start date (April/March)"

        combined = (title + " " + limited_text).lower()

        years_found = []
        for match in re.finditer(r"\b(202[4-9]|203[0-9])\b", combined):
            year = int(match.group(1))
            # Skip copyright years (© 2025, copyright 2025)
            start = max(0, match.start() - 20)
            context = combined[start:match.start()]
            if any(marker in context for marker in [
                '©', 'copyright', '℗', 'cohort', 'established', 'founded',
                'fiscal', 'revenue', 'fy', 'reported', 'announced', 'acquired',
                'raised', 'valued', 'as of', 'year ended', 'prior year',
                'q1', 'q2', 'q3', 'q4', 'earnings', 'profit', 'posted on',
            ]):
                continue
            years_found.append(year)

        if not years_found:
            return True, ""

        if any(year >= 2026 for year in years_found):
            return True, ""

        max_year = max(years_found)
        if max_year < 2026:
            # Only reject if title explicitly mentions old season
            # Don't reject based on page text years alone (copyright, founded dates slip through)
            title_has_old_year = bool(re.search(r"\b20(?:2[0-5])\b", title_lower))
            title_has_old_season = bool(re.search(r"\b(?:summer|fall|spring|winter)\s*20(?:2[0-5])\b", title_lower))
            if title_has_old_season:
                return False, f"Wrong season: {max_year}"
            if title_has_old_year and "summer" not in title_lower and "fall" not in title_lower:
                return False, f"Wrong season: {max_year}"
            # Page mentions old year but title is clean — accept (likely copyright/founded date)
            return True, ""

        return True, ""


# ============================================================================
# Job ID Extraction - ORIGINAL
# ============================================================================


class JobIDExtractor:
    @staticmethod
    def extract_from_url(url, platform="generic"):
        """ENHANCED: Platform-specific handling (Glassdoor N/A, career sites deprioritized)"""
        if not url:
            return ExtractionResult(None, 0.0, "url_extract")

        # NEW: Glassdoor special handling
        if platform == "glassdoor" or "glassdoor" in url.lower():
            return ExtractionResult("N/A", 0.98, "glassdoor_na")

        # Career sites often have misleading IDs in URLs (LinkedIn source IDs, etc.)
        # Lower confidence so page text extraction wins
        career_domains = ["disneycareers.com", "careers.google.com", "careers.",
                          "eightfold.ai", "careers.microsoft", "jobs.",
                          "?source=LINKEDIN", "?utm_source="]
        is_career_site = any(cd in url.lower() for cd in career_domains)

        for pattern, confidence in _COMPILED_JOB_ID_PATTERNS:
            try:
                match = pattern.search(url)
                if match:
                    job_id = match.group(1).strip()
                    if JobIDExtractor._is_valid_id(job_id):
                        # Penalize URL-extracted IDs on career sites (page text is more reliable)
                        if is_career_site and len(job_id) > 9:
                            confidence = min(confidence, 0.80)
                        return ExtractionResult(job_id, confidence, "url_pattern")
            except Exception as e:
                logging.debug(f"Job ID pattern failed on URL {url}: {e}")
                continue

        return ExtractionResult(None, 0.0, "url_extract")

    @staticmethod
    def extract_from_html_meta(soup):
        if not soup:
            return ExtractionResult(None, 0.0, "html_meta")

        try:
            for tag_name, attrs in [
                ("meta", {"property": "og:job:id"}),
                ("meta", {"name": "job-id"}),
            ]:
                elem = soup.find(tag_name, attrs)
                if elem:
                    value = elem.get("content") or elem.get("value")
                    if value and JobIDExtractor._is_valid_id(value):
                        return ExtractionResult(value, 0.95, "html_meta")

            for elem in soup.find_all(attrs={"data-job-id": True}):
                value = elem.get("data-job-id")
                if value and JobIDExtractor._is_valid_id(value):
                    return ExtractionResult(value, 0.90, "html_data_attr")
        except Exception as e:
            logging.debug(f"HTML meta extraction failed: {e}")

        return ExtractionResult(None, 0.0, "html_meta")

    @staticmethod
    def extract_from_json_ld(soup):
        if not soup:
            return ExtractionResult(None, 0.0, "json_ld")

        try:
            json_ld = soup.find("script", {"type": "application/ld+json"})
            if json_ld:
                data = json.loads(json_ld.string)
                if isinstance(data, dict):
                    identifier = data.get("identifier", {})
                    if isinstance(identifier, dict) and identifier.get("value"):
                        value = identifier["value"]
                        if JobIDExtractor._is_valid_id(value):
                            return ExtractionResult(value, 0.95, "json_ld")
        except Exception as e:
            logging.debug(f"JSON-LD job ID extraction failed: {e}")

        return ExtractionResult(None, 0.0, "json_ld")

    @staticmethod
    def extract_from_page_text(soup):
        if not soup:
            return ExtractionResult(None, 0.0, "page_text")

        try:
            page_text = soup.get_text()[:5000]
            for pattern, confidence in [
                (r"Job\s*Code\s*:?\s*([A-Z0-9]{4,15})\b", 0.90),
                (r"Job\s*ID\s*:?\s*([A-Z0-9\-]{4,15})\b", 0.85),
                (
                    r"job\s+requisition\s+id\s*:?\s*([A-Z0-9\-]{4,20})\b",
                    0.88,
                ),  # NEW: vRad format
                (r"Req(?:uisition)?\s*ID\s*:?\s*([A-Z0-9\-]{4,20})\b", 0.85),
                (
                    r"requisition\s+(?:id|number)\s*:?\s*([A-Z0-9\-]{4,20})\b",
                    0.85,
                ),  # NEW: flexible
                (r"Role\s*ID\s*:?\s*([A-Z0-9\-]{4,20})\b", 0.85),  # NEW: EA format
                (r"\b(R-\d{4}-\d{2}-\d{2,4})\b", 0.83),  # SiriusXM: R-2026-01-115
            ]:
                match = re.search(pattern, page_text, re.I)
                if match:
                    job_id = match.group(1).strip()
                    if JobIDExtractor._is_valid_id(job_id):
                        return ExtractionResult(job_id, confidence, "page_text_labeled")
        except Exception as e:
            logging.debug(f"Page text job ID extraction failed: {e}")

        return ExtractionResult(None, 0.0, "page_text")

    @staticmethod
    @lru_cache(maxsize=512)
    def _is_valid_id(job_id, url=''):
        if url and 'greenhouse.io' in url.lower():
            return False
        # Strip trailing "Apply" suffix from TikTok/ByteDance job IDs
        # e.g. "A112542AApply" → "A112542A", "A38740Apply" → "A38740"
        if job_id and job_id.endswith("Apply"):
            job_id = job_id[:-5].strip()
        if not job_id or not (4 <= len(job_id) <= 20):
            return False
        if not re.match(r"^[A-Z0-9\-_]+$", job_id, re.I):
            return False
        if not any(c.isdigit() for c in job_id):
            return False
        return job_id.upper() not in {
            "APPLY",
            "NOW",
            "HERE",
            "JOIN",
            "CLICK",
            "VIEW",
            "SOFTWARE",
            "ENGINEER",
        }

    @staticmethod
    def extract_all_methods(url, soup, platform="generic"):
        """ORIGINAL: All methods execute, vote on non-None"""
        results = [
            JobIDExtractor.extract_from_url(url, platform),
            JobIDExtractor.extract_from_html_meta(soup),
            JobIDExtractor.extract_from_json_ld(soup),
            JobIDExtractor.extract_from_page_text(soup),
        ]

        best_result = ExtractionVoter.vote(
            results, min_confidence=MIN_CONFIDENCE_JOB_ID
        )

        if best_result:
            return best_result.value

        try:
            from aggregator.config import JOB_ID_PREFERENCES

            return JOB_ID_PREFERENCES.get("fallback_value", "N/A")
        except (ImportError, AttributeError):
            return "N/A"


# ============================================================================
# Location Extraction - ORIGINAL + ENHANCED
# ============================================================================


class LocationExtractor:
    @staticmethod
    def extract_from_title(title):
        """NEW PRIORITY 1: Extract from title (catches Boomi, EA cases)"""
        if not title:
            return ExtractionResult(None, 0.0, "title_parse")

        try:
            # Pattern 1: (City, ST) or (City, Province)
            match = re.search(r"\(([A-Za-z\s]+),\s*([A-Z]{2})\)", title)
            if match:
                city, code = match.groups()
                return ExtractionResult(
                    f"{city.strip()}, {code}", 0.96, "title_parentheses"
                )

            # Pattern 2: - City, ST (original)
            match = re.search(r"[-–]\s*([A-Za-z\s]+,\s*[A-Z]{2})(?:\s|$)", title)
            if match:
                return ExtractionResult(
                    match.group(1).strip(), 0.90, "title_city_state"
                )

            # Pattern 3: (remote)
            if re.search(r"\(remote\)", title, re.I):
                return ExtractionResult("Remote", 0.92, "title_remote")
        except Exception as e:
            logging.debug(f"Title location extraction failed: {e}")

        return ExtractionResult(None, 0.0, "title_parse")

    @staticmethod
    def extract_from_html_selectors(soup, platform="generic"):
        """ORIGINAL: Platform-specific selectors"""
        if not soup:
            return ExtractionResult(None, 0.0, "html_selectors")

        selectors = PLATFORM_CONFIGS.get(platform, {}).get(
            "location_selectors", LOCATION_SELECTORS
        )

        for selector, confidence in selectors:
            try:
                elem = soup.select_one(selector)
                if elem:
                    text = elem.get_text(strip=True)
                    if 2 < len(text) < 100:
                        cleaned = LocationProcessor.clean_location_aggressive(text)
                        if cleaned and cleaned != "Unknown":
                            return ExtractionResult(
                                cleaned, confidence, "html_selector"
                            )
            except Exception as e:
                logging.debug(f"Selector {selector} failed: {e}")
                continue

        return ExtractionResult(None, 0.0, "html_selectors")

    @staticmethod
    def extract_from_json_ld(soup):
        """ORIGINAL"""
        if not soup:
            return ExtractionResult(None, 0.0, "json_ld")

        try:
            json_ld = soup.find("script", {"type": "application/ld+json"})
            if json_ld:
                data = json.loads(json_ld.string)
                if isinstance(data, dict):
                    job_location = data.get("jobLocation", {})
                    if isinstance(job_location, dict):
                        address = job_location.get("address", {})
                        if isinstance(address, dict):
                            city = address.get("addressLocality", "")
                            state = address.get("addressRegion", "")
                            if city and state and len(state) == 2:
                                return ExtractionResult(
                                    f"{city}, {state}", 0.95, "json_ld"
                                )
        except Exception as e:
            logging.debug(f"JSON-LD location extraction failed: {e}")

        return ExtractionResult(None, 0.0, "json_ld")

    @staticmethod
    def extract_from_page_text(soup):
        """ORIGINAL + ENHANCED"""
        if not soup:
            return ExtractionResult(None, 0.0, "page_text")

        try:
            page_text = soup.get_text()[:5000]

            # Original pattern
            match = _LOCATION_LABEL_PATTERN.search(page_text)
            if match:
                location = match.group(1).strip()
                if len(location) < 50:
                    cleaned = LocationProcessor.clean_location_aggressive(location)
                    if cleaned and cleaned != "Unknown":
                        return ExtractionResult(cleaned, 0.75, "page_text_labeled")

            match = _LOCATION_NO_SPACE_PATTERN.search(page_text)
            if match:
                location = match.group(1).strip()
                if len(location) < 50:
                    cleaned = LocationProcessor.clean_location_aggressive(location)
                    if cleaned and cleaned != "Unknown":
                        return ExtractionResult(cleaned, 0.75, "page_text_no_space")

            # NEW: EA-style pattern
            ea_pattern = (
                r"Locations?:\s*([A-Za-z\s]+,\s*[A-Za-z\s]+(?:,\s*[A-Za-z\s]+)?)"
            )
            match = re.search(ea_pattern, page_text, re.I)
            if match:
                location = match.group(1).strip()
                if len(location) < 100:
                    cleaned = LocationProcessor.clean_location_aggressive(location)
                    if cleaned and cleaned != "Unknown":
                        return ExtractionResult(cleaned, 0.80, "page_text_ea_format")

        except Exception as e:
            logging.debug(f"Page text location extraction failed: {e}")

        return ExtractionResult(None, 0.0, "page_text")

    @staticmethod
    def extract_from_url(url):
        """ORIGINAL + ENHANCED Workday parser"""
        if not url:
            return ExtractionResult(None, 0.0, "url_parse")

        try:
            # NEW: Enhanced Workday URL parser
            if "workday" in url.lower():
                return LocationExtractor._extract_workday_url_enhanced(url)

            # ORIGINAL: Standard patterns
            patterns = [
                r"/job/([A-Z][a-z]+(?:-[A-Z][a-z]+)*)-([A-Z]{2})(?:-USA)?/",
                r"/([A-Z][a-z]+(?:-[A-Z][a-z]+)*)-([A-Z]{2})/",
            ]

            for pattern in patterns:
                match = re.search(pattern, url)
                if match and len(match.groups()) == 2:
                    city, state = match.groups()
                    city = city.replace("-", " ")
                    if validate_us_state_code(state):
                        return ExtractionResult(f"{city}, {state}", 0.75, "url_path")
        except Exception as e:
            logging.debug(f"URL location extraction failed: {e}")

        return ExtractionResult(None, 0.0, "url_parse")

    @staticmethod
    def _extract_workday_url_enhanced(url):
        """NEW: Ultimate Workday URL parser - handles ALL edge cases"""
        try:
            match = re.search(r"/job/([^/]+)/", url)
            if not match:
                return ExtractionResult(None, 0.0, "url_workday")

            segment = match.group(1)

            # Step 1: Remove country suffixes (order matters - longest first)
            country_suffixes = [
                "-United-States-of-America",
                "-United-States",
                "-USA",
                "-US",
            ]
            for suffix in country_suffixes:
                segment = segment.replace(suffix, "")

            # Step 2: Remove address numbers (e.g., -307-Legget-Dr, -1800-Arch-St)
            segment = re.sub(r"-\d{3,}-.*$", "", segment)

            # Step 3: Split by hyphen and filter empty strings + "US"/"USA" tokens
            parts = [
                p for p in segment.split("-") if p and p.upper() not in ["US", "USA"]
            ]

            if len(parts) < 2:
                # Single part - check if Canadian city
                if len(parts) == 1 and parts[0].lower() in MAJOR_CANADIAN_CITIES:
                    return ExtractionResult(
                        f"CANADIAN_CITY_{parts[0]}", 0.88, "url_canadian_city"
                    )
                return ExtractionResult(None, 0.0, "url_workday")

            # Step 4: Try REVERSED format first (PA-Philadelphia)
            if len(parts[0]) == 2 and validate_us_state_code(parts[0]):
                state_code = parts[0].upper()
                city_parts = parts[1:]
                # Remove trailing numbers if any
                city_parts = [p for p in city_parts if not p.isdigit()]
                city = " ".join(city_parts)
                return ExtractionResult(
                    f"{city}, {state_code}", 0.93, "url_workday_reversed"
                )

            # Step 5: Try RIGHT-TO-LEFT state matching (normal format)
            state_code = None
            state_end_index = len(parts)

            # Try 2-word states (e.g., "South Dakota")
            for i in range(len(parts) - 1, 0, -1):
                if i < 1:
                    break
                two_word = f"{parts[i-1]} {parts[i]}".lower()
                if two_word in FULL_STATE_NAMES:
                    state_code = FULL_STATE_NAMES[two_word]
                    state_end_index = i - 1
                    break

            # Try 1-word states
            if not state_code:
                last_part = parts[-1]
                # Check 2-letter code
                if len(last_part) == 2 and validate_us_state_code(last_part):
                    state_code = last_part.upper()
                    state_end_index = len(parts) - 1
                # Check full state name
                elif last_part.lower() in FULL_STATE_NAMES:
                    state_code = FULL_STATE_NAMES[last_part.lower()]
                    state_end_index = len(parts) - 1

            if state_code:
                # Extract city (everything before state)
                city_parts = parts[:state_end_index]
                # If state was at index 0, city is everything AFTER it
                if not city_parts and state_end_index == 0:
                    city_parts = parts[1:]
                city = " ".join(city_parts)
                return ExtractionResult(
                    f"{city}, {state_code}", 0.92, "url_workday_ultimate"
                )

            # No US state found - check if Canadian city
            first_word = parts[0].lower()
            if first_word in MAJOR_CANADIAN_CITIES:
                return ExtractionResult(
                    f"CANADIAN_CITY_{parts[0]}", 0.88, "url_canadian_city"
                )

        except Exception as e:
            logging.debug(f"Workday URL parsing failed: {e}")

        return ExtractionResult(None, 0.0, "url_workday")

    @staticmethod
    def extract_all_methods(url, soup, title="", platform="generic", page_source=""):
        """
        CRITICAL ENHANCEMENT: ALL methods execute (no short-circuit)
        Title extraction is PRIORITY 1 (highest confidence)
        page_source parameter added for Selenium text extraction
        """
        results = [
            LocationExtractor.extract_from_title(title),  # NEW PRIORITY 1
            LocationExtractor.extract_from_json_ld(soup),
            LocationExtractor.extract_from_html_selectors(soup, platform),
            LocationExtractor.extract_from_page_text(soup),
            LocationExtractor.extract_from_url(url),  # ENHANCED Workday parser
        ]

        # NEW: If page_source available (from Selenium), try text extraction
        if page_source:
            text_result = LocationExtractor._extract_from_selenium_text(page_source)
            if text_result:
                results.append(text_result)

        best_result = ExtractionVoter.vote(
            results, min_confidence=MIN_CONFIDENCE_LOCATION
        )
        return (
            LocationProcessor.format_location_clean(best_result.value)
            if best_result
            else "Unknown"
        )

    @staticmethod
    def _extract_from_selenium_text(page_source):
        """NEW: Extract from raw Selenium page source (selector-independent)"""
        if not page_source:
            return None

        try:
            patterns = [
                (r"📍\s*([A-Za-z\s]+,\s*[A-Z]{2})", 0.90),
                (r">([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?,\s*[A-Z]{2,15})<", 0.82),
            ]

            for pattern, conf in patterns:
                match = re.search(pattern, page_source, re.I)
                if match:
                    location = match.group(1).strip()
                    if 3 < len(location) < 100:
                        return ExtractionResult(location, conf, "selenium_text")
        except Exception as _e:
            logging.debug(f"op failed: {_e}")

        return None


# ============================================================================
# Location Processing - ORIGINAL + ENHANCED
# ============================================================================


class LocationProcessor:
    @staticmethod
    @lru_cache(maxsize=512)
    def clean_location_aggressive(location_text):
        """ORIGINAL cleaning logic"""
        if not location_text or len(location_text) < 2:
            return "Unknown"
        # Reject garbage: if location text is too long, it likely contains description bleed
        if len(location_text) > 80:
            # Try to salvage a city,state pattern from the beginning
            import re as _loc_re
            quick_match = _loc_re.match(r"^([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*,\s*[A-Z]{2})\b", location_text.strip())
            if quick_match:
                return quick_match.group(1)
            return "Unknown"

        try:
            location = location_text.strip()

            for pattern in _COMPILED_METADATA_PATTERNS:
                location = pattern.sub("", location)

            for pattern in _COMPILED_ARTIFACT_PATTERNS:
                location = pattern.sub("", location)

            location = re.sub(r"\s*\([^)]*\)", "", location)
            location = re.sub(r"\s*-\s*[A-Z]{2,4}$", "", location)

            for keyword in [
                "Employment",
                "Type",
                "Details",
                "Program",
                "Internship",
                "Job",
                "Position",
            ]:
                if keyword in location:
                    location = location.split(keyword)[0].strip()
                    break

            location = re.sub(r",?\s*\d{3,5}\s+[A-Z][a-z]+", "", location)
            location = re.sub(r"\s*\d{5}(-\d{4})?\s*", "", location)
            location = re.sub(
                r",?\s*(?:USA|United States)\s*", "", location, flags=re.I
            )
            # FIX 5: strip floor/suite/address details
            location = re.sub(
                r",?\s*(?:floor|fl\.?|suite|ste\.?|room|rm\.?|bldg\.?|building)\s*\d*",
                "", location, flags=re.I
            )
            location = re.sub(
                r"\b\d+(?:st|nd|rd|th)\s+(?:floor|fl\.?)\b",
                "", location, flags=re.I
            )
            # Strip street addresses like "123 Main St" or "One Penn Plaza"
            location = re.sub(
                r"\b(?:one|two|three|\d+)\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\s+(?:plaza|ave|st|street|blvd|drive|dr|road|rd|lane|ln|way|court|ct)\b",
                "", location, flags=re.I
            ).strip().strip(",").strip()

            match = _CITY_STATE_PATTERN.search(location)
            if match:
                city, state = match.group(1).strip(), match.group(2).upper()
                if validate_us_state_code(state):
                    return f"{city}, {state}"

            location = re.sub(r"\s+", " ", location).strip()

            if not location or len(location) < 2:
                return "Unknown"

            if location.lower() in DEPARTMENT_KEYWORDS:
                return "Unknown"

            if any(kw in location.lower() for kw in INVALID_LOCATION_KEYWORDS):
                return "Unknown"

            state = get_state_for_city(location)
            if state:
                return f"{location.title()}, {state}"

            return location
        except Exception as e:
            logging.debug(f"Location cleaning failed for '{location_text}': {e}")
            return "Unknown"

    @staticmethod
    @lru_cache(maxsize=512)
    def format_location_clean(location):
        # First pass: normalize WFH/venue names before further processing
        location = LocationProcessor.normalize_location(location) if location else location

        """ORIGINAL + ENHANCED with suffix stripping, abbreviation expansion"""
        if not location or location == "Unknown":
            return "Unknown"

        try:
            # NEW: Handle Canadian city marker from URL extraction
            if location.startswith("CANADIAN_CITY_"):
                city = location.replace("CANADIAN_CITY_", "")
                return f"{city}, CANADA"  # Signal for rejection

            # ORIGINAL: Special values
            if location in ["Remote", "Hybrid"]:
                return location

            if location in WORKDAY_HQ_CODES:
                city, state = WORKDAY_HQ_CODES[location]
                return f"{city}, {state}" if state != "UNKNOWN" else "Unknown"

            # ORIGINAL: Clean
            location = LocationProcessor.clean_location_aggressive(location)
            if not location:
                return "Unknown"

            location = LocationProcessor.clean_location(location)
            if location == "Unknown":
                return "Unknown"

            # NEW: Strip location suffixes
            for suffix in LOCATION_SUFFIXES:
                if location.endswith(suffix):
                    location = location[: -len(suffix)].strip()
                    break

            # NEW: Expand city abbreviations
            location_lower = location.lower()
            if location_lower in CITY_ABBREVIATIONS:
                return CITY_ABBREVIATIONS[location_lower]

            # NEW: Convert full state names to codes
            location = LocationProcessor.convert_state_name_to_code(location)

            # ORIGINAL: State code prefix handling
            match = re.search(r"^([A-Z]{2})\s+(.+)$", location)
            if match:
                state, rest = match.groups()
                if validate_us_state_code(state):
                    return f"{rest.strip()}, {state}"

            location = re.sub(r"^[A-Z]{2,4}[:_-]\s*", "", location)

            # NEW: Validate isn't garbage
            if not LocationProcessor.is_valid_location_text(location):
                return "Unknown"

            # ORIGINAL: Check against bad values
            if location in {"Headquarters", "Office", "Campus"}:
                return "Unknown"

            return location
        except Exception as e:
            logging.debug(f"Location formatting failed for '{location}': {e}")
            return "Unknown"

    @staticmethod
    @lru_cache(maxsize=256)
    def convert_state_name_to_code(location):
        """NEW: Convert full state/province names to codes"""
        if not location:
            return location

        location_lower = location.lower()

        # Try 2-word states first (more specific)
        for full_name, code in FULL_STATE_NAMES.items():
            if len(full_name.split()) == 2 and full_name in location_lower:
                location = re.sub(rf"\b{full_name}\b", code, location, flags=re.I)
                return location

        # Then 1-word states
        for full_name, code in FULL_STATE_NAMES.items():
            if len(full_name.split()) == 1 and full_name in location_lower:
                location = re.sub(rf"\b{full_name}\b", code, location, flags=re.I)
                return location

        # Canadian provinces
        for full_name, code in CANADA_PROVINCE_NAMES.items():
            if full_name in location_lower:
                location = re.sub(rf"\b{full_name}\b", code, location, flags=re.I)
                return location

        return location

    @staticmethod
    @lru_cache(maxsize=256)
    def is_valid_location_text(text):
        """NEW: Validate location isn't garbage or too generic"""
        if not text or text == "Unknown":
            return False

        if len(text) > 50:
            return False
        if len(text) < 3:
            return False

        # FIX 2: Reject compensation/salary text leaking into location
        garbage_phrases = [
            "as well as",
            "in accordance with",
            "equal opportunity",
            "without regard to",
            "applicants",
            "candidates",
            "equity",
            "salary",
            "compensation",
            "benefits",
            "internal equity",
            "base pay",
            "pay range",
            "market rate",
            "experience and",
            "qualifications",
            "applications",
            "and internal",
            "or experience",
            "per hour",
            "per year",
            "annually",
            "multiple locations",
            "see job description",
            "various locations",
            "to be determined",
            "of the selected employee",
            "offered for this role",
            "show more options",
            "other locations schedule",
            "loading",
            "offered for this role",
            "show more options",
            "loading...",
            "other locations schedule",
            "see posting",
        ]

        text_lower = text.lower()
        if any(phrase in text_lower for phrase in garbage_phrases):
            return False

        # Reject too generic
        if text_lower in [
            "united states",
            "us",
            "usa",
            "headquarters",
            "office",
            "campus",
        ]:
            return False

        # Reject state-only (no city)
        if text.upper() in US_STATES_FALLBACK:
            return False

        return True

    @staticmethod
    @staticmethod
    def normalize_location(location: str) -> str:
        """Normalize messy location strings to clean values."""
        if not location:
            return "Unknown"
        loc = location.strip()
        loc = re.sub(r"\s*\((?:On[,\s-]*site|Remote|Hybrid)\)\s*$", "", loc, flags=re.I).strip()

        # "Work from Home" variants → Remote
        wfh_patterns = [
            r"work\s+from\s+home", r"\bwfh\b", r"work\s+at\s+home",
            r"telecommut", r"telework", r"virtual\s+location",
            r"^remote\s+in\s+usa$", r"^in\s+usa$", r"^usa$",
            r"^united\s+states$", r"^us$",
        ]
        for pat in wfh_patterns:
            if re.search(pat, loc, re.I):
                return "Remote"

        # Strip venue/building names from Workday locations
        # e.g. "Auburn Hills PHINIA WHQ, MI" → "Auburn Hills, MI"
        # Strip ALL-CAPS venue tokens before state code: 'Auburn Hills PHINIA WHQ, MI' → 'Auburn Hills, MI'
        loc = re.sub(r'\b[A-Z]{3,}(?:\s+[A-Z]{2,})*(?=\s*,\s*[A-Z]{2}\b)', '', loc).strip()
        loc = re.sub(r',\s*,', ',', loc).strip().strip(',').strip()
        loc = re.sub(r'\s+,', ',', loc).strip()  # remove space before comma

        # Normalize iCIMS state-coded locations: "US-AZ-Scottsdale" → "Scottsdale, AZ"
        _icims_m = re.match(r'^US-([A-Z]{2})-(.+)$', loc.strip())
        if _icims_m:
            state, city = _icims_m.group(1), _icims_m.group(2).replace('-', ' ').strip()
            return f"{city}, {state}"

        # Normalize city abbreviations
        _CITY_NORM = {
            'nyc': 'New York, NY',
            'new york city': 'New York, NY',
            'sf': 'San Francisco, CA',
            'la': 'Los Angeles, CA',
            'dc': 'Washington, DC',
            'washington dc': 'Washington, DC',
        }
        _loc_check = loc.strip().lower()
        if _loc_check in _CITY_NORM:
            return _CITY_NORM[_loc_check]

        loc = re.sub(r',\s*,', ',', loc).strip().strip(',').strip()
        loc = re.sub(r'\s+,', ',', loc).strip()  # remove space before comma

        # "York, NY" → keep as-is (valid)
        # "City, MO" is a known bad extraction — can't fix without re-fetch

        return loc if loc else "Unknown"

    @staticmethod
    def extract_remote_status_enhanced(soup, location, url, description=""):
        """FIXED: Added description parameter to match job_aggregator.py calls"""
        if not soup:
            return "Unknown"

        try:
            if location:
                location_lower = location.lower()
                if "remote" in location_lower:
                    return "Remote"
                if "hybrid" in location_lower:
                    return "Hybrid"

            # NEW: Check description first if provided
            if description:
                desc_lower = description.lower()
                if "100% remote" in desc_lower or "fully remote" in desc_lower:
                    return "Remote"
                if "hybrid" in desc_lower:
                    return "Hybrid"
                if "on-site" in desc_lower or "onsite" in desc_lower:
                    return "On Site"

            page_text = soup.get_text()[:2000].lower()

            try:
                from aggregator.config import ENHANCED_REMOTE_PATTERNS

                for pattern in ENHANCED_REMOTE_PATTERNS:
                    if pattern in page_text[:500]:
                        if "hybrid" in pattern:
                            return "Hybrid"
                        else:
                            return "Remote"
            except (ImportError, AttributeError):
                pass

            if "100% remote" in page_text or "fully remote" in page_text:
                return "Remote"
            if "remote" in page_text[:500]:
                return "Remote"
            if "hybrid" in page_text[:500]:
                return "Hybrid"
            if "on-site" in page_text[:500] or "onsite" in page_text[:500]:
                return "On Site"
        except Exception as e:
            logging.debug(f"Remote status extraction failed: {e}")

        return "Unknown"

    _TITLE_INTL_COUNTRIES = {
        "poland", "germany", "india", "china", "japan", "korea",
        "france", "spain", "italy", "brazil", "mexico", "argentina",
        "australia", "singapore", "ireland", "netherlands", "sweden",
        "switzerland", "israel", "taiwan", "uk", "united kingdom",
        "czech republic", "romania", "hungary", "portugal", "denmark",
        "norway", "finland", "austria", "belgium", "luxembourg",
        "new zealand", "philippines", "thailand", "vietnam", "malaysia",
        "indonesia", "egypt", "south africa", "nigeria", "kenya",
        "colombia", "chile", "peru", "costa rica", "puerto rico",
    }

    @staticmethod
    def check_if_international(location, soup=None, url=None, title=""):
        try:
            from aggregator.config import (
                INTERNATIONAL_URL_INDICATORS,
                INTERNATIONAL_TEXT_INDICATORS,
            )
        except (ImportError, AttributeError):
            INTERNATIONAL_URL_INDICATORS = [".co.uk", ".ca", "/uk/", "/canada/",
                                            ".de", ".co.de", "/germany/", "/de/"]
            INTERNATIONAL_TEXT_INDICATORS = []

        # Check title for international country names
        if title:
            title_lower = title.lower()
            for country in LocationProcessor._TITLE_INTL_COUNTRIES:
                # Match country as word boundary: "- Poland", ", Poland", "(Poland)"
                import re as _re_intl
                if _re_intl.search(rf"(?:^|[\s,\-\(]){country}(?:[\s,\-\)\.]|$)", title_lower):
                    return f"Location: International ({country.title()} in title)"

        if url:
            url_lower = url.lower()
            for indicator in INTERNATIONAL_URL_INDICATORS:
                if indicator in url_lower:
                    country = (
                        "UK"
                        if "uk" in indicator or "gb" in indicator
                        else (
                            "Canada"
                            if "ca" in indicator or "canada" in indicator
                            else "International"
                        )
                    )
                    return f"Location: International ({country} from URL)"

        if title:
            title_location = LocationExtractor.extract_from_title(title)
            if title_location and title_location.value:
                loc_upper = title_location.value.upper()
                for province in CANADA_PROVINCES:
                    if f", {province}" in loc_upper:
                        if province == "ON" and ", CA" not in loc_upper:
                            return f"Location: Canada (from title)"
                        elif province != "ON":
                            return f"Location: Canada (from title: {province})"

        # Always run page Canada check first — catches cases where location
        # extraction produced garbage but the page clearly says Canada
        if soup:
            _page_canada = LocationProcessor._check_page_for_canada(soup)
            if _page_canada:
                return _page_canada

        if location and location not in ["Unknown", ""]:
            location_lower = location.lower()
            normalized = normalize_unicode(location_lower)

            if ", CANADA" in location.upper() or "CANADIAN_CITY_" in location:
                return "Location: Canada (from location field)"

            if "canada" in location_lower:
                return "Location: Canada"

            # Germany — including common OCR/parsing typos from Simplify
            _germany_variants = ["germany", "geany", "deutschland",
                                  "munich", "münchen", "berlin", "hamburg",
                                  "frankfurt", "stuttgart", "cologne", "köln",
                                  "düsseldorf", "dusseldorf"]
            for _gv in _germany_variants:
                if _gv in location_lower:
                    return f"Location: Germany ({location})"

            # FIX 3: catch ATS-style "CAN" suffix e.g. "Peterborough CAN", "Toronto CAN"
            import re as _re_can
            if _re_can.search(r"\bCAN\b", location):
                return "Location: Canada (CAN suffix)"
            # Also catch province codes used by some ATS systems
            _can_suffixes = [" ON", " BC", " AB", " QC", " MB", " SK", " NS", " NB", " NL", " PE", " YT", " NT", " NU"]
            for _sfx in _can_suffixes:
                if location.upper().endswith(_sfx):
                    return f"Location: Canada (province suffix {_sfx.strip()})"

            for full_name, code in CANADA_PROVINCE_NAMES.items():
                if re.search(rf"\b{full_name}\b", location_lower, re.I):
                    if full_name == "ontario" and ", ca" in location_lower:
                        continue
                    return f"Location: Canada ({full_name.title()})"

            for province in CANADA_PROVINCES:
                if re.search(rf",\s*{province}\b", location):
                    if province == "ON" and ", ca" in location_lower:
                        continue
                    return f"Location: Canada (province: {province})"

            if (", CA" in location or " - CA" in location) and any(
                ind in location_lower for ind in ["toronto", "ottawa", "montreal"]
            ):
                if "ontario, ca" not in location_lower:
                    return "Location: Canada"

            for city in MAJOR_CANADIAN_CITIES:
                if city in normalized:
                    if city in AMBIGUOUS_CITIES:
                        resolved = LocationProcessor._resolve_ambiguous_city(
                            city, location, soup, url
                        )
                        if resolved == "Canada":
                            return f"Location: Canada ({city.title()})"
                    else:
                        return f"Location: Canada ({city.title()})"

            try:
                from aggregator.config import UK_CITIES
            except (ImportError, AttributeError):
                UK_CITIES = ["london", "manchester", "edinburgh"]

            import re as _re2
            US_STATE_CODES = ['AL','AK','AZ','AR','CA','CO','CT','DE','FL','GA','HI','ID','IL','IN','IA','KS','KY','LA','ME','MD','MA','MI','MN','MS','MO','MT','NE','NV','NH','NJ','NM','NY','NC','ND','OH','OK','OR','PA','RI','SC','SD','TN','TX','UT','VT','VA','WA','WV','WI','WY','DC']
            for uk_city in UK_CITIES:
                if uk_city in normalized:
                    # Check if followed by US state code — means US city, not UK
                    has_us_state = False
                    for st in US_STATE_CODES:
                        if _re2.search(rf",\s*{st}\b", location, _re2.IGNORECASE):
                            has_us_state = True
                            break
                    if not has_us_state:
                        return f"Location: International (UK - {uk_city.title()})"

            import re as _re
            intl_countries = [
                "united kingdom", "england", "scotland", "wales",
                "india", "china", "germany", "france",
                "singapore", "australia", "switzerland", "japan",
                "ireland", "netherlands", "sweden", "israel",
                "thailand", "vietnam", "malaysia", "philippines",
                "indonesia", "south korea", "taiwan", "egypt",
                "colombia", "chile", "peru", "costa rica",
                "brazil", "mexico", "argentina", "poland",
                "romania", "czech republic", "hungary", "ukraine",
                "pakistan", "bangladesh", "sri lanka", "nigeria",
                "kenya", "south africa", "new zealand",
            ]
            for country in intl_countries:
                if _re.search(r'\b' + country + r'\b', location_lower):
                    return f"Location: International ({country.title()})"
            # Check 'uk' separately (short word, needs strict boundary)
            if _re.search(r'\buk\b', location_lower):
                return "Location: International (UK)"

        if url:
            city_from_url = LocationProcessor._extract_city_from_url(url)
            if city_from_url:
                city_lower = city_from_url.lower()
                if city_lower in MAJOR_CANADIAN_CITIES:
                    if city_lower not in AMBIGUOUS_CITIES:
                        return f"Location: Canada (URL city: {city_from_url})"
                    else:
                        resolved = LocationProcessor._resolve_ambiguous_city(
                            city_from_url, location, soup, url
                        )
                        if resolved == "Canada":
                            return f"Location: Canada (URL city: {city_from_url})"

        canada_page_check = LocationProcessor._check_page_for_canada(soup)
        if canada_page_check:
            if location and location != "Unknown":
                if re.search(r",\s*[A-Z]{2}$", location):
                    state_code = location[-2:]
                    if validate_us_state_code(state_code):
                        logging.debug(
                            f"Meta tag says Canada but location field has US state '{state_code}' - trusting location field"
                        )
                        return None
            return canada_page_check

        canada_url_check = LocationProcessor._check_url_for_canada(url)
        if canada_url_check:
            return canada_url_check

        if location == "Unknown" and soup:
            page_snippet = soup.get_text()[:3000].lower()
            for pattern, country in INTERNATIONAL_TEXT_INDICATORS:
                if re.search(pattern, page_snippet):
                    return f"Location: International ({country} from page)"

        return None

    @staticmethod
    def check_company_for_international(company_name):
        """NEW: Check if company name indicates international location"""
        if not company_name or company_name == "Unknown":
            return None

        international_indicators_in_company = [
            "(united kingdom)",
            "(uk)",
            " uk)",
            " ltd. (uk",
            "(canada)",
            "(international)",
            "(europe)",
            "ltd. (united kingdom)",
            "limited (uk)",
        ]

        company_lower = company_name.lower()
        for indicator in international_indicators_in_company:
            if indicator in company_lower:
                country = (
                    "UK"
                    if "uk" in indicator or "kingdom" in indicator
                    else "International"
                )
                return f"Location: {country} (from company name)"

        return None

    @staticmethod
    def _extract_city_from_url(url):
        """NEW: Extract first city name from URL path"""
        if not url:
            return None
        try:
            # Pattern 1: /job/CityName-...
            match = re.search(r"/job/([A-Z][a-z]+)(?:-|/)", url)
            if match:
                return match.group(1)

            # Pattern 2: Query parameter
            match = re.search(r"[?&]city=([A-Za-z]+)", url, re.I)
            if match:
                return match.group(1)
        except Exception as _e:
            logging.debug(f"op failed: {_e}")
        return None

    @staticmethod
    def _resolve_ambiguous_city(city, location, soup, url):
        """NEW: Resolve ambiguous cities using context"""
        if city.lower() not in AMBIGUOUS_CITIES:
            return "Unknown"

        context_text = ""
        if location:
            context_text += location.lower() + " "
        if soup:
            context_text += soup.get_text()[:3000].lower() + " "
        if url:
            context_text += url.lower() + " "

        canada_score = sum(1 for kw in CANADA_CONTEXT_KEYWORDS if kw in context_text)
        us_score = sum(1 for kw in US_CONTEXT_KEYWORDS if kw in context_text)

        if url and ".ca" in url.lower():
            canada_score += 3

        for province in CANADA_PROVINCES:
            if f", {province}" in context_text:
                canada_score += 2

        if canada_score > us_score:
            return "Canada"
        elif us_score > 0:
            return "US"

        # Default to US for truly ambiguous
        return "US"

    @staticmethod
    def _check_page_for_canada(soup):
        """ORIGINAL"""
        if not soup:
            return None

        try:
            meta_desc = soup.find("meta", {"property": "og:description"})
            if meta_desc and meta_desc.get("content"):
                content = meta_desc["content"].lower()
                if content == "canada" or "canada only" in content:
                    return "Location: Canada (from meta tag)"

            _raw = soup.get_text()[:10000]
            page_text = _raw
            for _lp in [" du Canada", " du canada", "du Canada\n", "du canada\n", "du Canada ", "du canada "]:
                page_text = page_text.replace(_lp, " ")
            for pattern, city_name in [
                (r"Ottawa\s*,?\s*Ontario", "Ottawa, ON"),
                (r"Toronto\s*,?\s*Ontario", "Toronto, ON"),
                (r"Montreal\s*,?\s*Quebec", "Montreal, QC"),
                (r"Vancouver\s*,?\s*British\s*Columbia", "Vancouver, BC"),  # NEW
                (r"Calgary\s*,?\s*Alberta", "Calgary, AB"),  # NEW
            ]:
                if re.search(pattern, page_text, re.I):
                    return f"Location: Canada ({city_name})"

            # NEW: Generic "work in Canada" pattern
            if re.search(r"(work|located|based)\s+in\s+canada", page_text, re.I):
                return "Location: Canada (page text indicator)"

        except Exception as e:
            logging.debug(f"Canada page check failed: {e}")

        return None

    @staticmethod
    def _check_url_for_canada(url):
        """ORIGINAL"""
        if not url:
            return None

        url_lower = url.lower()

        if ".ca/" in url_lower or url_lower.endswith(".ca"):
            return "Location: Canada (domain .ca)"

        canada_patterns = [
            "/montreal-quebec-can/",
            "/toronto-ontario/",
            "-ontario-can",
            "/can/",
            "canada/",
            "/vancouver-british-columbia",  # NEW
            "/calgary-alberta",  # NEW
        ]

        if any(p in url_lower for p in canada_patterns):
            return "Location: Canada (from URL path)"

        return None

    @staticmethod
    def clean_location(location):
        """NEW: Comprehensive location cleaning and validation"""
        location = LocationProcessor.normalize_location(location) if location else location
        if not location or location == "Unknown":
            return location

        try:
            from aggregator.config import LOCATION_STOPWORDS, WORK_MODE_KEYWORDS, CURRENCY_CODES
        except (ImportError, AttributeError):
            LOCATION_STOPWORDS = [
                "responsibilities",
                "requirements",
                "remote",
                "hybrid",
            ]
            WORK_MODE_KEYWORDS = ["remote", "hybrid", "onsite"]
            CURRENCY_CODES = ["GBP", "USD", "EUR"]

        original = location

        if location in CURRENCY_CODES:
            currency_map = {"GBP": "UK", "EUR": "Europe", "CAD": "Canada"}
            return currency_map.get(location, "Unknown")

        location_lower = location.lower()
        if any(
            stopword == location_lower.split(",")[0].strip()
            for stopword in LOCATION_STOPWORDS
        ):
            return "Unknown"

        for work_mode in WORK_MODE_KEYWORDS:
            location = re.sub(rf"\b{work_mode}\b,?\s*", "", location, flags=re.I)

        location = re.sub(r"([a-z])([A-Z][a-z])", r"\1, \2", location)

        location = location.strip(", ")


        # Fix state code prefix: "CAFremont, CA" -> "Fremont, CA", "TXBellevue, WA" -> "Bellevue, WA"
        _sp_match = re.match(r"^([A-Z]{2})([A-Z][a-z].+)$", location)
        if _sp_match:
            _prefix, _rest = _sp_match.groups()
            from aggregator.config import validate_us_state_code
            if validate_us_state_code(_prefix):
                location = _rest.strip()
                if not re.search(r",\s*[A-Z]{2}\s*$", location):
                    location = f"{location}, {_prefix}"
        # Fix state code suffix: "HillsboroUS" -> strip trailing country code
        _ss_match = re.match(r"^([A-Za-z ]{3,})(US|CA)$", location)
        if _ss_match and "," not in location:
            location = _ss_match.group(1).strip()
        # Fix common location typos
        location = re.sub(r"\bSt,\s", "St. ", location)  # "St, Louis" → "St. Louis"
        # Fix "WA, DC" → "Washington, DC"
        if location.strip() in ("WA, DC", "WA DC", "WA, D, C,", "WA, D, C", "WA, D. C."):
            location = "Washington, DC"
        # Fix known abbreviations
        _abbrev_fixes = {
            "SFLA": "South Florida",
            "SF": "San Francisco, CA",
            "NYC": "New York, NY",
            "LA": "Los Angeles, CA",
            "DC": "Washington, DC",
            "BOS": "Boston, MA",
            "CHI": "Chicago, IL",
            "ATL": "Atlanta, GA",
            "SEA": "Seattle, WA",
            "DFW": "Dallas, TX",
        }
        if location.strip() in _abbrev_fixes:
            location = _abbrev_fixes[location.strip()]
        # Fix "+1" or "+2" multi-location suffix
        location = re.sub(r"\s*\+\d+\s*$", "", location).strip()
        # Fix "NY City Metropolitan Area (On, site)" → "New York, NY"
        if "ny city metropolitan" in location.lower():
            location = "New York, NY"
        # Fix "(On, site)" or "(On-site)" suffix
        location = re.sub(r"\s*\(On[,\s-]*site\)\s*$", "", location, flags=re.I).strip()
        # Fix concatenated state+city: "MANYC" -> "New York, NY"
        _concat_fixes = {"MANYC": "New York, NY", "Cambridge, MANYC": "Cambridge, MA"}
        if location.strip() in _concat_fixes:
            location = _concat_fixes[location.strip()]
        # Fix "STATE City, Street Address" → "City, STATE"
        import re as _re
        _state_city_match = _re.match(r"^([A-Z]{2})\s+([A-Za-z ]+?)(?:,\s*.+)?$", location)
        if _state_city_match:
            _st, _city = _state_city_match.groups()
            from aggregator.config import validate_us_state_code
            if validate_us_state_code(_st) and len(_city.strip()) > 2:
                location = f"{_city.strip()}, {_st}"


        # Fix iCIMS garbage locations like 's US', 'Location US'
        if location.lower() in ['s us', 'us', 'location us', 'locations us', 'jobs us']:
            location = 'US'
        # Fix leading comma: ', South San Francisco' → 'South San Francisco'
        location = re.sub(r'^[,\s]+', '', location)

        # Reject garbage location text (job descriptions leaking into location field)
        garbage_indicators = ['factors', 'such as', 'responsibilities', 'requirements',
                              'experience', 'qualifications', 'salary', 'benefits',
                              'finding a job', 'not finding', 'not ready', 'ready to apply',
                              'early careers', 'career opportunities', 'all jobs',
                              'office locations', 'locations include', 'include:',
                              'conduct', 'scheduling problems', 'scheduling',
                              'blog', 'press', 'about us', 'join our',
                              'working at', 'work with us', 'come work',
                              'are currently expected', 'currently expected',
                              'delivers multispecialty', 'multispecialty',
                              'delivers ', 'provides ', 'serving ',
                              's not finding', 's include', 's are currently',
                              'internship opportunities', 'apply now',
                              'as required by law', 's as required',
                              'city of wa county', 'city of',
                              ]
        if any(gi in location.lower() for gi in garbage_indicators):
            location = 'Unknown'
        if len(location) > 60:
            location = 'Unknown' 

        if len(location) < 2:
            return "Unknown"

        if location.lower() in LOCATION_STOPWORDS:
            return "Unknown"

        try:
            from aggregator.config import US_STATE_NAME_TO_CODE
        except (ImportError, AttributeError):
            US_STATE_NAME_TO_CODE = {}

        parts = location.split(",")
        if len(parts) == 2:
            city, state = parts[0].strip(), parts[1].strip()
            state_lower = state.lower()
            if state_lower in US_STATE_NAME_TO_CODE:
                state_code = US_STATE_NAME_TO_CODE[state_lower]
                location = f"{city}, {state_code}"

        parts = location.split(",")
        formatted_parts = []
        for part in parts:
            part = part.strip()
            if len(part) == 2 and part.isupper():
                formatted_parts.append(part)
            elif part.isupper() or part.islower():
                formatted_parts.append(part.title())
            else:
                formatted_parts.append(part)

        location = ", ".join(formatted_parts)

        # City→State lookup: if no comma (city without state), try to add state
        if location and "," not in location:
            try:
                from aggregator.config import CITY_TO_STATE
                city_lower = location.lower().strip()
                if city_lower in CITY_TO_STATE:
                    location = f"{location}, {CITY_TO_STATE[city_lower]}"
            except (ImportError, AttributeError):
                pass

        return location


# ============================================================================
# Validation Helpers - ORIGINAL + ENHANCED
# ============================================================================


class ValidationHelper:
    @staticmethod
    @lru_cache(maxsize=512)
    def is_valid_job_url(url):
        """ORIGINAL + NEW blacklist check"""
        if not url or not url.startswith("http"):
            return False, "Invalid URL format"

        url_lower = url.lower()

        # NEW: Check blacklist
        if any(domain in url_lower for domain in BLACKLIST_DOMAINS):
            return False, "Blacklisted domain"

        if "jobright.ai" in url_lower and "/jobs/info/" not in url_lower:
            return False, "Invalid Jobright URL"

        for pattern in [
            "/unsubscribe",
            "/my-alerts",
            "/blog",
            "/terms",
            "/privacy",
            "chromewebstore",
        ]:
            if pattern in url_lower:
                return False, f"{pattern.split('/')[-1].title()} link"

        return True, None

    @staticmethod
    def check_url_for_international(url):
        """FIX 8: checks Canada URL indicators — name kept for backward compat"""
        return LocationProcessor._check_url_for_canada(url)

    @staticmethod
    def check_url_for_canada(url):
        """FIX 8: explicit alias with accurate name"""
        return LocationProcessor._check_url_for_canada(url)

    @staticmethod
    def _check_high_school_only(soup):
        """NEW: Detect high school student requirements"""
        if not soup:
            return None, None

        try:
            from aggregator.config import HIGH_SCHOOL_ONLY_PATTERNS, PAGE_TEXT_FULL_SCAN
        except (ImportError, AttributeError):
            return None, None

        try:
            page_text = soup.get_text()[:PAGE_TEXT_FULL_SCAN].lower()

            for pattern in HIGH_SCHOOL_ONLY_PATTERNS:
                match = re.search(pattern, page_text)
                if match:
                    matched_text = match.group(0)
                    context = page_text[
                        max(0, match.start() - 150) : min(
                            len(page_text), match.end() + 150
                        )
                    ]

                    log_detailed_rejection(
                        "Company",
                        "Title",
                        "High school only",
                        pattern=pattern,
                        matched_text=matched_text,
                        context=context[:200],
                    )
                    return (
                        "REJECT",
                        "High school students only (college students not eligible)",
                    )

        except Exception as e:
            logging.debug(f"High school check failed: {e}")

        return None, None

    @staticmethod
    def _check_permanent_authorization(soup):
        """NEW: Detect permanent US work authorization requirements (excludes F-1 temporary status)"""
        if not soup:
            return None, None

        try:
            from aggregator.config import PERMANENT_US_AUTHORIZATION_PATTERNS, PAGE_TEXT_FULL_SCAN
        except (ImportError, AttributeError):
            return None, None

        try:
            page_text = soup.get_text()[:PAGE_TEXT_FULL_SCAN].lower()

            for pattern in PERMANENT_US_AUTHORIZATION_PATTERNS:
                match = re.search(pattern, page_text)
                if match:
                    matched_text = match.group(0)
                    context = page_text[
                        max(0, match.start() - 150) : min(
                            len(page_text), match.end() + 150
                        )
                    ]

                    log_detailed_rejection(
                        "Company",
                        "Title",
                        "Permanent authorization",
                        pattern=pattern,
                        matched_text=matched_text,
                        context=context[:200],
                    )
                    return (
                        "REJECT",
                        "Requires permanent US work authorization (F-1 temporary status not eligible)",
                    )

        except Exception as e:
            logging.debug(f"Permanent authorization check failed: {e}")

        return None, None

    @staticmethod
    def _check_non_cs_undergraduate_degree(soup):
        """NEW: Detect non-CS undergraduate degree requirements (BSEE, BSME, etc.)"""
        if not soup:
            return None, None

        try:
            from aggregator.config import NON_CS_UNDERGRADUATE_DEGREE_PATTERNS, PAGE_TEXT_FULL_SCAN
        except (ImportError, AttributeError):
            return None, None

        try:
            page_text = soup.get_text()[:PAGE_TEXT_FULL_SCAN].lower()

            for pattern in NON_CS_UNDERGRADUATE_DEGREE_PATTERNS:
                match = re.search(pattern, page_text)
                if match:
                    matched_text = match.group(0)
                    context = page_text[
                        max(0, match.start() - 200) : min(
                            len(page_text), match.end() + 200
                        )
                    ]

                    cs_keywords = [
                        "computer science",
                        "software engineering",
                        "computer engineering",
                        "information technology",
                        "data science",
                        "information systems",
                    ]
                    if any(kw in context for kw in cs_keywords):
                        logging.debug(
                            f"Non-CS degree found but CS also mentioned - accepting"
                        )
                        continue

                    logging.debug(
                        f"Non-CS undergraduate degree requirement: '{matched_text}'"
                    )
                    return (
                        "REJECT",
                        "Requires non-CS undergraduate degree (Electrical/Mechanical Engineering)",
                    )

        except Exception as e:
            logging.debug(f"Non-CS degree check failed: {e}")

        return None, None

    @staticmethod
    def _check_preferred_degree_mismatch(soup):
        """NEW: Detect when preferred degrees list excludes CS/Software"""
        if not soup:
            return None, None

        try:
            from aggregator.config import PREFERRED_DEGREE_MISMATCH_PATTERNS, PAGE_TEXT_FULL_SCAN
        except (ImportError, AttributeError):
            return None, None

        try:
            page_text = soup.get_text()[:PAGE_TEXT_FULL_SCAN].lower()

            for pattern in PREFERRED_DEGREE_MISMATCH_PATTERNS:
                match = re.search(pattern, page_text)
                if match:
                    degree_list = match.group(1).lower()

                    cs_keywords = [
                        "computer",
                        "software",
                        "cs ",
                        "information technology",
                        "it ",
                        "data science",
                    ]
                    has_cs = any(kw in degree_list for kw in cs_keywords)

                    non_cs_keywords = [
                        "mechanical",
                        "electrical",
                        "civil",
                        "aerospace",
                        "chemical",
                        "agricultural",
                    ]
                    has_non_cs = any(kw in degree_list for kw in non_cs_keywords)

                    if has_non_cs and not has_cs:
                        logging.debug(
                            f"Preferred degrees mismatch: '{degree_list}' (no CS/Software mentioned)"
                        )
                        return (
                            "REJECT",
                            "Preferred degrees do not include CS/Software (Mechanical/Electrical Engineering only)",
                        )

        except Exception as e:
            logging.debug(f"Preferred degree check failed: {e}")

        return None, None

    @staticmethod
    def check_salary_requirement(soup):
        """Reject jobs paying under $25/hr. Accept jobs with no salary listed."""
        if not soup:
            return None, None
        try:
            import re as _re
            # Scan full page — salary often appears at bottom of Workday pages
            page_text = soup.get_text()[:15000]
            patterns = [
                r'\$(\d+(?:\.\d+)?)\s*/\s*hr',
                r'\$(\d+(?:\.\d+)?)\s*(?:per\s*hour|/hour)',
                r'pay\s*range[:\s]+\$(\d+(?:\.\d+)?)',
                r'compensation[:\s]+\$(\d+(?:\.\d+)?)',
                r'\$(\d+(?:\.\d+)?)\s*(?:USD|usd)?\s*/\s*(?:hr|hour)',
                r'hourly\s+rate[:\s]+\$(\d+(?:\.\d+)?)',
                r'\$(\d+(?:\.\d+)?)\s*-\s*\$?\d+(?:\.\d+)?\s*/\s*hr',
                r'starting\s+(?:at\s+)?\$(\d+(?:\.\d+)?)\s*/\s*hr',
                r'\$(\d+(?:\.\d+)?)\s*-\s*\$(\d+(?:\.\d+)?)\s*(?:per\s*hour|/hour|usd/hr)',
                r'proposed\s+(?:minimum\s+)?salary[:\s]+\$(\d+(?:\.\d+)?)',
                r'base\s+pay[:\s]+\$(\d+(?:\.\d+)?)',
                r'hourly[:\s]+\$(\d+(?:\.\d+)?)',
                r'pay\s+type[:\s]+hourly.*?\$(\d+(?:\.\d+)?)',
                r'\$(\d+(?:\.\d+)?)\s*(?:USD)?\s*(?:hourly|per\s+hr)',
                r'salary.*?\$(\d+(?:\.\d+)?)\s*(?:to|-|–)\s*\$(\d+(?:\.\d+)?)\s*(?:per\s*hour|/hour|hourly)',
                r'(?:pay|wage|rate).*?\$(\d+(?:\.\d+)?)\s*(?:an?\s+hour|/hr|per\s+hour)',
                r'\$(\d+(?:\.\d+)?)\s*/\s*(?:hour|hr)\s*(?:USD|usd)?',
            ]
            found = []
            for p in patterns:
                for m in _re.finditer(p, page_text, _re.I):
                    try:
                        v = float(m.group(1))
                        if 1 < v < 200:
                            found.append(v)
                    except Exception:
                        pass
            if not found:
                return None, None
            low = min(found)
            high = max(found)
            # Accept if max salary >= $25/hr (pay range like $20-$35 should pass)
            if high >= 25.0:
                return None, None
            if low < 25.0:
                return "REJECT", f"Low salary: ${low:.0f}/hr (minimum $25/hr)"
        except Exception as e:
            logging.debug(f"Salary check failed: {e}")
        return None, None

    @staticmethod
    def check_page_restrictions(soup):
        if not soup:
            return None, None, []

        try:
            clearance_decision, clearance_reason = (
                ValidationHelper._check_clearance_requirements(soup)
            )
            if clearance_decision == "REJECT":
                return clearance_decision, clearance_reason, []

            citizenship_decision, citizenship_reason = (
                ValidationHelper._check_citizenship_requirements(soup)
            )
            if citizenship_decision == "REJECT":
                return citizenship_decision, citizenship_reason, []

            grad_decision, grad_reason = (
                ValidationHelper._check_graduation_requirements(soup)
            )
            if grad_decision == "REJECT":
                return grad_decision, grad_reason, []

            perm_auth_decision, perm_auth_reason = (
                ValidationHelper._check_permanent_authorization(soup)
            )
            if perm_auth_decision == "REJECT":
                return perm_auth_decision, perm_auth_reason, []

            highschool_decision, highschool_reason = (
                ValidationHelper._check_high_school_only(soup)
            )
            if highschool_decision == "REJECT":
                return highschool_decision, highschool_reason, []

            undergrad_decision, undergrad_reason = (
                ValidationHelper._check_undergraduate_only_requirements(soup)
            )
            if undergrad_decision == "REJECT":
                return undergrad_decision, undergrad_reason, []

            non_cs_decision, non_cs_reason = (
                ValidationHelper._check_non_cs_undergraduate_degree(soup)
            )
            if non_cs_decision == "REJECT":
                return non_cs_decision, non_cs_reason, []

            pref_degree_decision, pref_degree_reason = (
                ValidationHelper._check_preferred_degree_mismatch(soup)
            )
            if pref_degree_decision == "REJECT":
                return pref_degree_decision, pref_degree_reason, []

            phd_decision, phd_reason = ValidationHelper._check_phd_only_requirements(
                soup
            )
            if phd_decision == "REJECT":
                return phd_decision, phd_reason, []

            geographic_decision, geographic_reason = (
                ValidationHelper._check_geographic_enrollment_restrictions(soup)
            )
            if geographic_decision == "REJECT":
                return geographic_decision, geographic_reason, []

            cpt_decision, cpt_reason = ValidationHelper._check_cpt_opt_restrictions(
                soup
            )
            if cpt_decision == "REJECT":
                return cpt_decision, cpt_reason, []

            us_person_decision, us_person_reason = (
                ValidationHelper._check_us_person_dod_requirements(soup)
            )
            if us_person_decision == "REJECT":
                return us_person_decision, us_person_reason, []

            degree_decision, degree_reason = (
                ValidationHelper._check_degree_requirements_strict(soup)
            )
            if degree_decision == "REJECT":
                return degree_decision, degree_reason, []

            year_decision, year_reason = (
                ValidationHelper._check_graduation_year_requirements(soup)
            )
            if year_decision == "REJECT":
                return year_decision, year_reason, []

        except Exception as e:
            logging.debug(f"Page restrictions check failed: {e}")

        return None, None, []

    @staticmethod
    def extract_page_age(soup):
        """ENHANCED: Element-based age extraction avoiding header/footer/program dates"""
        if not soup:
            return None


        try:
            from aggregator.utils import DateParser
            from aggregator.config import PAGE_TEXT_STANDARD_SCAN
        except (ImportError, AttributeError):
            return None

        try:
            main_content = soup.find("main") or soup.find(
                "div", class_=re.compile("job.*desc|desc.*job|content", re.I)
            )

            if main_content:
                content_text = main_content.get_text()[:PAGE_TEXT_STANDARD_SCAN]
            else:
                all_text = soup.get_text()
                if len(all_text) > 500:
                    content_text = all_text[
                        200 : min(len(all_text), PAGE_TEXT_STANDARD_SCAN + 200)
                    ]
                else:
                    content_text = all_text

            posting_patterns = [
                (r"posted\s+(\d+\+?)\s*d(?:ays?)?\s*ago", "relative"),
                (r"posted\s+(\d+\+?)\s*mo(?:nth)?s?\s*ago", "relative"),
                (r"posted\s+(today|yesterday)", "relative"),
                (r"posting\s+date[:\s]+([A-Za-z]+\s+\d{1,2},?\s+\d{4})", "absolute"),
                (r"posted\s+on[:\s]+([A-Za-z]+\s+\d{1,2},?\s+\d{4})", "absolute"),
                (r"posted[:\s]+([0-9]{1,2}/[0-9]{1,2}/[0-9]{4})", "absolute"),
            ]

            for pattern, date_type in posting_patterns:
                match = re.search(pattern, content_text, re.I)
                if match:
                    matched_text = match.group(0)

                    context_start = max(0, match.start() - 50)
                    context_end = min(len(content_text), match.end() + 50)
                    context = content_text[context_start:context_end].lower()

                    skip_indicators = [
                        "start date",
                        "program begins",
                        "internship begins",
                        "©",
                        "copyright",
                        "established",
                        "founded",
                    ]
                    if any(indicator in context for indicator in skip_indicators):
                        logging.debug(
                            f"Skipping date in non-posting context: '{matched_text}'"
                        )
                        continue

                    age_days = DateParser.extract_days_ago(matched_text)

                    if age_days is not None:
                        if age_days > MAX_REASONABLE_AGE_DAYS:
                            logging.warning(
                                f"Page age {age_days} exceeds reasonable ({MAX_REASONABLE_AGE_DAYS}) - ignoring"
                            )
                            continue

                        if age_days < 0:
                            logging.warning(f"Negative page age {age_days} - ignoring")
                            continue

                        logging.debug(
                            f"Page age extracted: {age_days} days from '{matched_text}'"
                        )
                        return age_days

            logging.debug("No page age found (acceptable - will not reject)")
            return None

        except Exception as e:
            logging.debug(f"Page age extraction failed: {e}")
            return None

    @staticmethod
    def _check_degree_requirements_strict(soup):
        """ORIGINAL"""
        if not soup:
            return None, None

        try:
            page_text = soup.get_text()[:15000].lower()

            for pattern in [
                r"bachelor'?s?\s+(?:students?|degree|candidates?)\s+only",
                r"undergraduate\s+(?:students?|only)",
                r"undergraduate\s+students?\s+only",
                r"open\s+to\s+undergraduate\s+students?",
                r"for\s+undergraduate\s+students?\s+only",
                r"currently\s+pursuing\s+a?\s+bachelor",
                r"only\s+open\s+to\s+undergraduate",
            ]:
                match = re.search(pattern, page_text, re.I)
                if match:
                    context = page_text[
                        max(0, match.start() - 300) : min(
                            len(page_text), match.end() + 300
                        )
                    ]
                    if not any(
                        any(re.search(rf"\\b{kw}\\b", context) for kw in ["master", "ms/phd", "graduate degree", "grad student", "graduate program", "bs/ms", "ms ", " ms", "ms degree", "pursuing.*ms", "pursuing.*master"])
                    ):
                        return "REJECT", "Bachelor's students only"
        except Exception as e:
            logging.debug(f"Degree requirements check failed: {e}")

        return None, None

    @staticmethod
    def _check_undergraduate_only_requirements(soup):
        """NEW: Detect requirements for current undergraduate enrollment (MS students don't qualify)"""
        if not soup:
            return None, None

        try:
            page_text = soup.get_text()[:15000].lower()

            # Patterns that indicate CURRENT undergrad enrollment required
            undergraduate_patterns = [
                r"active\s+student\s+(?:currently\s+)?obtaining\s+a\s+bachelor",
                r"currently\s+obtaining\s+(?:a\s+|an\s+)?bachelor",
                r"student\s+obtaining\s+(?:a\s+)?(?:bs|ba|b\.s\.|b\.a\.)",
                r"pursuing\s+(?:a\s+)?bachelor'?s?\s+degree",
                r"currently\s+pursuing\s+(?:a\s+)?bachelor'?s?\s+degree",
                r"working\s+towards?\s+(?:a\s+)?bachelor'?s?\s+degree",
                r"rising\s+(?:junior|senior)\s+preferred",
                r"currently\s+pursuing\s+(?:a\s+)?(?:bs|ba|b\.s\.|b\.a\.)\s+(?:degree|in)",
                r"undergraduate\s+(?:junior|senior|sophomore)\s+status",
                r"(?:junior|senior)\s+pursuing\s+(?:a\s+)?bachelor",
                r"bachelor'?s?\s+student\s+enrolled",
                r"pursuing\s+(?:an?\s+)?undergraduate\s+degree",
                r"qualifications\s+include\s+pursuing\s+(?:an?\s+)?undergraduate",
                r"currently\s+enrolled\s+in\s+(?:a\s+)?bachelor",
                r"entering\s+(?:junior|senior)\s+year",
                r"(?:sophomore|junior|senior)\s+standing",
                r"currently\s+a\s+college\s+student",
                r"phd\s+(?:students?\s+)?(?:preferred|required|only)",
                r"3rd\s+year\s+phd\s+and\s+above",
                r"doctoral\s+degree.*required",
                r"must\s+be\s+pursuing\s+a\s+ph\.?d",
                r"\(ph\.?d\.?\)",
                r"ph\.?d\.?\s+intern",
                r"research\s+scientist\s+intern.*(?:audio|speech|vision|nlp|perception)",
                r"20\d\d\s+start\s+\(ph\.?d",
                r"summer\s+2027",
                r"fall\s+2027",
                r"2027\s+start",
                r"start.*2027",
                r"phd\s+internship",
                r"in\s+the\s+process\s+of\s+obtaining.*ph\.?d",
                r"currently\s+has.*ph\.?d\s+degree",
                r"ph\.?d\s+degree\s+in\s+the\s+field\s+of",
                r"(?:requires?|must\s+have).*ph\.?d\s+(?:degree|student|candidate)",
                r"currently\s+pursuing\s+ph\.?d",
                r"preferably\s+a\s+current\s+3rd\s+year",
                r"must\s+be\s+currently\s+pursuing\s+a\s+bachelor",
                # P&G writes "In process of obtaining a Bachelors degree" -
                # no apostrophe, and "in process of" rather than "pursuing".
                # The PhD equivalent was already covered; the bachelor one
                # was not, so an undergrad-only internship reached the sheet.
                r"in\s+(?:the\s+)?process\s+of\s+obtaining\s+(?:a\s+|an\s+)?bachelor",
                r"(?:obtaining|working\s+towards?)\s+(?:a\s+|an\s+)?bachelor'?s?\s+degree",
                r"not\s+open\s+to\s+candidates\s+on\s+opt",
                r"no\s+sponsorship\s+available.*not\s+open\s+to",
                r"actively\s+pursuing.*bachelor.*computer\s+(?:science|engineering)",
                r"must\s+be\s+actively\s+enrolled.*bachelor",
                r"current\s+student\s+pursuing.*bachelor",
                r"students?\s+must\s+be\s+continuing.*degree\s+during",
                r"associate.?s.*or.*bachelor.?s.*degree.*preferred",
                r"enrolled.*associate.?s.*or.*bachelor",
                r"actively\s+enrolled.*associate.?s.*or.*bachelor",
                r"student\s+going\s+into\s+junior\s+or\s+senior\s+year",
                r"rising\s+junior\s+or\s+senior",
                r"scheduled\s+to\s+obtain.*bachelor.*202[678]",
                r"junior\s+or\s+senior.*pursuing.*bachelor",
                r"pursuing\s+bsee",
                r"presently\s+pursuing\s+bsee",
                r"currently\s+pursuing.*bachelor.*computer.*science.*data.*science",
                r"undergraduate\s+students?\s+only",
                r"must\s+be\s+pursuing\s+(?:a\s+)?(?:bs|ba)\b",
                r"enrolled\s+in\s+(?:an?\s+)?undergraduate\s+program",
                r"currently\s+pursuing\s+(?:a\s+)?bachelor",
                r"must\s+be\s+(?:an?\s+)?undergraduate\s+student",
                r"(?:must\s+be\s+)?(?:a\s+)?rising\s+(?:junior|senior)",
                r"entering\s+(?:third|fourth)\s+year",
                r"(?:sophomore|junior|senior)\s+status",
                r"bachelor'?s?\s+candidates?\s+only",
                r"pursuing\s+(?:bs|ba)\s+degree",
                r"current\s+(?:bs|ba)\s+student",
                r"enrolled\s+(?:bs|ba)\s+program",
                r"undergraduate\s+enrollment\s+required",
                r"must\s+be\s+enrolled\s+in\s+bachelor",
                r"bachelor'?s?\s+program\s+enrollment",
                r"currently\s+enrolled\s+in\s+a\s+bachelor'?s?\s+degree\s+program",
                r"enrolled\s+in\s+(?:a\s+)?bachelor'?s?\s+degree\s+program",
                r"(?:junior|senior)\s+year\s+standing",
                r"rising\s+(?:sophomore|junior|senior)",
                r"advancing\s+to\s+(?:their\s+)?(?:junior|senior)\s+year",
                r"at\s+least\s+advancing\s+to\s+(?:their\s+)?(?:junior|senior)",
                r"entering\s+(?:their\s+)?(?:junior|senior)\s+year",
                r"going\s+into\s+(?:their\s+)?(?:junior|senior)\s+year",
                r"currently\s+enrolled\s+college\s+student",
                r"currently\s+enrolled\s+(?:as\s+a\s+)?college\s+student",
                r"enrolled\s+college\s+student",
                r"intern\s+undergraduate",
                r"undergraduate\s+intern",
                r"applicants?\s+considered\s+for.*undergraduate\s+only",
                r"undergraduate\s+students?\s+pursuing",
                r"pursuing\s+(?:a\s+)?bachelor.*computer\s+(?:science|engineering)",
                r"class\s+standing:\s*(?:junior|senior)",
                r"enrolled\s+(?:as\s+a\s+)?(?:full.?time\s+)?student\s+at\s+(?:an?\s+)?accredited\s+(?:four.year|4.year)",
                r"currently\s+enrolled\s+(?:as\s+a\s+)?(?:full.?time\s+)?student.*(?:four.year|4.year|college|university)",
                r"must\s+be\s+(?:a\s+)?(?:current|active|full.?time)\s+student.*(?:four.year|4.year|college|university)",
                r"accredited\s+four.year\s+(?:college|university)",
                r"enrolled.*(?:fulltime|full.time).*(?:college|university).*(?:bachelor|undergraduate|four.year)",
                r"pursuing\s+undergraduate\s+degree",
                r"current\s+undergraduate\s+status",
                r"undergraduate\s+student\s+status",
                r"(?:junior|senior)\s+or\s+senior\s+year",
                r"in\s+their\s+(?:junior|senior)\s+or\s+senior\s+year",
                r"junior\s+or\s+senior\s+year",
                r"junior\s+or\s+senior\s+standing",
                r"must\s+be\s+(?:a\s+)?(?:junior|senior)\s+(?:or\s+senior\s+)?(?:year|student)",
                r"for\s+(?:junior|senior)\s+(?:year\s+)?students\s+only",
                r"bachelor'?s?\s+level\s+student",
                r"undergraduate\s+program\s+student",
                r"enrolled\s+in\s+(?:a\s+)?4-year\s+(?:bachelor|undergraduate)",
                r"pursuing\s+(?:a\s+)?4-year\s+degree",
                r"rising\s+(?:sophomore|junior|senior)\s+(?:year\s+)?(?:student|pursuing|working)",
                r"completed\s+(?:2|3|two|three)\s+years?\s+by\s+start",
                r"must\s+have\s+completed\s+(?:sophomore|junior|senior)\s+year",
                r"entering\s+(?:their\s+)?(?:sophomore|junior|senior)\s+year",
                r"currently\s+enrolled\s+as\s+a\s+(?:junior|senior)\s+pursuing\s+a\s+bachelor",
                r"junior\s+or\s+senior\s+pursuing\s+a\s+bachelor",
                r"(?:must|should)\s+be\s+pursuing\s+(?:their|a)\s+bachelor",
                r"target(?:ed)?\s+majors?.*bachelor",
                r"(?:associate|associates|aa|as)\s+(?:or|and)\s+bachelor",
                r"(?:associate|aa)\s+degree.*only",
                r"no\s+(?:prior\s+)?experience.*bachelor.*program",
                r"graduating.*with\s+(?:a\s+)?(?:ba|bs|ba/bs|bs/ba|b\.s\.|b\.a\.)\b",
                r"with\s+(?:a\s+)?(?:ba/bs|bs/ba)\s*,?\s*majoring",
                r"graduating.*(?:ba|bs|ba/bs|bs/ba)\s*,?\s*majoring",
                r"receive\s+(?:a\s+)?(?:ba|bs|ba/bs)\s+(?:by|before|prior)",
                r"high\s+school\s+diploma.*currently\s+attending\s+(?:a\s+)?college",
            ]

            for pattern in undergraduate_patterns:
                match = re.search(pattern, page_text, re.I)
                if match:
                    # Check context for flexibility (MS/graduate also acceptable)
                    context_start = max(0, match.start() - 500)
                    context_end = min(len(page_text), match.end() + 500)
                    context = page_text[context_start:context_end]

                    # If context mentions graduate/master's, it's flexible
                    if any(
                        bool(re.search(rf"\b{kw}\b", context, re.I))
                        for kw in [
                            "master",
                            "masters",
                            "graduate",
                            "ms/phd",
                            "or graduate",
                            "and graduate",
                            "or master",
                            "master's degree",
                            "masters degree",
                            "ms degree",
                            "or ms",
                            r"m\.s\.",
                            "grad student",
                            "graduate student",
                            "bachelor.*or higher degree",  # "bachelor or higher" means MS accepted
                            "bachelor.*or.*master.*degree",  # explicit bachelor+master
                            "bachelor.*or.*master",
                            "bachelor.*master",
                            "pursuing.*master",
                            "advanced degree",
                        ]
                    ):
                        logging.debug(
                            f"Undergrad pattern matched but graduate/master's also mentioned - accepting"
                        )
                        continue

                    if "senior" in pattern or "junior" in pattern:
                        if "level" in pattern:
                            wider_context = page_text[
                                max(0, match.start() - 50) : min(
                                    len(page_text), match.end() + 50
                                )
                            ]
                            if "student" not in wider_context:
                                logging.debug(
                                    f"Undergrad check: '{pattern}' without 'student' context - likely 'senior engineer'"
                                )
                                continue

                    logging.debug(
                        f"Undergrad-only check: Found '{pattern}' without grad flexibility"
                    )
                    return (
                        "REJECT",
                        "Undergraduate students only (MS students not eligible)",
                    )

        except Exception as e:
            logging.debug(f"Undergraduate-only check failed: {e}")

        return None, None

    @staticmethod
    def _check_phd_only_requirements(soup):
        if not soup:
            return None, None

        try:
            from aggregator.config import (
                ENHANCED_PHD_PATTERNS,
                PHD_MS_FLEXIBILITY_KEYWORDS,
                PAGE_TEXT_FULL_SCAN,
                DEGREE_LIST_PATTERNS,
            )
        except (ImportError, AttributeError):
            ENHANCED_PHD_PATTERNS = []
            PHD_MS_FLEXIBILITY_KEYWORDS = ["master", " ms ", "ms/phd"]
            PAGE_TEXT_FULL_SCAN = 15000
            DEGREE_LIST_PATTERNS = []

        try:
            page_text = soup.get_text()[:PAGE_TEXT_FULL_SCAN].lower()

            for degree_pattern in DEGREE_LIST_PATTERNS:
                degree_matches = list(re.finditer(degree_pattern, page_text))
                for degree_match in degree_matches:
                    list_text = degree_match.group(0)

                    if re.search(r"\bms\b|\bma\b|master'?s?", list_text, re.I):
                        logging.debug(
                            f"PhD check: Degree list found with MS: {list_text[:100]}"
                        )
                        return None, None

            phd_only_patterns = [
                r"\bphd\s+(?:intern|student|candidate|only|required)",
                r"doctoral\s+(?:intern|student|candidate|only)",
                r"(?:pursuing|enrolled\s+in|candidates?\s+in).*\bphd\s+(?:degree|program)",
                r"phd-only",
                r"phd\s+internship",
                r"in\s+the\s+process\s+of\s+obtaining.*ph\.?d",
                r"currently\s+has.*ph\.?d\s+degree",
                r"ph\.?d\s+degree\s+in\s+the\s+field\s+of",
                r"(?:requires?|must\s+have).*ph\.?d\s+(?:degree|student|candidate)",
            ]

            phd_only_patterns.extend(ENHANCED_PHD_PATTERNS)

            for pattern in phd_only_patterns:
                match = re.search(pattern, page_text)
                if match:
                    matched_text = match.group(0)
                    context = page_text[
                        max(0, match.start() - 500) : min(
                            len(page_text), match.end() + 500
                        )
                    ]

                    if not any(kw in context for kw in PHD_MS_FLEXIBILITY_KEYWORDS):
                        requirements_start = page_text.find("qualification")
                        if requirements_start == -1:
                            requirements_start = page_text.find("requirement")
                        requirements_section = (
                            page_text[requirements_start : requirements_start + 2000]
                            if requirements_start != -1
                            else ""
                        )

                        if requirements_section and any(
                            kw in requirements_section
                            for kw in PHD_MS_FLEXIBILITY_KEYWORDS
                        ):
                            logging.debug(
                                f"PhD check: MS found in requirements section"
                            )
                            continue

                        log_detailed_rejection(
                            "Company",
                            "Title",
                            "PhD-only",
                            pattern=pattern,
                            matched_text=matched_text,
                            context=context[:200],
                            debug_info=f"No MS keywords in context or requirements",
                        )
                        return "REJECT", "PhD students only (MS students not eligible)"

        except Exception as e:
            logging.debug(f"PhD check failed: {e}")

        return None, None

    @staticmethod
    def _check_citizenship_requirements(soup):
        """NEW: Detect US citizenship requirements (stricter than sponsorship)"""
        if not soup:
            return None, None

        try:
            page_text = soup.get_text()[:15000].lower()

            # Patterns that indicate citizenship is required
            citizenship_patterns = [
                r"u\.?s\.?\s+citizenship\s+required",
                r"must\s+be\s+(?:a\s+)?u\.?s\.?\s+citizen",
                r"u\.?s\.?\s+citizen\s+or\s+permanent\s+resident",
                r"citizenship\s+requirement",
                r"require(?:s|d)?\s+u\.?s\.?\s+citizenship",
                r"only\s+u\.?s\.?\s+citizens",
                r"u\.?s\.?\s+citizens?\s+only",
                r"must\s+be\s+authorized\s+to\s+work.*(?:without|no)\s+(?:employer\s+)?sponsor",
                r"permanent\s+(?:us\s+)?work\s+authorization\s+required",
                # FIX 3: Trane-style combined citizenship+sponsorship phrase
                r"u\.?s\.?\s+citizen\s+or\s+have\s+the\s+legal\s+right\s+to\s+work",
                r"legal\s+right\s+to\s+work.*without\s+(?:requiring\s+)?sponsor",
                r"without\s+requiring\s+sponsorship\s+now\s+or\s+in\s+the\s+future",
                r"not\s+(?:now|currently).*require.*sponsorship.*future",
                r"no\s+visa\s+sponsorship.*(?:now|future|available)",
                r"students?\s+must\s+be\s+authorized\s+to\s+work\s+in\s+the\s+u\.?s\.?\s+without",
                r"u\.?s\.?\s+citizen\b(?!\s+or\s+permanent)",  # "U.S. Citizen" alone
                r"\bu\.?s\.?\s+citizenship\b",
                r"must\s+be\s+a\s+u\.?s\.?\s+citizen",
                r"candidates?\s+must\s+be\s+u\.?s\.?\s+citizens?",
                r"this\s+role\s+is\s+not\s+eligible\s+for.*sponsor",
                r"not\s+eligible\s+for\s+employer.sponsored\s+work\s+visa",
                r"no\s+employment\s+sponsorship\s+(?:required|available).*(?:now|future)",
                r"employment\s+sponsorship\s+(?:is\s+)?not\s+(?:required|available)",
            ]

            for pattern in citizenship_patterns:
                if re.search(pattern, page_text, re.I):
                    logging.debug(f"Citizenship check: Found requirement pattern")
                    return "REJECT", "US Citizenship required"

        except Exception as e:
            logging.debug(f"Citizenship check failed: {e}")

        return None, None

    @staticmethod
    def _check_graduation_requirements(soup):
        """Reject jobs requiring graduation by Fall 2026 or earlier (user graduates May 2027)."""
        if not soup:
            return None, None
        try:
            page_text = soup.get_text()[:15000].lower()
            grad_patterns = [
                r"graduat(?:e|ing|ion)\s+(?:by|before|no later than)\s+(?:fall|december|dec)\s+2026",
                r"(?:fall|december|dec)\s+2026\s+graduat",
                r"must\s+(?:be\s+)?graduat(?:e|ing)\s+(?:by|in)\s+(?:fall|december|dec)\s+2026",
                r"juniors?,?\s+seniors?,?\s+or\s+master'?s?\s+students?\s+in\s+(?:the\s+)?fall\s+(?:of\s+)?2026",
                r"graduating\s+(?:in\s+)?(?:fall|december)\s+202[0-5]",
                r"ideal\s+intern.*graduat(?:e|ing)\s+by\s+december\s+2026",
                r"complete\s+(?:ms|master|degree)\s+(?:by|in)\s+(?:fall|december|dec)\s+2026",
            ]
            for pattern in grad_patterns:
                if re.search(pattern, page_text, re.I):
                    return "REJECT", "Requires graduation by Fall/Dec 2026 (user graduates May 2027)"
        except Exception:
            pass
        return None, None

    @staticmethod
    def _check_cpt_opt_restrictions(soup):
        if not soup:
            return None, None

        try:
            from aggregator.config import CPT_OPT_EXCLUSION_PATTERNS, PAGE_TEXT_FULL_SCAN
        except (ImportError, AttributeError):
            return None, None

        try:
            page_text = soup.get_text()[:PAGE_TEXT_FULL_SCAN].lower()

            for pattern in CPT_OPT_EXCLUSION_PATTERNS:
                match = re.search(pattern, page_text)
                if match:
                    matched_text = match.group(0)
                    context = page_text[
                        max(0, match.start() - 150) : min(
                            len(page_text), match.end() + 150
                        )
                    ]

                    positive_indicators = [
                        "support cpt",
                        "cpt eligible",
                        "cpt accepted",
                        "welcome cpt",
                        "provide cpt",
                        "offer cpt",
                        "support opt",
                        "opt eligible",
                    ]
                    if any(indicator in context for indicator in positive_indicators):
                        logging.debug(
                            f"CPT/OPT: Skipping positive mention: {matched_text}"
                        )
                        continue

                    log_detailed_rejection(
                        "Company",
                        "Title",
                        "CPT/OPT exclusion",
                        pattern=pattern,
                        matched_text=matched_text,
                        context=context[:200],
                    )
                    return (
                        "REJECT",
                        "Company does not support CPT/OPT (F-1 students not eligible)",
                    )

        except Exception as e:
            logging.debug(f"CPT/OPT check failed: {e}")

        return None, None

    @staticmethod
    def _check_us_person_dod_requirements(soup):
        if not soup:
            return None, None

        try:
            from aggregator.config import (
                US_PERSON_DOD_PATTERNS,
                PAGE_TEXT_FULL_SCAN,
                EXPORT_CONTROL_EXCLUSION_KEYWORDS,
            )
        except (ImportError, AttributeError):
            EXPORT_CONTROL_EXCLUSION_KEYWORDS = ["export control", "export compliance"]

        try:
            page_text = soup.get_text()[:PAGE_TEXT_FULL_SCAN].lower()

            for pattern in US_PERSON_DOD_PATTERNS:
                match = re.search(pattern, page_text)
                if match:
                    matched_text = match.group(0)
                    context_start = max(0, match.start() - 300)
                    context_end = min(len(page_text), match.end() + 300)
                    context = page_text[context_start:context_end]

                    is_export_control = any(
                        keyword in context
                        for keyword in EXPORT_CONTROL_EXCLUSION_KEYWORDS
                    )

                    if is_export_control:
                        logging.debug(
                            f"US Person found in export control context - skipping: '{matched_text}'"
                        )
                        continue

                    log_detailed_rejection(
                        "Company",
                        "Title",
                        "US Person/DoD",
                        pattern=pattern,
                        matched_text=matched_text,
                        context=context[:200],
                    )
                    return "REJECT", "US Person or DoD contract requirement"

        except Exception as e:
            logging.debug(f"US Person/DoD check failed: {e}")

        return None, None

    @staticmethod
    def _check_geographic_enrollment_restrictions(soup):
        if not soup:
            return None, None

        # If page says enrolled at university in the US/United States, that is NOT a geographic restriction
        try:
            quick_text = soup.get_text()[:10000].lower()
            if any(phrase in quick_text for phrase in [
                'university in the united states',
                'college or university in the us',
                'university in the us',
                'accredited college or university in the united',
                'enrolled in an accredited',
            ]):
                return None, None
        except Exception:
            pass

        try:
            from aggregator.config import (
                GEOGRAPHIC_ENROLLMENT_PATTERNS,
                PAGE_TEXT_FULL_SCAN,
                USER_LOCATION,
                USER_STATE,
                USER_COUNTRY,
            )
        except (ImportError, AttributeError):
            USER_STATE = "Massachusetts"
            USER_COUNTRY = "United States"
            USER_LOCATION = "Boston"

        try:
            page_text = soup.get_text()[:PAGE_TEXT_FULL_SCAN].lower()

            for pattern in GEOGRAPHIC_ENROLLMENT_PATTERNS:
                match = re.search(pattern, page_text)
                if match and match.lastindex >= 1:
                    required_location = match.group(1).strip()

                    if not required_location:
                        continue

                    words = required_location.split()
                    if len(words) > 4:
                        continue

                    non_location_words = [
                        "the",
                        "of",
                        "what",
                        "and",
                        "or",
                        "who",
                        "which",
                        "that",
                        "this",
                    ]
                    if any(
                        word in required_location.lower().split()
                        for word in non_location_words
                    ):
                        continue

                    required_normalized = (
                        required_location.lower().replace("/", " ").replace("-", " ")
                    )

                    # Validate captured text is actually a geographic location
                    geo_stopwords = {
                        "organization", "last year", "following", "program", "university",
                        "degree", "field", "company", "team", "department", "within",
                        "accredited", "recognized", "approved", "completed", "enrolled",
                        "pursuing", "graduating", "year", "semester", "quarter",
                    }
                    if any(sw in required_normalized for sw in geo_stopwords):
                        logging.debug(
                            f"Geographic check: '{required_location}' is not a location — skipping"
                        )
                        continue

                    # Must contain at least one real geographic indicator
                    import string
                    has_geo = False
                    # Check US states
                    for state_name in ["massachusetts", "california", "new york", "texas",
                                       "florida", "illinois", "washington", "michigan",
                                       "ohio", "pennsylvania", "georgia", "virginia",
                                       "north carolina", "new jersey", "arizona", "colorado",
                                       "minnesota", "wisconsin", "oregon", "maryland",
                                       "connecticut", "indiana", "missouri", "tennessee"]:
                        if state_name in required_normalized:
                            has_geo = True
                            break
                    # Check 2-letter state codes
                    if not has_geo and re.search(r'\b[A-Z]{2}\b', required_location):
                        code = re.search(r'\b([A-Z]{2})\b', required_location)
                        if code and validate_us_state_code(code.group(1)):
                            has_geo = True
                    # Check city names and region words
                    region_words = ["area", "region", "metro", "bay area", "tri-state",
                                    "detroit", "chicago", "boston", "seattle", "denver",
                                    "atlanta", "houston", "dallas", "phoenix", "portland"]
                    if not has_geo:
                        for rw in region_words:
                            if rw in required_normalized:
                                has_geo = True
                                break

                    if not has_geo:
                        logging.debug(
                            f"Geographic check: '{required_location}' has no recognized location — skipping"
                        )
                        continue

                    if (
                        "united states" in required_normalized
                        and USER_COUNTRY == "United States"
                    ):
                        logging.debug(
                            f"Geographic restriction 'United States' matches user country - accepting"
                        )
                        continue

                    if USER_STATE.lower() in required_normalized:
                        logging.debug(
                            f"Geographic restriction '{required_location}' matches user state - accepting"
                        )
                        continue

                    if USER_LOCATION.lower() in required_normalized:
                        logging.debug(
                            f"Geographic restriction '{required_location}' matches user location - accepting"
                        )
                        continue

                    log_detailed_rejection(
                        "Company",
                        "Title",
                        "Geographic enrollment",
                        pattern=pattern,
                        matched_text=f"Required: {required_location}",
                        debug_info=f"User location: {USER_STATE}, {USER_COUNTRY}",
                    )
                    return (
                        "REJECT",
                        f"Geographic enrollment required: {required_location}",
                    )

        except Exception as e:
            logging.debug(f"Geographic enrollment check failed: {e}")

        return None, None

    @staticmethod
    def _check_clearance_requirements(soup):
        """Check for security clearance requirements"""
        if not soup:
            return None, None

        try:
            page_text = soup.get_text()[:15000].lower()

            clearance_patterns = [
                r"export\s+administration\s+regulations",
                r"ear\s+eligible",
                r"must\s+be\s+ear\s+eligible",
                r"eligible\s+under.*export",
                r"us\s+export\s+control",
                r"itar\s+(?:compliance|eligible|required)",
                r"(?:security\s+)?clearance.*(?:required|preferred)",
                r"must\s+(?:be\s+)?(?:able\s+to\s+)?(?:obtain|get|acquire).*clearance",
                r"(?:eligible|eligibility)\s+for.*(?:security\s+)?clearance",
                r"able\s+to\s+obtain.*clearance",
                r"obtain\s+(?:a\s+)?(?:secret|top\s+secret|ts/sci)\s+clearance",
                r"ability\s+to\s+obtain\s+(?:a\s+)?(?:secret|top)\s+clearance",
                r"clearance\s+(?:eligibility|required|preferred)",
                r"u\.?s\.?\s+citizen.*clearance",
                r"citizenship.*clearance",
                r"dod\s+(?:secret|top\s+secret)",
                r"ts/sci",
                r"polygraph",
                r"ear/itar",
                r"itar.*(?:required|compliance|restriction)",
                r"export\s+control.*(?:required|compliance|restriction)",
                r"access\s+to\s+export.controlled",
                r"export.controlled.*(?:information|technology|data)",
            ]

            for pattern in clearance_patterns:
                if re.search(pattern, page_text, re.I):
                    logging.debug(f"Clearance check: Found requirement")
                    return "REJECT", "Security clearance required"

        except Exception as e:
            logging.debug(f"Clearance check failed: {e}")

        return None, None

    @staticmethod
    def _check_graduation_year_requirements(soup):
        """Check for specific graduation year requirements"""
        if not soup:
            return None, None

        try:
            page_text = soup.get_text()[:15000]

            # Check for flexibility phrases first
            if re.search(
                r"(?:graduation|graduating).*(?:or\s+later|and\s+beyond)",
                page_text,
                re.I,
            ):
                return None, None

            # Remove calendar dates to avoid false positives
            text_cleaned = re.sub(
                r"\b(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},?\s+\d{4}\b",
                "",
                page_text,
                flags=re.I,
            )

            graduation_years = set()

            # Check for year ranges
            range_patterns = [
                r"(?:between\s+)?(\d{1,2})/(\d{4})\s*-\s*(\d{1,2})/(\d{4})",
                r"between\s+(?:[A-Za-z]+\s+)?(\d{4})\s+(?:and|to)\s+(?:[A-Za-z]+\s+)?(\d{4})",  # FIXED: Handles month names
                r"(\d{4})\s+(?:and|to|-)\s+(\d{4}).*(?:graduat|class)",
            ]

            for pattern in range_patterns:
                for match in re.finditer(pattern, text_cleaned, re.I):
                    years = [
                        g for g in match.groups() if g and g.isdigit() and len(g) == 4
                    ]
                    for y in years:
                        year_int = int(y)
                        if 2020 <= year_int <= 2030:
                            graduation_years.add(year_int)

            if graduation_years:
                min_year = min(graduation_years)
                max_year = max(graduation_years)
                if min_year <= 2027 <= max_year:
                    return None, None

            # Check for specific years
            specific_patterns = [
                r"(?:anticipated\s+)?graduation(?:\s+of)?(?:\s+date)?[:\s]+(?:in\s+)?(?:[A-Za-z]+\s+)?(\d{4})",
                r"graduating\s+(?:in\s+)?(?:[A-Za-z]+\s+)?(\d{4})",
                r"class\s+of\s+(\d{4})",
                r"expected\s+graduation[:\s]+(?:[A-Za-z]+\s+)?(\d{4})",
                r"not\s+graduating\s+(?:prior\s+to|before)\s+(?:[A-Za-z]+\s+)?(\d{4})",
            ]

            for pattern in specific_patterns:
                for match in re.finditer(pattern, text_cleaned, re.I):
                    year_str = match.group(1)
                    if year_str and year_str.isdigit():
                        year_int = int(year_str)
                        if 2020 <= year_int <= 2030:
                            graduation_years.add(year_int)

            if not graduation_years:
                return None, None

            if 2027 in graduation_years:
                return None, None

            # FIXED: Reject ONLY if ALL years are > 2027 (wanting future grads only)
            # Accept if ANY year is ≤ 2027 (includes your May 2027 graduation)
            if all(year > 2027 for year in graduation_years):
                years_str = ", ".join(str(y) for y in sorted(graduation_years))
                logging.debug(f"Grad year: {years_str} requires future graduation")
                return "REJECT", f"Graduation year mismatch: {years_str}"

        except Exception as e:
            logging.debug(f"Graduation year check failed: {e}")

        return None, None


    @staticmethod
    def clean_legal_entity(company):
        """ORIGINAL + FIX 5: strip leading The/trailing Companies/Group/Holdings"""
        if not company:
            return company
        try:
            from aggregator.apply_learned import fix_company_slug
            company = fix_company_slug(company)
        except Exception:
            pass
        company = re.sub(r"^\d+\s+|^[A-Z]{2,4}[-\s]", "", company)
        company = re.sub(
            r",?\s+(Inc\.?|LLC\.?|Corp\.?|Ltd\.?)$", "", company, flags=re.I
        ).strip()
        # FIX 5: strip "The X Companies" → "X", "X Holdings" → "X"
        company = re.sub(r"^The\s+", "", company, flags=re.I).strip()
        company = re.sub(
            r"\s+(Companies|Company|Group|Holdings|Partners|Associates|Enterprises|Solutions|Services|Technologies|Technology)$",
            "", company, flags=re.I
        ).strip()
        return company

    @staticmethod
    @lru_cache(maxsize=256)
    def extract_company_from_domain(url):
        """ORIGINAL"""
        if not url:
            return "Unknown"

        try:
            subdomain, domain = extract_domain_and_subdomain(url)
            if domain:
                domain_name = domain.split(".")[0]
                if domain_name in COMPANY_SLUG_MAPPING:
                    return COMPANY_SLUG_MAPPING[domain_name]
                name = domain_name.replace("-", " ")
                # Preserve short all-caps acronyms (WSP, IBM, ABB, KLA)
                if len(name) <= 4 and name.isalpha():
                    return name.upper()
                return name.title()
        except Exception as e:
            logging.debug(f"Domain extraction failed for {url}: {e}")

        return "Unknown"

    @staticmethod
    def check_sponsorship_status(soup, company=""):
        """ORIGINAL + Brain caching"""
        if company:
            try:
                from outreach.brain import Brain
                cached = Brain.get().get_sponsorship(company)
                if cached in ("Yes", "No"):
                    logging.debug(f"Sponsorship from Brain: {company} -> {cached}")
                    return cached
            except Exception:
                pass
        if not soup:
            return "Unknown"

        try:
            page_text = soup.get_text()[:3000]

            if re.search(
                r"(?:will|does|provides?)\s+sponsor|h-?1b.*sponsor|sponsor.*h-?1b|h-?1b\s+visa\s+sponsor", page_text, re.I
            ):
                return "Yes"
            if re.search(r"(?:no|not|doesn\'t|cannot|can\'t|unable\s+to|will\s+not|does\s+not)\s+(?:provide\s+)?(?:visa\s+)?sponsor", page_text, re.I):
                return "No"
            if re.search(r"unable\s+to\s+sponsor\s+or\s+take\s+over\s+sponsorship", page_text, re.I):
                return "No"
            if re.search(r"not\s+able\s+to\s+(?:provide\s+)?(?:visa\s+)?sponsor", page_text, re.I):
                return "No"
            if re.search(r"sponsorship\s+(?:is\s+)?(?:not\s+)?available", page_text, re.I):
                return "No"
        except Exception as e:
            logging.debug(f"Sponsorship check failed: {e}")

        return "Unknown"


# ============================================================================
# Company Extraction - ORIGINAL (ALL 6 METHODS)
# ============================================================================


class CompanyExtractor:
    @staticmethod
    def extract_from_url_mapping(url):
        """ORIGINAL"""
        if not url:
            return ExtractionResult(None, 0.0, "url_mapping")

        try:
            for pattern, company_name in _COMPILED_URL_COMPANY_PATTERNS:
                if pattern.search(url):
                    return ExtractionResult(company_name, 0.95, "url_mapping")
        except Exception as e:
            logging.debug(f"URL company mapping failed: {e}")

        return ExtractionResult(None, 0.0, "url_mapping")

    @staticmethod
    def learn_company_name(url, company_name):
        """Save URL domain→company mapping to brain.json for future use"""
        if not url or not company_name or company_name in ("Unknown", ""):
            return
        try:
            from outreach.brain import Brain
            from urllib.parse import urlparse
            domain = urlparse(url).netloc.lower().replace("www.", "")
            # Take the registrable name, not the first label. "boards" from
            # boards.greenhouse.io and "careers" from careers.stripe.com are
            # subdomains, not companies - the old code learned {'boards':
            # 'Figma'} and skipped Stripe entirely.
            _parts = [p for p in domain.split(".") if p]
            _GENERIC_SUB = {"boards", "job-boards", "jobs", "careers", "apply",
                            "www", "app", "my", "hire", "recruiting", "talent",
                            "wd1", "wd3", "wd5", "wd12", "wd103", "wd108"}
            _named = [p for p in _parts[:-1] if p not in _GENERIC_SUB]
            slug = _named[-1] if _named else (_parts[0] if _parts else "")
            _ats = {"greenhouse", "lever", "workable", "ashbyhq", "rippling",
                    "smartrecruiters", "icims", "taleo", "bamboohr", "jobvite",
                    "myworkdayjobs", "successfactors", "oraclecloud", "adp",
                    "job-boards", "careers", "jobs", "apply", "wd1", "wd3", "wd5"}
            if slug in _ats or len(slug) < 3:
                return
            # Brain exposes _data, not data. This raised AttributeError on
            # EVERY job - 145 swallowed failures in one run - so company-name
            # learning has never once worked. Found by the swallowed-exception
            # counter on its first run.
            b = Brain.get()
            if "learned_company_names" not in b._data:
                b._data["learned_company_names"] = {}
            if slug not in b._data["learned_company_names"]:
                b._data["learned_company_names"][slug] = company_name
                b.save()
                logging.info(f"Learned company: {slug} → {company_name}")
        except Exception as _sw:
            from aggregator.swallowed import swallow as _s; _s('brain.learned_company_names', _sw)

    @staticmethod
    def extract_from_json_ld(soup):
        """ORIGINAL"""
        if not soup:
            return ExtractionResult(None, 0.0, "json_ld")

        try:
            json_ld = soup.find("script", {"type": "application/ld+json"})
            if json_ld:
                data = json.loads(json_ld.string)
                org = (
                    data.get("hiringOrganization", {}) if isinstance(data, dict) else {}
                )
                if isinstance(org, dict) and org.get("name"):
                    name = org["name"]
                    if len(name) < 100:
                        cleaned = ValidationHelper.clean_legal_entity(name)
                        normalized = CompanyNormalizer.normalize(cleaned, "")
                        if normalized and CompanyValidator.is_valid(normalized):
                            return ExtractionResult(normalized, 0.93, "json_ld")
        except Exception as e:
            logging.debug(f"JSON-LD company extraction failed: {e}")

        return ExtractionResult(None, 0.0, "json_ld")

    @staticmethod
    def extract_from_meta_tags(soup):
        """ORIGINAL"""
        if not soup:
            return ExtractionResult(None, 0.0, "meta_tags")

        try:
            meta = soup.find("meta", {"property": "og:site_name"})
            if meta and meta.get("content"):
                company = re.sub(
                    r"\s*[-|]\s*(careers|jobs).*$",
                    "",
                    meta["content"].strip(),
                    flags=re.I,
                )
                if company and len(company) < 50:
                    normalized = CompanyNormalizer.normalize(company, "")
                    if normalized and CompanyValidator.is_valid(normalized):
                        return ExtractionResult(normalized, 0.90, "meta_tags")
        except Exception as e:
            logging.debug(f"Meta tag company extraction failed: {e}")

        return ExtractionResult(None, 0.0, "meta_tags")

    @staticmethod
    def extract_from_visible_elements(soup, url):
        """ORIGINAL"""
        if not soup:
            return ExtractionResult(None, 0.0, "visible_elements")

        try:
            url_lower = url.lower() if url else ""

            if "icims.com" in url_lower:
                mobile_header = soup.find("div", id="mobile-header-container")
                if mobile_header:
                    h1 = mobile_header.find("h1")
                    if h1:
                        normalized = CompanyNormalizer.normalize(
                            h1.get_text().strip(), url
                        )
                        if normalized and CompanyValidator.is_valid(normalized):
                            return ExtractionResult(normalized, 0.88, "visible_icims")

            if "smartrecruiters" in url_lower:
                logo = soup.find("img", alt=re.compile(r"logo", re.I))
                if logo:
                    alt = logo.get("alt", "").replace(" logo", "").strip()
                    if len(alt) > 2:
                        normalized = CompanyNormalizer.normalize(alt, url)
                        if normalized and CompanyValidator.is_valid(normalized):
                            return ExtractionResult(normalized, 0.88, "visible_logo")

            header = soup.find(["header", "nav"])
            if header:
                h1 = header.find("h1")
                if h1:
                    text = h1.get_text().strip()
                    if text and not CompanyExtractor._looks_like_title(text):
                        normalized = CompanyNormalizer.normalize(text, url)
                        if normalized and CompanyValidator.is_valid(normalized):
                            return ExtractionResult(normalized, 0.82, "visible_header")
        except Exception as e:
            logging.debug(f"Visible elements extraction failed: {e}")

        return ExtractionResult(None, 0.0, "visible_elements")

    @staticmethod
    def extract_from_url_path(url, platform):
        """ORIGINAL"""
        if not url:
            return ExtractionResult(None, 0.0, "url_path")

        try:
            path_company = CompanyNormalizer.extract_from_url_path(url, platform)
            if path_company and CompanyValidator.is_valid(path_company):
                if not CompanyValidator.is_junk_subdomain(path_company.lower()):
                    return ExtractionResult(path_company, 0.70, "url_path")
        except Exception as e:
            logging.debug(f"URL path company extraction failed: {e}")

        return ExtractionResult(None, 0.0, "url_path")

    @staticmethod
    def extract_from_subdomain(url):
        """ORIGINAL"""
        if not url:
            return ExtractionResult(None, 0.0, "subdomain")

        try:
            subdomain, _ = extract_domain_and_subdomain(url)

            if subdomain and not CompanyValidator.is_junk_subdomain(subdomain):
                normalized = CompanyNormalizer.normalize(subdomain, url)
                if normalized and CompanyValidator.is_valid(normalized):
                    return ExtractionResult(normalized, 0.40, "subdomain")
        except Exception as e:
            logging.debug(f"Subdomain extraction failed: {e}")

        return ExtractionResult(None, 0.0, "subdomain")

    @staticmethod
    def extract_from_workday(url, soup):
        if not soup or "myworkdayjobs.com" not in url:
            return ExtractionResult(None, 0.0, "workday")

        try:
            title_tag = soup.find("title")
            if title_tag:
                title_text = title_tag.get_text().strip()

                if " - " in title_text:
                    parts = title_text.split(" - ")
                    if len(parts) >= 2:
                        company_part = parts[-1]
                        company_part = re.sub(
                            r"\s*(?:Careers?|Career Site|Jobs?)\s*$",
                            "",
                            company_part,
                            flags=re.I,
                        )
                        company_part = company_part.strip()

                        if (
                            company_part
                            and len(company_part) > 2
                            and len(company_part) < 100
                        ):
                            cleaned = CompanyExtractor.clean_company_name(company_part)
                            if cleaned and cleaned != "Unknown":
                                return ExtractionResult(cleaned, 0.92, "workday_title")

            json_ld = soup.find("script", type="application/ld+json")
            if json_ld:
                data = json.loads(json_ld.string)
                org = (
                    data.get("hiringOrganization", {}) if isinstance(data, dict) else {}
                )
                if isinstance(org, dict) and org.get("name"):
                    name = org["name"]
                    if len(name) < 100:
                        cleaned = CompanyExtractor.clean_company_name(name)
                        if cleaned and cleaned != "Unknown":
                            return ExtractionResult(cleaned, 0.91, "workday_json")

        except Exception as e:
            logging.debug(f"Workday company extraction failed: {e}")

        return ExtractionResult(None, 0.0, "workday")

    @staticmethod
    def clean_company_name(name):
        if not name or name == "Unknown":
            return name

        try:
            from aggregator.config import (
                PORTAL_NAME_INDICATORS,
                LEGAL_ENTITY_SUFFIXES,
                DBA_INDICATORS,
                COMPANY_NORMALIZATIONS,
                COMPANY_NAME_FIXES,
                GARBAGE_COMPANY_NAMES,
            )
        except (ImportError, AttributeError):
            PORTAL_NAME_INDICATORS = []
            LEGAL_ENTITY_SUFFIXES = ["LLC", "Inc.", "Corp.", "Ltd."]
            DBA_INDICATORS = [" DBA ", " d/b/a "]
            COMPANY_NORMALIZATIONS = {}
            COMPANY_NAME_FIXES = {}
            GARBAGE_COMPANY_NAMES = set()

        import html

        name = html.unescape(name)

        # Strip GitHub continuation row prefixes (e.g. "at Atlantic Health System")
        name = re.sub(r"^(?:at|by|for|with|from)\s+", "", name, flags=re.I)
        name = re.sub(r"^[-*#@!|:]+\s*", "", name)

        name = re.sub(r"^[A-Z]{2,6}\d{2,6}\s+", "", name)
        name = re.sub(r"^\d{2}-\d{7}\s+", "", name)
        name = re.sub(r"^US\d+-[A-Z]{2,5}\s+", "", name)
        name = re.sub(r"^[A-Z]{2}\d+\s*-\s*", "", name)
        name = re.sub(r"^[A-Z]{2,6}&[A-Z]{1,3}\s+", "", name)

        for dba in DBA_INDICATORS:
            if dba in name:
                parts = name.split(dba)
                name = parts[-1]

        for suffix_pattern in LEGAL_ENTITY_SUFFIXES:
            escaped = re.escape(suffix_pattern)
            name = re.sub(rf",?\s+{escaped}$", "", name, flags=re.I)

        name = re.sub(
            r"\s*\([^)]*(?:United Kingdom|UK|Canada|International|U\.S\.|USA|[0-9]{4,})\)$",
            "",
            name,
            flags=re.I,
        )

        name = re.sub(
            r"[,_]\s*(?:United States|U\.S\.A\.?|USA|US)$", "", name, flags=re.I
        )

        for indicator in PORTAL_NAME_INDICATORS:
            if indicator in name:
                return "Unknown"

        normalizations = {
            "The Charles Stark Draper Laboratory": "Draper",
            "Bose Corporation, U.S.A": "Bose Corporation",
            "On Location X": "TKO Group Holdings",
            "On Location": "TKO Group Holdings",
        }

        normalizations.update(COMPANY_NORMALIZATIONS)

        name = normalizations.get(name, name)

        if "myworkdayjobs.com" in (name or ""):
            return "Unknown"

        name = name.strip()

        if len(name) > 70:
            company_suffixes = [
                " Company of America",
                " of America",
                " Corporation of America",
                " Holdings Company",
                " Group Holdings",
                " Services Company",
            ]
            for suffix in company_suffixes:
                if name.endswith(suffix):
                    name = name[: -len(suffix)].strip()
                    break

        # ── Self-healing: apply COMPANY_NAME_FIXES + GARBAGE_COMPANY_NAMES ──
        try:
            from aggregator.config import COMPANY_NAME_FIXES, GARBAGE_COMPANY_NAMES
            name_key = name.lower().strip()
            if name_key in GARBAGE_COMPANY_NAMES:
                return "Unknown"
            if name_key in COMPANY_NAME_FIXES:
                fixed = COMPANY_NAME_FIXES[name_key]
                return "Unknown" if fixed == "Unknown" else fixed
        except Exception:
            pass

        return name

    @staticmethod
    @lru_cache(maxsize=256)
    def _looks_like_title(text):
        """ORIGINAL"""
        if not text:
            return False
        text_lower = text.lower()
        if any(p in text_lower for p in ["submit your", "sign in", "apply now"]):
            return True
        # Reject page titles like "ICF Jobs", "RTS Careers", "Company - Careers"
        import re as _tt_re
        if _tt_re.match(r'^[A-Za-z\s]{2,20}\s+(?:Jobs|Careers|Openings|Positions)$', text.strip(), _tt_re.I):
            return True
        return (
            sum(
                1
                for kw in ["intern", "co-op", "engineer", "developer", "software"]
                if kw in text_lower
            )
            >= 2
        )

    @staticmethod
    def extract_all_methods(url, soup):
        platform = PlatformDetector.detect(url)

        workday_result = CompanyExtractor.extract_from_workday(url, soup)

        results = [
            workday_result,
            CompanyExtractor.extract_from_url_mapping(url),
            CompanyExtractor.extract_from_json_ld(soup),
            CompanyExtractor.extract_from_meta_tags(soup),
            CompanyExtractor.extract_from_visible_elements(soup, url),
            CompanyExtractor.extract_from_url_path(url, platform),
            CompanyExtractor.extract_from_subdomain(url),
        ]

        valid_results = [r for r in results if r.value and r.value != "Unknown"]

        if valid_results:
            valid_results.sort(key=lambda r: r.confidence, reverse=True)
            # Strip common suffixes from company names
            for r in valid_results:
                if r.value and r.value.lower().endswith('.jobs'):
                    r.value = r.value[:-5]
                if r.value and r.value.lower().endswith('.com'):
                    r.value = r.value[:-4]
                # Strip legal suffixes
                if r.value:
                    import re as _re
                    # Strip legal suffixes
                    r.value = _re.sub(r"\s*[,.]?\s*(?:Inc\.?|LLC|Corp\.?|Ltd\.?|Co\.?)\s*$", "", r.value, flags=_re.I)
                    r.value = _re.sub(r"\s*\((?:United States|US|USA|UK|Canada|Global)\)\s*$", "", r.value, flags=_re.I)
                    # Strip "inc" embedded at end of company name (e.g. Skyworksinc -> Skyworks)
                    r.value = _re.sub(r"(?i)(\w)(?:inc|llc|corp)$", r"\1", r.value)
                    # Strip career page suffixes
                    r.value = _re.sub(r"\s*(?:Job Board|Careers?|Career Page|Career Site|Jobs?)\s*$", "", r.value, flags=_re.I)
                    # Strip LinkedIn job title leakage
                    r.value = _re.split(r"\s+hiring\s+|\s+in\s+[A-Z][a-z]+,?\s+|\s*\|\s*LinkedIn", r.value)[0]
                    r.value = r.value.strip().strip(",").strip()

            for result in valid_results:
                cleaned = CompanyExtractor.clean_company_name(result.value)
                if cleaned and cleaned != "Unknown":
                    return cleaned

        best_result = ExtractionVoter.vote(
            results, min_confidence=MIN_CONFIDENCE_COMPANY
        )

        if best_result and best_result.value:
            cleaned = CompanyExtractor.clean_company_name(best_result.value)
            if cleaned and cleaned != "Unknown":
                return cleaned

        return ValidationHelper.extract_company_from_domain(url)


# ============================================================================
# Quality Scoring - ORIGINAL
# ============================================================================


class QualityScorer:
    @staticmethod
    def calculate_score(job_data):
        score = 0

        if (
            job_data.get("company") not in [None, "Unknown", "N/A"]
            and len(job_data.get("company", "")) > 2
        ):
            score += 3

        if job_data.get("location") not in [None, "Unknown"]:
            score += 2

        job_id = job_data.get("job_id", "N/A")
        if job_id not in ["N/A", None] and not job_id.startswith("HASH_"):
            score += 1

        title_len = len(job_data.get("title", ""))
        if 15 < title_len < 120:
            score += 1

        return score

    @staticmethod
    def is_acceptable_quality(score, min_score=4):
        return score >= min_score


# ── Fuzzy City Correction ──────────────────────────────────────────────
_COMMON_CITIES = {
    "farmington": "Farmington", "clearwater": "Clearwater",
    "springfield": "Springfield", "binghamton": "Binghamton",
    "pittsburgh": "Pittsburgh", "indianapolis": "Indianapolis",
    "minneapolis": "Minneapolis", "philadelphia": "Philadelphia",
    "albuquerque": "Albuquerque", "sacramento": "Sacramento",
    "jacksonville": "Jacksonville", "san francisco": "San Francisco",
    "los angeles": "Los Angeles", "new york": "New York",
}

def _fuzzy_fix_city(city_str):
    """Fix garbled city names using edit distance against known cities."""
    if not city_str or len(city_str) < 4:
        return city_str
    city_lower = city_str.lower().strip()
    # Direct match
    if city_lower in _COMMON_CITIES:
        return _COMMON_CITIES[city_lower]
    # Edit distance check for short strings
    best_match = None
    best_dist = 3  # max 2 edits
    for known in _COMMON_CITIES:
        if abs(len(known) - len(city_lower)) > 2:
            continue
        dist = _levenshtein(city_lower, known)
        if dist < best_dist:
            best_dist = dist
            best_match = _COMMON_CITIES[known]
    return best_match if best_match else city_str

def _levenshtein(s1, s2):
    """Simple Levenshtein distance."""
    if len(s1) < len(s2):
        return _levenshtein(s2, s1)
    if len(s2) == 0:
        return len(s1)
    prev = range(len(s2) + 1)
    for i, c1 in enumerate(s1):
        curr = [i + 1]
        for j, c2 in enumerate(s2):
            curr.append(min(prev[j + 1] + 1, curr[j] + 1, prev[j] + (c1 != c2)))
        prev = curr
    return prev[len(s2)]
