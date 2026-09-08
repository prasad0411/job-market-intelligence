#!/usr/bin/env python3

import warnings
import os

warnings.filterwarnings("ignore")
os.environ["TF_CPP_MIN_LOG_LEVEL"] = "3"

import logging

logging.getLogger("urllib3").setLevel(logging.ERROR)
logging.getLogger("selenium").setLevel(logging.ERROR)

try:
    import lxml.etree

    LXML_AVAILABLE = True
    DEFAULT_PARSER = "lxml"
except ImportError:
    LXML_AVAILABLE = False
    DEFAULT_PARSER = "html.parser"

try:
    import html5lib

    HTML5LIB_AVAILABLE = True
except ImportError:
    HTML5LIB_AVAILABLE = False

PARSER_CHAIN = []
if LXML_AVAILABLE:
    PARSER_CHAIN.append("lxml")
if HTML5LIB_AVAILABLE:
    PARSER_CHAIN.append("html5lib")
PARSER_CHAIN.append("html.parser")

try:
    from uszipcode import SearchEngine

    US_ZIPCODE_AVAILABLE = True
    _search_engine = SearchEngine()
except Exception as _e:
    logging.debug("suppressed: %s", _e)
    US_ZIPCODE_AVAILABLE = False
    _search_engine = None

try:
    import pycountry

    PYCOUNTRY_AVAILABLE = True
except Exception as _e:
    logging.debug("suppressed: %s", _e)
    PYCOUNTRY_AVAILABLE = False

try:
    import us as us_library

    US_LIBRARY_AVAILABLE = True
except Exception as _e:
    logging.debug("suppressed: %s", _e)
    US_LIBRARY_AVAILABLE = False

try:
    import tldextract

    TLDEXTRACT_AVAILABLE = True
except Exception as _e:
    logging.debug("suppressed: %s", _e)
    TLDEXTRACT_AVAILABLE = False

try:
    from rapidfuzz import fuzz, process

    RAPIDFUZZ_AVAILABLE = True
except Exception as _e:
    logging.debug("suppressed: %s", _e)
    RAPIDFUZZ_AVAILABLE = False

try:
    from dateutil import parser as dateutil_parser

    DATEUTIL_AVAILABLE = True
except Exception as _e:
    logging.debug("suppressed: %s", _e)
    DATEUTIL_AVAILABLE = False

try:
    import validators

    VALIDATORS_AVAILABLE = True
except Exception as _e:
    logging.debug("suppressed: %s", _e)
    VALIDATORS_AVAILABLE = False

try:
    import pgeocode

    PGEOCODE_AVAILABLE = True
    _pgeocode_nomi = pgeocode.Nominatim("us")
except Exception as _e:
    logging.debug("suppressed: %s", _e)
    PGEOCODE_AVAILABLE = False
    _pgeocode_nomi = None

try:
    from unidecode import unidecode as unidecode_func

    UNIDECODE_AVAILABLE = True
except Exception as _e:
    logging.debug("suppressed: %s", _e)
    UNIDECODE_AVAILABLE = False

SHEET_NAME = "H1B visa"
WORKSHEET_NAME = "Valid Entries"

# University-specific roles (require enrollment at specific school)
UNIVERSITY_KEYWORDS = [
    "rector & visitors", "university of virginia", "uva ",
    "mit lincoln", "stanford university", "harvard university",
    "yale university", "princeton university", "columbia university",
    "cornell university", "brown university", "dartmouth college",
    "duke university", "caltech", "carnegie mellon university",
]
DISCARDED_WORKSHEET = "Discarded Entries"
REVIEWED_WORKSHEET = "Reviewed - Not Applied"

SHEETS_CREDS_FILE = os.path.join(".local", "credentials.json")
GMAIL_CREDS_FILE = os.path.join(".local", "gmail_credentials.json")
GMAIL_TOKEN_FILE = os.path.join(".local", "gmail_token.pickle")
JOBRIGHT_COOKIES_FILE = os.path.join(".local", "jobright_cookies.json")
PROCESSED_EMAILS_FILE = os.path.join(".local", "processed_emails.json")
FAILED_SIMPLIFY_CACHE = os.path.join(".local", "failed_simplify_urls.json")
FAILED_URLS_FILE = os.path.join(".local", "failed_urls.json")

SIMPLIFY_URL = "https://raw.githubusercontent.com/SimplifyJobs/Summer2027-Internships/dev/README.md"
VANSHB03_URL = (
    "https://raw.githubusercontent.com/vanshb03/Summer2027-Internships/dev/README.md"
)
SPEEDYAPPLY_SWE_URL = (
    "https://raw.githubusercontent.com/speedyapply/2027-SWE-College-Jobs/main/README.md"
)

# ── New GitHub Sources (Fall 2026 / Spring 2027 / New Grad 2027) ──
SPEEDYAPPLY_AI_URL = (
    "https://raw.githubusercontent.com/speedyapply/2027-AI-College-Jobs/main/README.md"
)
ZAPPLYJOBS_URL = (
    "https://raw.githubusercontent.com/zapplyjobs/New-Grad-Jobs-2027/main/README.md"
)
JOBRIGHT_GITHUB_URL = (
    "https://raw.githubusercontent.com/jobright-ai/2026-Engineer-Internship/master/README.md"
)
SIMPLIFY_OFFSEASON_URL = (
    "https://raw.githubusercontent.com/SimplifyJobs/Summer2027-Internships/dev/README-Off-Season.md"
)
VANSHB03_OFFSEASON_URL = (
    "https://raw.githubusercontent.com/vanshb03/Summer2027-Internships/dev/OFFSEASON_README.md"
)
NEWGRAD_SIMPLIFY_URL = (
    "https://raw.githubusercontent.com/SimplifyJobs/New-Grad-Positions/dev/README.md"
)
NEWGRAD_CVRVE_URL = (
    "https://raw.githubusercontent.com/cvrve/New-Grad/main/README.md"
)
SIMPLIFY_2026_URL = (
    "https://raw.githubusercontent.com/SimplifyJobs/Summer2026-Internships/master/README.md"
)
ZAPPLYJOBS_2026_URL = (
    "https://raw.githubusercontent.com/zapplyjobs/Internships-2026/main/README.md"
)


# ── Missing high-value feeds ──────────────────────────────────────────
# speedyapply publishes FOUR files per repo; we were only reading README.md
# (internships). NEW_GRAD_USA.md is 728 SWE + 397 AI full-time roles that
# were never fetched — and full-time new grad is the actual goal.
SPEEDYAPPLY_SWE_NEWGRAD_URL = (
    "https://raw.githubusercontent.com/speedyapply/2027-SWE-College-Jobs/main/NEW_GRAD_USA.md"
)
SPEEDYAPPLY_AI_NEWGRAD_URL = (
    "https://raw.githubusercontent.com/speedyapply/2027-AI-College-Jobs/main/NEW_GRAD_USA.md"
)
# vanshb03 runs a SEPARATE new-grad repo from the internship one.
VANSHB03_NEWGRAD_URL = (
    "https://raw.githubusercontent.com/vanshb03/New-Grad-2027/dev/README.md"
)
# zapplyjobs: two more US-scoped repos on the same daily pipeline.
ZAPPLYJOBS_IT_URL = (
    "https://raw.githubusercontent.com/zapplyjobs/New-Grad-IT-Jobs-2027/main/README.md"
)
ZAPPLYJOBS_ML_INTERN_URL = (
    "https://raw.githubusercontent.com/zapplyjobs/awesome-ml-internships/main/README.md"
)
ZAPPLYJOBS_INTERNSHIPS_2027_URL = (
    "https://raw.githubusercontent.com/zapplyjobs/Internships-2027/main/README.md"
)


MAX_JOB_AGE_DAYS = 3
MAX_REASONABLE_AGE_DAYS = 365
PAGE_AGE_THRESHOLD_DAYS = 3
MIN_QUALITY_SCORE = 4
MIN_CONFIDENCE_JOB_ID = 0.70
MIN_CONFIDENCE_LOCATION = 0.70
MIN_CONFIDENCE_COMPANY = 0.70
REQUIRE_MULTIPLE_CONFIRMATIONS = True
EMAIL_TRACKING_RETENTION_DAYS = 7

MAX_RETRIES = 3
RETRY_DELAY_SECONDS = 2
BACKOFF_MULTIPLIER = 2

BLACKLIST_DOMAINS = [
    "workatastartup.com",
    "youtube.com",
    "youtu.be",
]

PLATFORM_BLACKLIST = [
    "Sierra Space",
    "RTX",
    "Raytheon",
    "Raytheon Technologies",
    "Northrop Grumman",
    "Lockheed Martin",
    "Leidos",
    "Leidos Defense Systems",
    "General Dynamics Mission Systems",
    "Kaiser Permanente",
    "Parsons",
    "CACI",
    "Voyager Technologies",
    "CAE",
    "Lawrence Livermore National Laboratory (LLNL)",
    "Savannah River National Laboratory",
    "Teledyne Technologies",
    "Teledyne FLIR",
    "Teledyne LeCroy",
]
PLATFORM_BLACKLIST_REASONS = {
    "Sierra Space": "Auto-blacklisted: Security clearance required",
    "RTX": "Company always requires security clearance",
    "Raytheon": "Company always requires security clearance",
    "Raytheon Technologies": "Company always requires security clearance",
    "Northrop Grumman": "Company always requires security clearance",
    "Lockheed Martin": "Company always requires security clearance",
    "Leidos": "Company always requires security clearance",
    "Leidos Defense Systems": "Company always requires security clearance",
    "General Dynamics Mission Systems": "Company always requires security clearance",
    "Kaiser Permanente": "Does not sponsor H1B visa",
    "Parsons": "Auto-blacklisted: Security clearance required",
    "CACI": "Auto-blacklisted: Security clearance required",
    "Voyager Technologies": "Auto-blacklisted: US Citizenship required",
    "CAE": "Auto-blacklisted: Security clearance required",
    "Lawrence Livermore National Laboratory (LLNL)": "Auto-blacklisted: US Person requirement",
    "Savannah River National Laboratory": "Auto-blacklisted: US Citizenship required",
    "Teledyne Technologies": "Always requires US citizenship",
    "Teledyne Scientific & Imaging": "Always requires US citizenship",
    "SAIC": "Company always requires security clearance",
    "Teledyne Scientific": "Always requires US citizenship",
    "Teledyne FLIR": "Always requires US citizenship",
    "Teledyne LeCroy": "Always requires US citizenship",
}

VERBOSE_OUTPUT = False
SHOW_LOADING_STATS = False
SHOW_GITHUB_COUNTS = False

# Self-healing: extract location from Workday job URLs
# Pattern: /job/City-Name-State/ in Workday URLs
URL_LOCATION_EXTRACT_PATTERN = r"/job/([A-Za-z][A-Za-z0-9\-]+(?:\-[A-Z]{2})?)/[^/]+"
# Maps common URL city slugs to "City, ST" format
URL_CITY_STATE_MAP = {
    "new-york": "New York, NY",
    "new-york-ny": "New York, NY",
    "nyc": "New York, NY",
    "san-francisco": "San Francisco, CA",
    "sf": "San Francisco, CA",
    "san-francisco-ca": "San Francisco, CA",
    "seattle-wa": "Seattle, WA",
    "seattle": "Seattle, WA",
    "austin-tx": "Austin, TX",
    "austin": "Austin, TX",
    "chicago-il": "Chicago, IL",
    "chicago": "Chicago, IL",
    "boston-ma": "Boston, MA",
    "boston": "Boston, MA",
    "atlanta-ga": "Atlanta, GA",
    "atlanta": "Atlanta, GA",
    "dallas-tx": "Dallas, TX",
    "dallas": "Dallas, TX",
    "denver-co": "Denver, CO",
    "denver": "Denver, CO",
    "los-angeles": "Los Angeles, CA",
    "santa-clara-ca": "Santa Clara, CA",
    "santa-clara": "Santa Clara, CA",
    "san-jose-ca": "San Jose, CA",
    "san-jose": "San Jose, CA",
    "menlo-park": "Menlo Park, CA",
    "palo-alto": "Palo Alto, CA",
    "mountain-view": "Mountain View, CA",
    "sunnyvale-ca": "Sunnyvale, CA",
    "sunnyvale": "Sunnyvale, CA",
    "redwood-city": "Redwood City, CA",
    "bellevue-washington": "Bellevue, WA",
    "bellevue-wa": "Bellevue, WA",
    "chandler-arizona": "Chandler, AZ",
    "chandler-az": "Chandler, AZ",
    "pittsburgh-pa": "Pittsburgh, PA",
    "pittsburgh": "Pittsburgh, PA",
    "phoenix-az": "Phoenix, AZ",
    "phoenix": "Phoenix, AZ",
    "reston-va": "Reston, VA",
    "falls-church-va": "Falls Church, VA",
    "mclean-va": "McLean, VA",
    "herndon-va": "Herndon, VA",
    "niskayuna": "Niskayuna, NY",
    "longmont-co": "Longmont, CO",
    "remote": "Remote",
    "remote-us": "Remote",
    "usa-remote": "Remote",
    "us-remote": "Remote",
    "united-states-remote": "Remote",
    "work-from-home": "Remote",
    "milpitas-ca": "Milpitas, CA",
    "milpitas": "Milpitas, CA",
    "plymouth-mi": "Plymouth, MI",
    "northville-mi": "Northville, MI",
    "san-diego-ca": "San Diego, CA",
    "san-diego": "San Diego, CA",
    "minneapolis-mn": "Minneapolis, MN",
    "minneapolis": "Minneapolis, MN",
    "houston-tx": "Houston, TX",
    "houston": "Houston, TX",
    "overland-park-kansas": "Overland Park, KS",
}

USER_LOCATION = "Boston"
USER_STATE = "Massachusetts"
USER_COUNTRY = "United States"

REPROCESS_EMAILS_DAYS = 4
EMAIL_DATE_FILTER_ENABLED = False

PAGE_TEXT_QUICK_SCAN = 2000
PAGE_TEXT_STANDARD_SCAN = 5000
PAGE_TEXT_FULL_SCAN = 15000

JOB_ID_PREFERENCES = {
    "hash_fallback_enabled": False,
    "fallback_value": "N/A",
    "require_digit": True,
    "minimum_confidence": 0.70,
}

DATA_SANITIZATION_PREFERENCES = {
    "remove_emojis": True,
    "normalize_unicode": True,
    "decode_html_entities": True,
    "strip_field_prefixes": True,
    "trim_whitespace": True,
    "standardize_location_format": True,
    "validate_garbage_locations": True,
    "normalize_sponsorship_values": True,
}

FIELD_PREFIXES_TO_REMOVE = [
    "Title:",
    "Company:",
    "Location:",
    "Position:",
    "Role:",
    "Job:",
    "locations",
    "location ",
]

GARBAGE_LOCATION_PATTERNS = [
    "experience, and",
    "experience and",
    "salary",
    "compensation",
    "nearest major market",
    "multiple locations",
]

INTERNSHIP_INDICATORS = [
    "intern",
    "co-op",
    "coop",
    "apprentice",
    "apprenticeship",
    "emerging talent",
    "fellowship",
    "fellow",
    "trainee",
    "student program",
    "early career program",
    "rotational program",
    "data science",
]

VALID_INTERNSHIP_TYPES = [
    "Internship",
    "Co-op",
    "Fellowship",
    "Apprenticeship",
    "Trainee",
]

GRADUATE_PROGRAM_PATTERNS = [
    r"graduate.*202[6-9]",
    r"graduate.*program.*(?:intern|summer)",
    r"graduate.*(?:intern|co-op)",
    r"(?:masters|ms).*202[6-9]",
    r"(?:ms|master).*(?:intern|graduate)",
]

DURATION_INTERNSHIP_PATTERNS = [
    r"\b(?:10|12|8)\s*[-–]?\s*week",
    r"\b(?:3|6|12)\s*[-–]?\s*month",
    r"june\s*(?:through|to|-|–)\s*august",
    r"may\s*(?:through|to|-|–)\s*august",
    r"summer\s*202[6-9]",
    r"(?:start|begin).*(?:june|may|august)\s*202[6-9]",
    r"(?:temporary|fixed[\s-]term)\s*position",
    r"internship\s+(?:duration|program|period)",
]

ENROLLMENT_PATTERNS = [
    r"must\s+be\s+(?:currently\s+)?enrolled",
    r"currently\s+pursuing.*degree",
    r"(?:pursuing|enrolled\s+in).*(?:bachelor|master|degree)",
    r"graduating.*202[6-9]",
    r"expected\s+graduation.*202[6-9]",
    r"graduation\s+date.*202[6-9]",
]

CONFLICTING_SIGNAL_PATTERNS = [
    r"full[\s-]time\s+(?:position|role|opportunity|employee)",
    r"permanent\s+(?:position|role)",
    r"(?:new|recent)\s+grad(?:uate)?s?\s+(?:welcome|encouraged)",
]

ASSOCIATE_BACHELOR_ONLY_PATTERNS = [
    r"(?:associate|associates|aa|as)\s+(?:or|and)\s+bachelor",
    r"(?:associate|aa)\s+degree.*only",
    r"no\s+(?:prior\s+)?experience.*bachelor.*program",
    r"bachelor.*program\s+(?:required|only)",
    r"entering.*(?:junior|senior)\s+year",
    r"(?:junior|senior)\s+year\s+(?:preferred|required|students?)",
    r"(?:sophomore|junior)\s+(?:or|and)\s+(?:junior|senior)",
    r"at\s+least\s+(?:a\s+)?(?:sophomore|junior)",
    r"minimum.*sophomore",
    r"completed.*sophomore\s+year",
    r"(?:rising|entering)\s+(?:junior|senior)",
    r"graduate.*202[67].*between.*(?:junior|senior)",
    r"summer\s+between.*(?:junior|senior)\s+year",
    r"currently\s+enrolled.*pursuing.*bachelor'?s?\s+degree",
    r"enrolled.*bachelor'?s?\s+(?:degree\s+)?program",
    r"actively\s+enrolled.*bachelor'?s?\s+program",
    r"student\s+pursuing.*bachelor'?s?",
    r"pursuit\s+of.*bachelor'?s?\s+degree",
    r"bachelor'?s?\s+degree\s+program.*enrollment",
    r"pursuing.*(?:an?\s+)?undergraduate'?s?\s+degree",
    r"enrolled.*undergraduate'?s?\s+(?:degree|program)",
    r"undergraduate'?s?\s+degree.*(?:required|program)",
    r"(?:in\s+)?(?:an?\s+)?undergraduate'?s?\s+degree",
    r"associate'?s?\s+or\s+bachelor'?s?\s+degree",
    r"pursuing.*associate'?s?\s+or\s+bachelor'?s?",
    r"senior\s+level\s+student",
    r"junior\s+level\s+student",
    r"(?:junior|senior)-level\s+student",
    r"junior\s+(?:or|and|to)\s+senior\s+level",
    r"senior.*graduating.*(?:summer|spring|may|june|202[67])",
    r"(?:junior|senior).*graduating.*(?:this\s+)?(?:summer|spring)",
    r"currently.*working.*towards.*bachelor'?s?",
    r"rising.*(?:junior\s+or\s+senior|senior\s+or\s+junior)",
    r"enrolled.*(?:in\s+)?(?:an?\s+)?undergraduate.*(?:engineering|program)",
    r"enrolled\s+in\s+an?\s+accredited\s+undergraduate",
    r"toward\s+a?\s+(?:bsee|bsce|bsme|bscs|bsae)",
    r"working\s+towards?\s+a?\s+(?:bsee|bsce|bsme|bsae)",
    r"completion\s+of\s+.*undergraduate\s+education\s+toward",
    r"final-year\s+undergraduate",
    r"currently\s+a\s+college\s+student.*3rd\s+year",
    r"preferably\s+a\s+current\s+3rd\s+year",
    r"bachelor.?s\s+degree.*(?:computer\s+engineering|computer\s+science).*required",
    r"must\s+be\s+(?:actively\s+)?enrolled.*bachelor",
    r"pursuing.*bachelor.?s.*(?:computer\s+engineering|computer\s+science|data\s+analysis)",
    r"no\s+sponsorship\s+available.*otp",
    r"not\s+open\s+to\s+candidates\s+on\s+opt",
    r"(?:third|3rd).*(?:or|and|-).*(?:fourth|4th).*year",
    r"(?:3rd|4th)\s+year.*(?:or|and)\s+(?:recent\s+)?graduate",
    r"senior\s+standing,?\s+(?:may|june|spring|summer)\s+202[67]",
    r"pursuing.*(?:ba|bs)/(?:ba|bs)\s+degree",
    r"currently.*(?:ba|bs)\s+degree",
    r"pursuing.*(?:bsee|bsce|bsme|bsae|bsie)\b",
    r"enrolled.*(?:bsee|bsce|bsme|bsae)\s+(?:or|program)",
    r"enrollment.*(?:4-year|four.year).*(?:university|college)",
    r"enrolled.*(?:4-year|four.year).*(?:university|college).*(?:pursuing|technology|degree)",

]

CPT_OPT_EXCLUSION_PATTERNS = [
    r"will\s+not\s+(?:provide|offer|support|sign).{0,80}(?:cpt|opt|curricular\s+practical|optional\s+practical)",
    r"(?:does\s+not|doesn't|cannot)\s+(?:support|provide|sponsor).{0,80}(?:cpt|opt)",
    r"(?:\bcpt\b|\bopt\b|curricular\s+practical|optional\s+practical).{0,80}(?:not|n't|cannot).{0,50}(?:support|provide|available|offered)",
    r"no.{0,30}(?:assistance|support|documentation).{0,50}(?:for|with|regarding).{0,30}(?:cpt|opt)",
    r"will\s+not.*sign.*documentation.{0,50}(?:cpt|opt)",
    r"(?:\bcpt\b|\bopt\b).{0,50}not\s+(?:available|supported|provided|offered)",
    r"not\s+eligible.{0,30}(?:for|under).{0,30}(?:cpt|opt)",
    r"visa.*not\s+available.{0,200}f-1.{0,100}(?:cpt|opt|ead)",
    r"not\s+available.{0,150}(?:includes|including).{0,100}f-1.{0,50}(?:cpt|opt|ead)",
    r"sponsorship.*not\s+available.{0,200}(?:includes|including).{0,100}(?:cpt|opt|f-1)",
]

GEOGRAPHIC_ENROLLMENT_PATTERNS = [
    r"enrolled\s+at.*(?:college|university).*in\s+(?:the\s+)?([A-Za-z\s/]+)\s+area",
    r"must\s+be\s+enrolled.*in\s+([A-Za-z\s/]+).*to\s+be\s+considered",
    r"attend.*(?:college|university).*(?:within|in)\s+([A-Za-z\s/]+)",
    r"(?:college|university).*in\s+the\s+([A-Za-z\s/]+).*(?:area|region)",
]

HIGH_SCHOOL_ONLY_PATTERNS = [
    r"high\s+school\s+(?:student|senior|graduate)",
    r"graduating\s+(?:from\s+)?high\s+school",
    r"on\s+track\s+to\s+graduating\s+high\s+school",
    r"current(?:ly)?\s+(?:in\s+)?high\s+school",
    r"must\s+be.*high\s+school",
    r"high\s+school.*plans\s+to\s+attain",
]

PERMANENT_US_AUTHORIZATION_PATTERNS = [
    r"permanent.*(?:us|united\s+states|u\.s\.).*(?:work|employment)\s+authorization",
    r"requisite.*permanent.*(?:work|employment).*authorization",
    r"must\s+(?:have|possess).*permanent.*(?:right|authorization)\s+to\s+work",
    r"permanently\s+authorized\s+to\s+work",
    r"permanent.*(?:right|ability)\s+to\s+work.*(?:in\s+the\s+)?(?:us|united\s+states)",
    r"not\s+(?:currently\s+)?hiring\s+foreign\s+national\s+applicants.*(?:sponsorship|H,\s*L,\s*TN,\s*F)",
    r"not\s+(?:currently\s+)?(?:hiring|sponsoring).*foreign\s+national.*(?:H|L|TN|F|J|E|O)",
    # Removed: r"visa sponsorship is not available" — too broad, catches companies
    # that just won't sponsor H-1B post-graduation, which is fine for F-1 CPT internships
]

US_PERSON_DOD_PATTERNS = [
    r"\bus\s+person\b",
    r"u\.s\.\s+person",
    r"united\s+states\s+person",
    r"\bdod\b",
    r"department\s+of\s+defense",
    r"dod\s+contract",
    r"defense\s+contract",
]

# These patterns trigger skip when found in job page text
# Added to SPONSORSHIP_REJECT_PATTERNS so they are checked automatically
EXPORT_CONTROL_EXCLUSION_KEYWORDS = [
    "export control",
    "export compliance",
    "export regulations",
    "itar",
    "ear",
    "immigration & nationality act",
    "defined by",
    "classified as",
    "u.s. export control",
    "export-controlled",
    "for export compliance",
    "eligible for any required authorizations",
    "eligible for authorizations from",
    "to conform to u.s. export control",
    "export-controlled commodities and technology",
]

ENHANCED_PHD_PATTERNS = [
    # ── "PhD in <field>" — the commonest phrasing, previously uncovered ──
    # Every other pattern requires PhD to be followed by degree/program/
    # intern/student/candidate, so "pursuing a PhD in computer science",
    # "must have a Ph.D. in a related field" and "requires a PhD in ML" all
    # slipped through and PhD-only roles reached the sheet.
    # MS-eligible roles stay safe: DEGREE_LIST_PATTERNS returns early when a
    # degree list mentions MS, and PHD_MS_FLEXIBILITY_KEYWORDS checks +/-500
    # chars around any match. Verified against both, 12/12 cases.
    r"(?:pursuing|obtaining|working\s+towards?)\s+(?:a\s+|an\s+)?ph\.?\s?d\.?\s+in\b",
    r"(?:requires?|must\s+have|should\s+have)\s+(?:a\s+|an\s+)?ph\.?\s?d\.?\s+in\b",
    r"\bph\.?\s?d\.?\s+(?:candidates?|applicants?)\s+only\b",

    r"working\s+towards\s+a?\s*phd",
    r"phd\s+level\s+degree",
    r"working\s+toward\s+a?\s*phd",
    r"current\s+phd\s+student",
    r"currently.*phd\s+student",
    r"active\s+phd\s+candidate",
    r"must\s+be\s+pursuing.*phd.*\(enrolled",
    r"criteria:?.*pursuing.*phd",
    r"one\s+of.*following.*phd",
    r"enrolled.*phd\s+student",
    r"phd-level\s+student",
    r"ongoing\s+ph\.?d\.?",
    r"current\s+ph\.?d\.?",
    r"active\s+ph\.?d\.?",
    r"ph\.?d\.?\s+(?:student|candidate|intern)",
    r"pursuing.*ph\.?d\.?\s+(?:degree|program)",
]

DEGREE_LIST_PATTERNS = [
    r"(?:pursuing|currently\s+in|degree\s+in|enrolled\s+in).{0,80}(?:ba|bs|ms|ma|phd|ph\.d\.).{0,50}(?:ba|bs|ms|ma|phd|ph\.d\.|or|and|,)",
    r"(?:bachelor|master|doctoral|phd|ph\.d\.).{0,50}(?:or|and|,).{0,50}(?:bachelor|master|phd|ph\.d\.)",
    r"(?:ba|bs|ms|ma|phd|ph\.d\.)[\s,/]+(?:ba|bs|ms|ma|phd|ph\.d\.)",
]

PHD_MS_FLEXIBILITY_KEYWORDS = [
    "master",
    " ms ",
    "ms/phd",
    "or master",
    "master's",
    "graduate students",
    "advanced degree students",
    "masters",
    "m.s.",
    "ms degree",
    "ms students",
]

NON_CS_UNDERGRADUATE_DEGREE_PATTERNS = [
    r"pursuing.*(?:bsee|bsme|bsce|bsae|bsie)\b",
    r"enrolled.*(?:bsee|bsme|bsce|bsae)\s+(?:or|program)",
    r"bachelor.*(?:electrical\s+engineering|mechanical\s+engineering|civil\s+engineering|aerospace\s+engineering)",
    r"degree.*preferred:\s*(?:mechanical|electrical|civil|aerospace)(?:\s+engineer)",
]

PREFERRED_DEGREE_MISMATCH_PATTERNS = [
    r"degrees?\s+preferred:\s*([^.]+)",
    r"preferred\s+(?:majors?|degrees?):\s*([^.]+)",
    r"(?:majoring|degree)\s+in:\s*([^.]+)\s+preferred",
]

INVALID_TITLE_KEYWORDS = [
    # MBA-only roles
    r"\bmba\b(?!.*(?:software|swe|engineer|data|ml|ai))",
    # Non-CS engineering roles
    r"project\s+engineer\s+intern",
    r"process\s+engineer\s+intern",
    r"product\s+engineer\s+intern(?!.*software)",
    r"yard\s+engineer",
    r"\bmba\s+(?:intern|summer|associate|fellow)",
    r"\bmba\s+candidate",
    r"solidworks?",
    r"autocad",
    r"\bgd[&]t\b",
    r"geometric\s+dimensioning",
    r"\bfmea\b",
    r"finite\s+element",
    r"\bfea\b",
    r"free\s+body\s+diagram",
    r"biomedical\s+engineer",
    r"mechanical\s+design\s+tool",
    r"opto[- ]?mechanical",
    r"optomechanical",
    r"hardware\s+engineering\s+(?:intern|co-op|coop)",
    r"hardware\s+engineer\s+(?:intern|co-op|coop)",
    r"\baosp\b",
    r"android\s+(?:aosp|platform|bsp|hal\b)",
    r"\bhal\b.*(?:intern|engineer)",
    r"\bbsp\b.*(?:intern|engineer)",
    r"hardware\s+abstraction\s+layer",
    r"board\s+support\s+package",
    r"hardware\s+engineering\s+(?:intern|co-op|coop)",
    r"hardware\s+engineer\s+(?:intern|co-op|coop)",
    r"military.*veteran",
    r"veteran.*military",
    r"\bphd\b.*intern",
    r"\bphd\b.*residency",
    r"residency.*machine\s+learning",
    r"waste\s+characterization",
    r"intern.*\bphd\b",
    r"ph\.d\..*intern",
    r"intern.*ph\.d\.",
    # FIX 2: hardware/optics/photonics/laser/materials/SkillBridge roles
    r"laser\s+(?:application|engineer|technician|optic)",
    r"optic(?:al|s)?\s+(?:engineer|intern|technician)",
    r"opto[- ]?mechanical\s+(?:engineer|intern|system|design)",
    r"optomechanical\s+(?:engineer|intern)",
    r"photonics?\s+(?:engineer|intern|technician|design)",
    r"materials?\s+science\s+(?:intern|engineer|co-op)",
    r"mechanical\s+engineer(?:ing)?\s+(?:intern|co-op)",
    r"supplier\s+engineer(?:ing)?\s+(?:intern|co-op)",
    r"supplier\s+development\s+(?:intern|co-op)",
    r"project\s+excellence\s+(?:intern|co-op)",
    r"manufacturing\s+engineer(?:ing)?\s+(?:intern|co-op)",
    r"product\s+development\s+co.?op",
    r"fleet\s+management\s+intern",
    r"condition\s+based\s+maintenance",
    r"facilities\s+(?:intern|co-op)",
    r"operations\s+(?:intern|co-op).*maintenance",
    r"university\s+research\s+intern",
    r"campus\s+research\s+intern",
    r"vehicle\s+safety.*intern",
    r"safety.*testing\s+intern",
    r"process\s+engineer(?:ing)?\s+(?:intern|co-op)",
    r"plasma\s+etch\s+(?:intern|engineer)",
    r"(?:intern|co-op).*laser\s+application",
    r"robotics\s+(?:engineer|software|hardware|intern|co-op)",
    r"adas\s+hardware",
    r"chemical\s+engineer",
    r"field\s+engineer\s+apprentice",
    r"biomedical\s+technician",
    r"optical\s+systems\s+engineer",
    r"applications\s+systems\s+engineer",
    r"instrument\s+control",
    r"construction\s+data",
    r"corporate\s+forecasting",
    r"commercial\s+product.*intern",
    r"ad\s+sales.*intern",
    r"return.*recommerce.*economics",
    r"marketing\s+analytics\s+intern",
    r"refining.*(?:intern|co.op)",
    r"(?:fp&a|financial.*planning|finance.*intern)",
    r"sharepoint.*(?:intern|design)",
    r"quality\s+assurance\s+intern",
    r"insurance.*intern",
    r"military\s+pathways",
    r"radio\s+frequency.*intern",
    r"\brf\b.*engineer",
    r"rf\s+engineer",
    r"radio\s+frequency\s+engineer",
    r"\brf\b.*engineer.*intern",
    r"computer\s+vision.*intern",
    r"geospatial.*intern",
    r"hardware.*intern",
    r"broadcast\s+(?:journalism|technology|engineering)\s+(?:intern|co-op)",
    r"video\s+production\s+(?:intern|co-op)",
    r"communications\s+(?:intern|major|degree)\s+(?:intern|co-op)",
    r"product\s+management.*(?:broadcast|journalism|media\s+production)",
    r"engineer,\s+hardware",
    r"electromechanical.*intern",
    r"test\s+engineering.*intern",
    r"rsd\s+intern",
    r"r&d\s+engineering.*intern",
    r"master\s+data.*intern",
    r"procurement.*intern",
    r"specialty\s+coatings",
    r"computational\s+materials",
    r"wearables\s+prototype",
    r"electrical\s+machines.*testing",
    r"hr\s+ai\s+analytics",
    r"student\s+hourly\s+assistant",
    r"quantitative\s+technologist",
    r"quantitative\s+research.*intern",
    r"product\s+strategist.*intern",
    r"it\s+product\s+management",
    r"alternative\s+delivery.*analytics",
    r"nissc",
    r"skillbridge",
    r"veteran.*program",
    r"revops.*intern",
    r"pmo.*intern",
    r"people\s+data\s+intern",
    r"failure\s+analysis.*intern",
    r"instrumentation\s+engineer.*co.op",
    r"test\s+development\s+intern",
    r"aura\s+frames",
    r"hardware\s+optics",
    r"hardware\s+electronics\s+(?:intern|co-op)",
    r"non-civil\s+service",
    r"civil\s+service\s+position",
    r"returning\s+interns?\s+only",
    r"skillbridge",
    r"dod\s+skillbridge",
    r"(?:robot|robotic)\s+(?:systems?|engineer|software)\s+(?:intern|co-op)",
    r"autonomous\s+(?:vehicle|driving|robot)\s+(?:intern|co-op)",
    r"rotating\s+machine",
    r"hvac\s+(?:intern|engineer|technician)",
    r"refrigeration\s+(?:intern|engineer)",
    r"skillbridge",
    r"dod\s+skillbridge",
    r"skill\s*bridge",
    r"\bsales\s+(?:intern|internship|co-op|coop)",
    r"\bsales\s+(?:development|representative)\s+intern",
    r"rotational\s+(?:program|engineer|developer)(?!.*intern)",
    r"rotation\s+program(?!.*intern)",
    r"\bmarketing\s+(?:intern|internship)(?!.*(?:analytics|data|tech|engineer))",
    r"\brecruiting\s+(?:intern|internship)",
    r"\bhr\s+(?:intern|internship)",
    r"\bhuman\s+resources\s+(?:intern|internship)",
    r"\baccounting\s+(?:intern|internship)",
    r"\blegal\s+(?:intern|internship)",
    r"\bnursing\s+(?:intern|internship)",
    r"\bclinical\s+(?:intern|internship)(?!.*(?:data|software|engineer|analyst))",
    r"\bgraphic\s+design\s+(?:intern|internship)",
    r"\beditorial\s+(?:intern|internship)",
    r"\bcopywriter\s+(?:intern|internship)",
    r"\breal\s+estate\s+(?:intern|internship)",
    r"\binsurance\s+(?:agent|broker)\s+(?:intern|internship)",
]

INTERNATIONAL_URL_INDICATORS = [
    ".co.uk",
    ".uk",
    "/uk/",
    "/gb/",
    "-uk-",
    "-gb-",
    "/gbp/",
    "-gbp-",
    "/sheffield-gbp/",
    ".ca/",
    "/canada/",
    "/canadian/",
    ".com.au",
    ".au",
    "/australia/",
    ".de",
    "/germany/",
    "/deutschland/",
    ".fr",
    "/france/",
    ".in",
    "/india/",
    ".sg",
    "/singapore/",
]

INTERNATIONAL_TEXT_INDICATORS = [
    (r"\bunited\s+kingdom\b", "UK"),
    (r",\s*uk\b", "UK"),
    (r",\s*gb\b", "UK"),
    (r"\blondon,\s*uk", "UK"),
    (r"\bengland\b", "UK"),
    (r",\s*england\b", "UK"),
    (r"\bscotland\b", "UK"),
    (r"\bwales\b", "UK"),
    (r"\bnorthern\s+ireland\b", "UK"),
    (r"\bharrogate\b", "UK"),
    (r"\bmanchester\b", "UK"),
    (r"\bedinburgh\b", "UK"),
    (r"\bglasgow\b", "UK"),
    (r"\bleeds\b", "UK"),
    (r"\bbristol\b", "UK"),
    (r"\bcambridge,\s*uk", "UK"),
    (r"(?:location|office|based|remote)\s*:?\s*canada|canada\s*(?:only|remote|office)", "Canada"),
    (r"ontario,\s*can", "Canada"),
    (r"toronto,\s*on\b", "Canada"),
    (r"montreal,\s*qc", "Canada"),
    (r"vancouver,\s*bc", "Canada"),
]

UK_CITIES = [
    "london",
    "manchester",
    "edinburgh",
    "glasgow",
    "birmingham",
    "leeds",
    "bristol",
    "harrogate",
    "cambridge",
    "oxford",
    "reading",
    "milton keynes",
    "southampton",
    "nottingham",
    "sheffield",
    "coventry",
    "liverpool",
]

CITY_STATE_DISAMBIGUATION = {
    "wyoming": {"MN": "Wyoming, Minnesota"},
    "ontario": {"CA": "Ontario, California"},
    "paris": {"TX": "Paris, Texas"},
    "portland": {"ME": "Portland, Maine", "OR": "Portland, Oregon"},
    "kansas city": {"KS": "Kansas City, Kansas", "MO": "Kansas City, Missouri"},
}

US_STATE_NAME_TO_CODE = {
    "alabama": "AL",
    "alaska": "AK",
    "arizona": "AZ",
    "arkansas": "AR",
    "california": "CA",
    "colorado": "CO",
    "connecticut": "CT",
    "delaware": "DE",
    "florida": "FL",
    "georgia": "GA",
    "hawaii": "HI",
    "idaho": "ID",
    "illinois": "IL",
    "indiana": "IN",
    "iowa": "IA",
    "kansas": "KS",
    "kentucky": "KY",
    "louisiana": "LA",
    "maine": "ME",
    "maryland": "MD",
    "massachusetts": "MA",
    "michigan": "MI",
    "minnesota": "MN",
    "mississippi": "MS",
    "missouri": "MO",
    "montana": "MT",
    "nebraska": "NE",
    "nevada": "NV",
    "new hampshire": "NH",
    "new jersey": "NJ",
    "new mexico": "NM",
    "new york": "NY",
    "north carolina": "NC",
    "north dakota": "ND",
    "ohio": "OH",
    "oklahoma": "OK",
    "oregon": "OR",
    "pennsylvania": "PA",
    "rhode island": "RI",
    "south carolina": "SC",
    "south dakota": "SD",
    "tennessee": "TN",
    "texas": "TX",
    "utah": "UT",
    "vermont": "VT",
    "virginia": "VA",
    "washington": "WA",
    "west virginia": "WV",
    "wisconsin": "WI",
    "wyoming": "WY",
}

LOCATION_STOPWORDS = [
    "responsibilities",
    "requirements",
    "qualifications",
    "description",
    "benefits",
    "who you are",
    "what you'll do",
    "about you",
    "overview",
    "summary",
    "details",
    "information",
    "remote",
    "hybrid",
    "onsite",
    "on-site",
    "virtual",
    "flexible",
    "office",
    "workplace",
    "telecommute",
]

WORK_MODE_KEYWORDS = ["remote", "hybrid", "onsite", "on-site", "virtual", "flexible"]

CURRENCY_CODES = ["GBP", "USD", "EUR", "CAD", "AUD", "JPY", "CHF"]

LEGAL_ENTITY_SUFFIXES = [
    "LLC",
    "L.L.C.",
    "L.P.",
    "LLP",
    "Inc.",
    "Inc",
    "Corp.",
    "Corp",
    "Ltd.",
    "Ltd",
    "PLC",
    "Limited",
    "Corporation",
    "Incorporated",
    "Company",
    "Co.",
]

DBA_INDICATORS = [" DBA ", " d/b/a ", " doing business as "]

TERMINAL_COMPANY_WIDTH = 100

PORTAL_NAME_INDICATORS = [
    "Agency Contractor",
    "Preferential Rehire",
    "Job Site",
    "External Career",
    "External Job",
    "Career Portal",
]

WORKDAY_ABBREVIATIONS = {
    "bcbsmn": "Blue Cross and Blue Shield of Minnesota",
    "bcbsnc": "Blue Cross Blue Shield of North Carolina",
    "hp": "HP Inc.",
    "cat": "Caterpillar",
    "bmo": "Bank of Montreal",
    "cibc": "Canadian Imperial Bank of Commerce",
    "troweprice": "T. Rowe Price",
    "healthfirst": "Healthfirst",
    "barrywehmiller": "Barry-Wehmiller",
    "centerstone": "Centerstone",
    "insulet": "Insulet Corporation",
    "sunlife": "Sun Life Financial",
    "resmed": "ResMed",
    "guardianlife": "The Guardian Life Insurance Company",
    "huron": "Huron Consulting Group",
    "nreca": "NRECA",
    "coxhealth": "CoxHealth",
    "kbr": "KBR",
    "calix": "Calix",
    "barr": "Barr Engineering",
    "amat": "Applied Materials",
    "jll": "Jones Lang LaSalle",
    "msd": "Merck Sharp & Dohme",
    "biibhr": "Biogen",
    "washpost": "The Washington Post",
    "cccis": "CCC Intelligent Solutions",
    "nshs": "Endeavor Health",
    "gevernova": "GE Vernova",
    "pattersoncompanies": "Patterson Companies",
    "carters": "Carter's",
    "nxp": "NXP Semiconductors",
    "cmu": "Carnegie Mellon University",
    "alliance": "Nissan North America",
    "denver": "City and County of Denver",
    "integritymarketing": "Integrity Marketing Group",
    "asmglobal": "ASM Global",
    "roberthalf": "Robert Half",
    "guggenheiminvestment": "Guggenheim Partners",
    "aptiv": "Aptiv",
    "sonyglobal": "Sony Corporation of America",
    "analogdevices": "Analog Devices",
    "globalfoundries": "GlobalFoundries",
    "rsm": "RSM US LLP",
    "valmont": "Valmont Industries",
    "entegris": "Entegris",
    "boseallaboutme": "Bose Corporation",
    "iqvia": "IQVIA",
    "tencent": "Tencent America",
    "generalmotors": "General Motors",
}

# Companies that almost always require security clearance
STAFFING_AGENCIES = [
    "express employment", "robert half", "adecco", "manpower",
    "kelly services", "randstad", "staffing agency",
]

CLEARANCE_COMPANIES = [
    "booz allen", "raytheon", "northrop grumman", "lockheed martin",
    "general dynamics", "bae systems", "l3harris", "leidos",
    "saic", "caci", "mantech", "perspecta", "kbr",
    "amentum", "gdit", "cole engineering", "metova federal",
    "parsons", "sierra space",
]

COMPANY_NORMALIZATIONS = {
    "SSB&T": "State Street Bank & Trust",
    "SSB": "State Street",
    "500 WP": "The Washington Post",
    "The Charles Stark Draper Laboratory": "Draper",
    "Bose Corporation, U.S.A": "Bose Corporation",
    "On Location X": "TKO Group Holdings",
    "On Location": "TKO Group Holdings",
    "HF Management Services": "Healthfirst",
    "Management Services": "Healthfirst",
    "WD": "Western Digital",
    "Western Digital Corporation": "Western Digital",
    "Westerndigital": "Western Digital",
    "GE HealthCare": "GE Healthcare",
    "General Electric Healthcare": "GE Healthcare",
    "TMobile": "T-Mobile",
    "Axway Software SA": "Axway",
    "Axway Corporate": "Axway",
    "Oleria-Security": "Oleria Security",
    "Thales USA, Inc. (AMS)": "Thales",
    "Thales USA Inc": "Thales",
    "US CMS ZOLL Manufacturing": "ZOLL Medical",
    "Newtonresearch": "Newton Research",
    "Newton research": "Newton Research",
    "Semiconductors": None,
    "Greenhouse": None,
    "General": None,
    "Auto Parts": "Genuine Parts Company",
    "T Mobile": "T-Mobile",
    "Elevance Health": "The Elevance Health Companies",
    "CareBridge": "The Elevance Health Companies",
    "Kaiser": "Kaiser Permanente",
    "Credit Acceptance Careers": "Credit Acceptance",
    "Veolia Environnement SA": "Veolia",
    "Sandisk": "Western Digital",
    "SanDisk": "Western Digital",
    "Boxinc": "Box",
    "Box, Inc.": "Box",
    "CACI": "CACI",
    "CACI International": "CACI",
    "Vsp": "VSP Vision",
    "Ukg": "UKG",
    "Abloy": "ASSA ABLOY",
}

GUARANTEED_TECHNICAL_PHRASES = [
    "computer science",
    "software engineer",
    "software developer",
    "software intern",
    "data scientist",
    "data engineer",
    "machine learning engineer",
    "ml engineer",
    "ai engineer",
    "information services intern",
    "it intern",
    "technology intern",
    "systems engineer intern",
    "platform engineer intern",
    "site reliability intern",
    "solutions engineer intern",
    "applied scientist intern",
]

ENHANCED_REMOTE_PATTERNS = [
    "work from home",
    "wfh",
    "telecommute",
    "distributed",
    "remote-first",
    "remote friendly",
    "location: remote",
    "anywhere in",
    "work anywhere",
    "fully remote",
    "100% remote",
    "remote work",
    "remote position",
]

US_STATES_FALLBACK = {
    "AL",
    "AK",
    "AZ",
    "AR",
    "CA",
    "CO",
    "CT",
    "DE",
    "FL",
    "GA",
    "HI",
    "ID",
    "IL",
    "IN",
    "IA",
    "KS",
    "KY",
    "LA",
    "ME",
    "MD",
    "MA",
    "MI",
    "MN",
    "MS",
    "MO",
    "MT",
    "NE",
    "NV",
    "NH",
    "NJ",
    "NM",
    "NY",
    "NC",
    "ND",
    "OH",
    "OK",
    "OR",
    "PA",
    "RI",
    "SC",
    "SD",
    "TN",
    "TX",
    "UT",
    "VT",
    "VA",
    "WA",
    "WV",
    "WI",
    "WY",
    "DC",
}

FULL_STATE_NAMES = {
    "alabama": "AL",
    "alaska": "AK",
    "arizona": "AZ",
    "arkansas": "AR",
    "california": "CA",
    "colorado": "CO",
    "connecticut": "CT",
    "delaware": "DE",
    "florida": "FL",
    "georgia": "GA",
    "hawaii": "HI",
    "idaho": "ID",
    "illinois": "IL",
    "indiana": "IN",
    "iowa": "IA",
    "kansas": "KS",
    "kentucky": "KY",
    "louisiana": "LA",
    "maine": "ME",
    "maryland": "MD",
    "massachusetts": "MA",
    "michigan": "MI",
    "minnesota": "MN",
    "mississippi": "MS",
    "missouri": "MO",
    "montana": "MT",
    "nebraska": "NE",
    "nevada": "NV",
    "new hampshire": "NH",
    "new jersey": "NJ",
    "new mexico": "NM",
    "new york": "NY",
    "north carolina": "NC",
    "north dakota": "ND",
    "ohio": "OH",
    "oklahoma": "OK",
    "oregon": "OR",
    "pennsylvania": "PA",
    "rhode island": "RI",
    "south carolina": "SC",
    "south dakota": "SD",
    "tennessee": "TN",
    "texas": "TX",
    "utah": "UT",
    "vermont": "VT",
    "virginia": "VA",
    "washington": "WA",
    "west virginia": "WV",
    "wisconsin": "WI",
    "wyoming": "WY",
}

CITY_TO_STATE_FALLBACK = {
    "san francisco": "CA",
    "san jose": "CA",
    "palo alto": "CA",
    "mountain view": "CA",
    "sunnyvale": "CA",
    "santa clara": "CA",
    "cupertino": "CA",
    "santa monica": "CA",
    "south san francisco": "CA",
    "foster city": "CA",
    "san ramon": "CA",
    "fremont": "CA",
    "milpitas": "CA",
    "los angeles": "CA",
    "san diego": "CA",
    "sacramento": "CA",
    "oakland": "CA",
    "irvine": "CA",
    "anaheim": "CA",
    "redwood city": "CA",
    "menlo park": "CA",
    "berkeley": "CA",
    "san mateo": "CA",
    "santa fe": "NM",
    "new york": "NY",
    "brooklyn": "NY",
    "buffalo": "NY",
    "seattle": "WA",
    "bellevue": "WA",
    "redmond": "WA",
    "bothell": "WA",
    "boston": "MA",
    "cambridge": "MA",
    "worcester": "MA",
    "westford": "MA",
    "braintree": "MA",
    "waltham": "MA",
    "chicago": "IL",
    "atlanta": "GA",
    "philadelphia": "PA",
    "pittsburgh": "PA",
    "denver": "CO",
    "golden": "CO",
    "boulder": "CO",
    "louisville": "CO",
    "phoenix": "AZ",
    "tempe": "AZ",
    "scottsdale": "AZ",
    "orlando": "FL",
    "miami": "FL",
    "tampa": "FL",
    "dallas": "TX",
    "austin": "TX",
    "plano": "TX",
    "houston": "TX",
    "san antonio": "TX",
    "fort worth": "TX",
    "charlotte": "NC",
    "raleigh": "NC",
    "durham": "NC",
    "rockville": "MD",
    "baltimore": "MD",
    "towson": "MD",
    "bloomington": "MN",
    "minneapolis": "MN",
    "draper": "UT",
    "salt lake city": "UT",
    "sioux falls": "SD",
    "pleasant prairie": "WI",
    "milwaukee": "WI",
    "cedar rapids": "IA",
    "newark": "NJ",
    "berkeley heights": "NJ",
    "middletown": "NJ",
    "washington": "DC",
    "englewood cliffs": "NJ",
    "framingham": "MA",
}

CITY_ABBREVIATIONS = {
    "sf": "San Francisco, CA",
    "nyc": "New York, NY",
    "la": "Los Angeles, CA",
    "dc": "Washington, DC",
    "philly": "Philadelphia, PA",
    "chi": "Chicago, IL",
}

LOCATION_SUFFIXES = [
    " Office",
    " office",
    " Headquarters",
    " headquarters",
    " HQ",
    " hq",
    " Campus",
    " campus",
    " Bay Area",
    " bay area",
    " Metro Area",
    " metro area",
    " Metropolitan Area",
    " Area",
    " area",
]

CANADA_PROVINCES = {"ON", "QC", "BC", "AB", "MB", "SK", "NS", "NB", "NL", "PE"}

CANADA_PROVINCE_NAMES = {
    "ontario": "ON",
    "quebec": "QC",
    "british columbia": "BC",
    "alberta": "AB",
    "manitoba": "MB",
    "saskatchewan": "SK",
    "nova scotia": "NS",
    "new brunswick": "NB",
    "newfoundland and labrador": "NL",
    "newfoundland": "NL",
    "prince edward island": "PE",
}

MAJOR_CANADIAN_CITIES = {
    "toronto": "ON",
    "ottawa": "ON",
    "montreal": "QC",
    "vancouver": "BC",
    "calgary": "AB",
    "edmonton": "AB",
    "winnipeg": "MB",
    "quebec city": "QC",
    "mississauga": "ON",
    "brampton": "ON",
    "hamilton": "ON",
    "kitchener": "ON",
    "london": "ON",
    "markham": "ON",
    "vaughan": "ON",
    "gatineau": "QC",
    "laval": "QC",
    "waterloo": "ON",
    "guelph": "ON",
    "kanata": "ON",
    "burlington": "ON",
    "oakville": "ON",
    "richmond hill": "ON",
    "pickering": "ON",
    "ajax": "ON",
    "whitby": "ON",
    "kingston": "ON",
    "windsor": "ON",
    "oshawa": "ON",
    "barrie": "ON",
    "longueuil": "QC",
    "sherbrooke": "QC",
    "surrey": "BC",
    "burnaby": "BC",
    "richmond": "BC",
    "abbotsford": "BC",
    "coquitlam": "BC",
    "victoria": "BC",
    "kelowna": "BC",
    "red deer": "AB",
    "lethbridge": "AB",
    "saskatoon": "SK",
    "regina": "SK",
    "halifax": "NS",
    "moncton": "NB",
    "st johns": "NL",
    "st. johns": "NL",
}

AMBIGUOUS_CITIES = {
    "vancouver": {"US": "WA", "Canada": "BC"},
    "ontario": {"US": "CA", "Canada": "ON"},
    "cambridge": {"US": "MA", "Canada": "ON"},
    "london": {"US": "OH", "Canada": "ON"},
    "waterloo": {"US": "IA", "Canada": "ON"},
    "windsor": {"US": "CT", "Canada": "ON"},
    "richmond": {"US": "VA", "Canada": "BC"},
    "burlington": {"US": "MA", "Canada": "ON"},
    "kingston": {"US": "NY", "Canada": "ON"},
    "hamilton": {"US": "OH", "Canada": "ON"},
    "victoria": {"US": "TX", "Canada": "BC"},
}

CANADIAN_COMPANIES = {
    "bmo",
    "bank of montreal",
    "shopify",
    "wealthsimple",
    "cae",
    "blackberry",
    "kinaxis",
    "opentext",
    "hootsuite",
}

US_CONTEXT_KEYWORDS = ["usa", "united states", "u.s.", "bay area", "silicon valley"]
CANADA_CONTEXT_KEYWORDS = ["canada", "canadian", "gta", "greater toronto area"]

PLATFORM_DETECTION_PATTERNS = {
    "workday": r"\.wd\d+\.myworkdayjobs\.com",
    "greenhouse": r"(boards\.|job-boards\.)?greenhouse\.io",
    "lever": r"jobs\.lever\.co",
    "ashby": r"(?:jobs\.)?ashbyhq\.com",
    "linkedin": r"linkedin\.com/jobs",
    "icims": r"\.icims\.com",
    "smartrecruiters": r"(jobs\.)?smartrecruiters\.com",
    "oracle": r"(\.fa\.|oraclecloud\.com)",
    "eightfold": r"\.eightfold\.ai",
    "ea": r"jobs\.ea\.com",
    "glassdoor": r"glassdoor\.com",
    "boomi": r"boomi\.com",
}

PLATFORM_CONFIGS = {
    "workday": {
        "requires_selenium": True,
        "wait_time": 15,
        "location_selectors": [
            ('dd[data-automation-id="locations"]', 0.95),
            ('dd[data-automation-id="location"]', 0.93),
            ('span[data-automation-id="jobLocation"]', 0.92),
            ('[data-automation-id*="location"]', 0.85),
            ('div[data-automation-id="jobProperties"] dd', 0.75),
            (".jobProperty .jobPropertyValue", 0.70),
            ("div.css-1ij27gp", 0.80),
            ('[aria-label*="location"]', 0.80),
        ],
        "company_selector": 'meta[property="og:site_name"]',
        "title_selector": 'h1[data-automation-id="jobTitle"]',
        "job_id_pattern": r"_([A-Z]R?-?\d{5,})(?:-\d+)?(?:\?|$)",
    },
    "greenhouse": {
        "requires_selenium": False,
        "wait_time": 3,
        "location_selectors": [
            (".location", 0.92),
            (".job-location", 0.88),
            ("div.location", 0.90),
            ("[data-qa='job-location']", 0.88),
            (".app-title + div", 0.75),
            ("h1 + div", 0.70),
            (".posting-headline + div", 0.72),
            ("[class*='location']", 0.80),
        ],
        "company_selector": 'meta[property="og:site_name"]',
        "title_selector": ".app-title",
        "job_id_pattern": r"/jobs?/(\d{7,})",
    },
    "oracle": {
        "requires_selenium": True,
        "wait_time": 15,
        "location_selectors": [
            ('[data-automation="jobLocation"]', 0.95),
            (".jobProperty", 0.80),
        ],
        "company_selector": 'meta[property="og:site_name"]',
        "title_selector": 'h1[data-automation="jobTitle"]',
        "job_id_pattern": r"/job/(\d{6,})",
    },
    "lever": {
        "requires_selenium": False,
        "wait_time": 3,
        "location_selectors": [
            (".location", 0.92),
            (".posting-categories .location", 0.88),
        ],
        "company_selector": 'meta[property="og:site_name"]',
        "title_selector": ".posting-headline h2",
        "job_id_pattern": r"(?:jobs\.)?lever\.co/[^/]+/([a-f0-9-]{36})",
    },
    "smartrecruiters": {
        "requires_selenium": False,
        "wait_time": 3,
        "location_selectors": [
            ('[itemprop="jobLocation"]', 0.92),
            (".job-location", 0.85),
        ],
        "company_selector": 'img[alt*="logo"]',
        "title_selector": 'h1[itemprop="title"]',
        "job_id_pattern": r"/(\d{15})",
    },
    "ashby": {
        "requires_selenium": True,
        "wait_time": 6,
        "location_selectors": [
            ('[class*="JobLocation"]', 0.92),
            ('div[class*="location"]', 0.88),
        ],
        "company_selector": 'meta[property="og:site_name"]',
        "title_selector": "h1",
        "job_id_pattern": r"(?:jobs\.)?ashbyhq\.com/[^/]+/([a-f0-9-]{36})",
    },
    "icims": {
        "requires_selenium": False,
        "wait_time": 3,
        "location_selectors": [
            ('[data-field="formattedLocation"]', 0.95),
            ('.iCIMS_InfoMsg.iCIMS_InfoField_Location', 0.92),
            ('[class*="location"]', 0.80),
            ('span.iCIMS_InfoMsg', 0.75),
        ],
        "company_selector": 'meta[property="og:site_name"]',
        "title_selector": "h1",
        "job_id_pattern": r"/jobs/(\d+)/job",
    },
    "ea": {
        "requires_selenium": False,
        "wait_time": 3,
        "location_selectors": [],
        "company_selector": 'meta[property="og:site_name"]',
        "title_selector": "h2",
        "job_id_pattern": r"/(\d{6,})",
    },
    "glassdoor": {
        "requires_selenium": False,
        "wait_time": 3,
        "location_selectors": [
            ('[data-test="location"]', 0.92),
            (".location", 0.85),
        ],
        "company_selector": 'meta[property="og:site_name"]',
        "title_selector": "h1",
        "job_id_pattern": None,
    },
}

WORKDAY_HQ_CODES = {
    "USNYNYC": ("New York", "NY"),
    "USCASFO": ("San Francisco", "CA"),
    "USWAEAT": ("Seattle", "WA"),
    "USTXAUS": ("Austin", "TX"),
    "USMABOA": ("Boston", "MA"),
    "USCALA": ("Los Angeles", "CA"),
    "USCASJO": ("San Jose", "CA"),
}

URL_TO_COMPANY_MAPPING = {
    r"quickenloans\.wd\d+\.myworkdayjobs\.com": "Rocket Companies",
    r"geico\.wd\d+\.myworkdayjobs\.com": "GEICO",
    r"cox\.wd\d+\.myworkdayjobs\.com": "Cox Automotive",
    r"roche\.wd\d+\.myworkdayjobs\.com": "Roche",
    r"cranecompany\.wd\d+\.myworkdayjobs\.com": "Crane Co.",
    r"motorola.*\.wd\d+\.myworkdayjobs\.com": "Motorola Solutions",
    r"nvidia\.wd\d+\.myworkdayjobs\.com": "NVIDIA",
    r"tmobile\.wd\d+\.myworkdayjobs\.com": "T-Mobile",
    r"att\.wd\d+\.myworkdayjobs\.com": "AT&T Services",
    r"disney\.wd\d+\.myworkdayjobs\.com": "The Walt Disney Company",
    r"pru\.wd\d+\.myworkdayjobs\.com": "Prudential Financial",
    r"coke\.wd\d+\.myworkdayjobs\.com": "The Coca-Cola Company",
    r"lilly\.wd\d+\.myworkdayjobs\.com": "Eli Lilly and Company",
    r"donaldson\.wd\d+\.myworkdayjobs\.com": "Donaldson Company",
    r"intapp\.wd\d+\.myworkdayjobs\.com": "Intapp",
    r"assetmark\.wd\d+\.myworkdayjobs\.com": "AssetMark",
    r"axos\.wd\d+\.myworkdayjobs\.com": "Axos Bank",
    r"nrel\.wd\d+\.myworkdayjobs\.com": "National Renewable Energy Laboratory",
    r"hhmi\.wd\d+\.myworkdayjobs\.com": "Howard Hughes Medical Institute",
    r"nasdaq\.wd\d+\.myworkdayjobs\.com": "Nasdaq",
    r"comcast\.wd\d+\.myworkdayjobs\.com": "Comcast",
    r"sbdinc\.wd\d+\.myworkdayjobs\.com": "Stanley Black & Decker",
    r"abb\.wd\d+\.myworkdayjobs\.com": "ABB",
    r"selinc\.wd\d+\.myworkdayjobs\.com": "Schweitzer Engineering Laboratories",
    r"asml\.wd\d+\.myworkdayjobs\.com": "ASML",
    r"ciena\.wd\d+\.myworkdayjobs\.com": "Ciena",
    r"globalfoundries\.wd\d+\.myworkdayjobs\.com": "GlobalFoundries",
    r"spgi\.wd\d+\.myworkdayjobs\.com": "S&P Global",
    r"cadence\.wd\d+\.myworkdayjobs\.com": "Cadence Design Systems",
    r"finra\.wd\d+\.myworkdayjobs\.com": "Finra",
    r"rb\.wd\d+\.myworkdayjobs\.com": "The Federal Reserve System",
    r"group1001wd\.wd\d+\.myworkdayjobs\.com": "Group 1001",
    r"uline\.wd\d+\.myworkdayjobs\.com": "Uline",
    r"wnc\.wd\d+\.myworkdayjobs\.com": "WNC",
    r"warnerbros\.wd\d+\.myworkdayjobs\.com": "Warner Bros.",
    r"kbr\.wd\d+\.myworkdayjobs\.com": "KBR",
    r"philips\.wd\d+\.myworkdayjobs\.com": "Philips",
    r"jci\.wd\d+\.myworkdayjobs\.com": "Johnson Controls",
    r"bmo\.wd\d+\.myworkdayjobs\.com": "Bank of Montreal",
    r"biibhr\.wd\d+\.myworkdayjobs\.com": "Biogen",
    r"icf\.wd\d+\.myworkdayjobs\.com": "ICF",
    r"highmarkhealth\.wd\d+\.myworkdayjobs\.com": "Highmark Health",
    r"labcorp\.wd\d+\.myworkdayjobs\.com": "LabCorp",
    r"websteronline\.wd\d+\.myworkdayjobs\.com": "Webster Bank",
    r"blueorigin\.wd\d+\.myworkdayjobs\.com": "Blue Origin",
    r"bloomenergy\.wd\d+\.myworkdayjobs\.com": "Bloom Energy",
    r"premierinc\.wd\d+\.myworkdayjobs\.com": "Premier Inc",
    r"leidos\.wd\d+\.myworkdayjobs\.com": "Leidos",
    r"tnsi\.wd\d+\.myworkdayjobs\.com": "TNS",
    r"flextronics\.wd\d+\.myworkdayjobs\.com": "Flex Ltd",
    r"genmab\.wd\d+\.myworkdayjobs\.com": "Genmab",
    r"doubleverify\..*greenhouse\.io": "DoubleVerify",
    r"job-boards\.greenhouse\.io/doubleverify": "DoubleVerify",
    r"shakeshack\.wd\d+\.myworkdayjobs\.com": "Shake Shack",
    r"avnet\.wd\d+\.myworkdayjobs\.com": "Avnet",
    r"micron\.wd\d+\.myworkdayjobs\.com": "Micron",
    r"panduit\.wd\d+\.myworkdayjobs\.com": "Panduit",
    r"moog\.wd\d+\.myworkdayjobs\.com": "Moog",
    r"erickson\.wd\d+\.myworkdayjobs\.com": "Erickson Senior Living",
    r"gdit\.wd\d+\.myworkdayjobs\.com": "GDIT",
    r"lumentum\.wd\d+\.myworkdayjobs\.com": "Lumentum",
    r"ingrammicro\.wd\d+\.myworkdayjobs\.com": "Ingram Micro",
    r"jobs\.smartrecruiters\.com/MSXInternational": "MSX International",
    r"jobs\.smartrecruiters\.com/TheNielsenCompany": "Nielsen",
    r"jobs\.smartrecruiters\.com/Nielsen": "Nielsen",
    r"jobs\.smartrecruiters\.com/ATPCO1": "ATPCO",
    r"jobs\.smartrecruiters\.com/BoschGroup": "Bosch",
    r"jobs\.smartrecruiters\.com/Cotiviti": "Cotiviti",
    r"jobs\.smartrecruiters\.com/Businessolver": "Businessolver",
    r"centific\.wd\d+\.myworkdayjobs\.com": "Centific",
    r"jpmc\.fa\.[^/]+\.oraclecloud\.com": "JPMorgan Chase",
    r"jpmorganchase\.wd\d+\.myworkdayjobs\.com": "JPMorgan Chase",
    r"commscope\.wd\d+\.myworkdayjobs\.com": "CommScope",
    r"jobs\.commscope\.com": "CommScope",
    r"freseniusmedicalcare\.wd\d+\.myworkdayjobs\.com": "Fresenius Medical Care",
    r"alleghenycounty\.bamboohr\.com": "Allegheny County",
    r"rdccareers.*greenhouse\.io": "Realtor.com",
    r"job-boards\.greenhouse\.io/rdccareers": "Realtor.com",
    r"pge\.com.*job": "PG&E",
    r"altera\.wd\d+\.myworkdayjobs\.com": "Altera",
    r"usaa\.wd\d+\.myworkdayjobs\.com": "USAA",
    r"pwc\.wd\d+\.myworkdayjobs\.com": "PwC",
    r"sec\.wd\d+\.myworkdayjobs\.com": "Samsung Electronics America",
    r"jj\.wd\d+\.myworkdayjobs\.com": "Johnson & Johnson",
    r"tamus\.wd\d+\.myworkdayjobs\.com": "Texas A&M University System",
    r"alcon\.wd\d+\.myworkdayjobs\.com": "Alcon",
    r"kla\.wd\d+\.myworkdayjobs\.com": "KLA Corporation",
    r"cccis\.wd\d+\.myworkdayjobs\.com": "CCC Intelligent Solutions",
    r"nshs\.wd\d+\.myworkdayjobs\.com": "Endeavor Health",
    r"gevernova\.wd\d+\.myworkdayjobs\.com": "GE Vernova",
    r"pattersoncompanies\.wd\d+\.myworkdayjobs\.com": "Patterson Companies",
    r"carters\.wd\d+\.myworkdayjobs\.com": "Carter's",
    r"nxp\.wd\d+\.myworkdayjobs\.com": "NXP Semiconductors",
    r"cmu\.wd\d+\.myworkdayjobs\.com": "Carnegie Mellon University",
    r"alliance\.wd\d+\.myworkdayjobs\.com": "Nissan North America",
    r"denver\.wd\d+\.myworkdayjobs\.com": "City and County of Denver",
    r"integritymarketing\.wd\d+\.myworkdayjobs\.com": "Integrity Marketing Group",
    r"asmglobal\.wd\d+\.myworkdayjobs\.com": "ASM Global",
    r"asmglobal\.wd\d+\.myworkdayjobs\.com/careers/job/.*Conshohocken": "Legends Global",
    r"asmglobal\.wd\d+\.myworkdayjobs\.com/careers/job/.*Frisco": "Legends Global",
    r"roberthalf\.wd\d+\.myworkdayjobs\.com": "Robert Half",
    r"guggenheiminvestment\.wd\d+\.myworkdayjobs\.com": "Guggenheim Partners",
    r"sunlife\.wd\d+\.myworkdayjobs\.com": "Sun Life Financial",
    r"aptiv\.wd\d+\.myworkdayjobs\.com": "Aptiv",
    r"enovis\.wd\d+\.myworkdayjobs\.com": "Enovis",
    r"cae\.wd\d+\.myworkdayjobs\.com": "CAE",
    r"sonyglobal\.wd\d+\.myworkdayjobs\.com": "Sony Corporation of America",
    r"analogdevices\.wd\d+\.myworkdayjobs\.com": "Analog Devices",
    r"globalhr\.wd\d+\.myworkdayjobs\.com": "RTX",
    r"parsons\.wd\d+\.myworkdayjobs\.com": "Parsons Corporation",
    r"rsm\.wd\d+\.myworkdayjobs\.com": "RSM US LLP",
    r"humana\.wd\d+\.myworkdayjobs\.com": "Humana",
    r"valmont\.wd\d+\.myworkdayjobs\.com": "Valmont Industries",
    r"entegris\.wd\d+\.myworkdayjobs\.com": "Entegris",
    r"boseallaboutme\.wd\d+\.myworkdayjobs\.com": "Bose Corporation",
    r"iqvia\.wd\d+\.myworkdayjobs\.com": "IQVIA",
    r"tencent\.wd\d+\.myworkdayjobs\.com": "Tencent America",
    r"generalmotors\.wd\d+\.myworkdayjobs\.com": "General Motors",
    r"autodesk\.wd\d+\.myworkdayjobs\.com": "Autodesk",
    r"amat\.wd\d+\.myworkdayjobs\.com": "Applied Materials",
    r"job-boards\.greenhouse\.io/asteraearlycareer": "Astera Labs",
    r"job-boards\.greenhouse\.io/samsungresearchamericainternship": "Samsung Research America",
    r"job-boards\.greenhouse\.io/obsidiansecurity": "Obsidian Security",
    r"job-boards\.greenhouse\.io/commvault": "Commvault",
    r"job-boards\.greenhouse\.io/audaxgroup": "Audax Group",
    r"job-boards\.greenhouse\.io/verkada": "Verkada",
    r"job-boards\.greenhouse\.io/auctane": "Auctane",
    r"job-boards\.greenhouse\.io/faire": "Faire",
    r"job-boards\.greenhouse\.io/internshiplist2000": "Greenhouse",
    r"job-boards\.greenhouse\.io/waterloocoop": "Waterloo",
    r"job-boards\.greenhouse\.io/clear": "CLEAR",
    r"job-boards\.greenhouse\.io/mongodb": "MongoDB",
    r"job-boards\.greenhouse\.io/sift": "Sift",
    r"job-boards\.greenhouse\.io/ramp": "Ramp",
    r"job-boards\.greenhouse\.io/zoox": "Zoox",
    r"job-boards\.greenhouse\.io/twosigma": "Two Sigma",
    r"job-boards\.greenhouse\.io/stratacareers": "Strata Decision Technology",
    r"job-boards\.greenhouse\.io/launchdarkly": "LaunchDarkly",
    r"job-boards\.greenhouse\.io/gelberhandshake": "Gelber Group",
    r"job-boards\.greenhouse\.io/gametimeunited": "Gametime",
    r"job-boards\.greenhouse\.io/appliedintuition": "Applied Intuition",
    r"job-boards\.greenhouse\.io/sigmacomputing": "Sigma Computing",
    r"job-boards\.greenhouse\.io/abacusinsights": "Abacus Insights",
    r"job-boards\.eu\.greenhouse\.io/physicsx": "PhysicsX",
    r"generatebiomedicines\.com": "Generate Biomedicines",
    r"jobs\.smartrecruiters\.com/Visa": "Visa",
    r"jobs\.smartrecruiters\.com/Intuitive": "Intuitive Surgical",
    r"jobs\.smartrecruiters\.com/Experian": "Experian",
    # REMOVED duplicate key: BoschGroup -> Robert Bosch Venture Capital
    # It silently overrode the earlier, correct entry.
    # Bosch's smartrecruiters board is the main company, not the
    # VC arm; and Portland in tech postings is Oregon far more
    # often than Maine.
    r"jobs\.smartrecruiters\.com/WesternDigital": "Western Digital",
    r"jobs\.lever\.co/zoox": "Zoox",
    r"jobs\.lever\.co/tri": "Toyota Research Institute",
    r"jobs\.lever\.co/brightmachines": "Bright Machines",
    r"job-boards\.greenhouse\.io/rocketlawyer": "Rocket Lawyer",
    r"job-boards\.greenhouse\.io/newtonresearch": "Newton Research",
    r"job-boards\.greenhouse\.io/armada": "Armada",
    r"careers-magaero\.icims\.com": "MAG Aerospace",
    r"thermofisher\.wd\d+\.myworkdayjobs\.com": "Thermo Fisher Scientific",
    r"genpt\.wd\d+\.myworkdayjobs\.com": "Genuine Parts Company",
    r"zoll\.wd\d+\.myworkdayjobs\.com": "ZOLL Medical",
    r"jeffersonhealth\.wd\d+\.myworkdayjobs\.com": "Jefferson Health",
    r"arienscompany\.wd\d+\.myworkdayjobs\.com": "Ariens",
    r"ebcs\.fa\.em\d+\.oraclecloud\.com": "Moog",
    r"edel\.fa\.us\d+\.oraclecloud\.com/hcmUI/CandidateExperience/en/sites/CX_2001": "Fortinet",
    r"edel\.fa\.us\d+\.oraclecloud\.com": "BXP",
    r"wacom\.applytojob\.com": "Wacom",
    r"cerence\.wd\d+\.myworkdayjobs\.com": "Cerence",
    r"jobs\.ashbyhq\.com/skydio": "Skydio",
    r"irhythmtech\.wd\d+\.myworkdayjobs\.com": "iRhythm Technologies",
    r"careers-ice\.icims\.com": "Intercontinental Exchange",
    r"statestreet\.wd\d+\.myworkdayjobs\.com": "State Street",
    r"hntb\.wd\d+\.myworkdayjobs\.com": "HNTB",
    r"compass\.wd\d+\.myworkdayjobs\.com": "Radian",
    r"verdantas\.wd\d+\.myworkdayjobs\.com": "Verdantas",
    r"job-boards\.greenhouse\.io/billiontoone": "BillionToOne",
    r"job-boards\.greenhouse\.io/spacekinetic": "Space Kinetic",
    r"careers-axway\.icims\.com": "Axway",
    r"careers-sig\.icims\.com": "Susquehanna International Group",
    r"careers-stifel\.icims\.com": "Stifel",
    r"careers-uwmcareers\.icims\.com": "United Wholesale Mortgage",
    r"job-boards\.greenhouse\.io/industrialelectricmanufacturing": "Industrial Electric Manufacturing",
    r"job-boards\.greenhouse\.io/avride": "Avride",
    r"kiongroup\.wd\d+\.myworkdayjobs\.com": "KION Group",
    r"chamberlain\.wd\d+\.myworkdayjobs\.com": "Chamberlain Group",
    r"thales\.wd\d+\.myworkdayjobs\.com": "Thales",
    r"realtyincome\.wd\d+\.myworkdayjobs\.com": "Realty Income",
    r"careers\.hellofresh\.com": "HelloFresh",
    r"fiserv\.wd\d+\.myworkdayjobs\.com": "Fiserv",
    r"fa-exty-saasfaprod\d+\.fa\.ocs\.oraclecloud\.com": "Howmet Aerospace",
    r"njm\.wd\d+\.myworkdayjobs\.com": "NJM Insurance",
    r"skillz\.com/careers": "Skillz",
    r"sponsorunited\.breezy\.hr": "SponsorUnited",
    r"firstam\.wd\d+\.myworkdayjobs\.com": "First American Financial",
    r"infineon\.com/careers": "Infineon",
    r"jobs\.infineon\.com": "Infineon",
    r"jobs\.lever\.co/veeva": "Veeva Systems",
    r"jobs\.lever\.co/wealthsimple": "Wealthsimple",
    r"jobs\.lever\.co/seatgeek": "SeatGeek",
    r"jobs\.ashbyhq\.com/uipath": "UiPath",
    r"jobs\.ashbyhq\.com/Ridealso": "ALSO",
    r"jobs\.ashbyhq\.com/atomicsemi": "Atomic Semi",
    r"jobs\.ashbyhq\.com/cohere": "Cohere",
    r"jobs\.ea\.com": "Electronic Arts",
    r"eeho\.fa\.us2\.oraclecloud\.com": "Oracle",
    r"edxn\.fa\.us2\.oraclecloud\.com": "BXP",
    r"fa-evmr.*\.oraclecloud\.com": "Nokia",
    r"jobs-legrand\.icims\.com": "Legrand",
    r"careers-gdms\.icims\.com": "General Dynamics Mission Systems",
    r"careers-peraton\.icims\.com": "Peraton",
    r"careers-here\.icims\.com": "HERE Technologies",
    r"careers-ebscoind\.icims\.com": "EBSCO",
    r"careers-sas\.icims\.com": "SAS",
    r"careers-sri\.icims\.com": "SRI International",
    r"uscareers-waters\.icims\.com": "Waters Corporation",
    r"jobs\.paccar\.com": "Paccar",
    r"apply\.careers\.microsoft\.com": "Microsoft",
    r"3ds\.com/careers": "Dassault Systèmes",
    r"careers\.adobe\.com": "Adobe",
    r"jobs\.siemens\.com": "Siemens",
    r"ats\.rippling\.com/.*/redaspen": "Red Aspen",
    r"ats\.rippling\.com/netwrix-corporation": "Netwrix",
    r"uscareers-lennox\.icims\.com": "Lennox International",
    r"careers\.cisco\.com": "Cisco",
    r"jobs\.lever\.co/lexeotx": "Lexeo Therapeutics",
    r"jobs\.lever\.co/kognitos": "Kognitos",
    r"jobs\.lever\.co/trumid": "Trumid",
    r"jobs\.ashbyhq\.com/poshmark": "Poshmark",
    r"jobs\.ashbyhq\.com/Kognitos": "Kognitos",
    r"rakuten\.wd\d+\.myworkdayjobs\.com": "Rakuten",
    r"ssctech\.wd\d+\.myworkdayjobs\.com": "SSC Technologies",
    r"tricentis\.wd\d+\.myworkdayjobs\.com": "Tricentis",
    r"equinix\.wd\d+\.myworkdayjobs\.com": "Equinix",
    r"uhaul\.wd\d+\.myworkdayjobs\.com": "U-Haul",
    r"calix\.wd\d+\.myworkdayjobs\.com": "Calix",
    r"flir\.wd\d+\.myworkdayjobs\.com": "Teledyne FLIR",
    r"jobs\.bytedance\.com": "ByteDance",
    r"lifeattiktok\.com": "TikTok",
    r"jobs\.lever\.co/verygoodsecurity": "Very Good Security",
    r"job-boards\.greenhouse\.io/northspyre": "Northspyre",
    r"job-boards\.greenhouse\.io/queracomputinginc": "Quera Computing",
    r"job-boards\.greenhouse\.io/queracomputing": "Quera Computing",
    r"job-boards\.greenhouse\.io/trumid": "Trumid",
    r"abcfinancial\.wd\d+\.myworkdayjobs\.com": "ABC Financial Services",
    r"trimble\.wd\d+\.myworkdayjobs\.com": "Trimble",
    r"nuclearn-ai\.breezy\.hr": "Nuclearn AI",
    r"cinfin\.taleo\.net": "Cincinnati Financial",
    r"praxair\.taleo\.net": "Praxair",
    r"jobs\.saic\.com": "SAIC",
    r"jobs\.ashbyhq\.com/Flock": "Flock Safety",
    r"intralinks\.wd\d+\.myworkdayjobs\.com": "Intralinks",
    r"avav\.wd\d+\.myworkdayjobs\.com": "AeroVironment",
    r"apply\.workable\.com/ascendis-pharma": "Ascendis Pharma",
    r"apply\.workable\.com/connectprep": "ConnectPrep",
    r"apply\.workable\.com/al-warren-oil": "Al Warren Oil Company",
    r"careers-cotiviti\.icims\.com": "Cotiviti",
    r"businessolver\.com/careers": "Businessolver",
    r"jobs\.ashbyhq\.com/marianaminerals": "Mariana Minerals",
    r"jobs\.ashbyhq\.com/output": "Output Biosciences",
    r"jobs\.ashbyhq\.com/Ontic": "Ontic",
    r"jobs\.lever\.co/weride": "WeRide",
    r"jobs\.lever\.co/gr0": "Gr0",
    r"invoicecloud\.net/careers": "InvoiceCloud",
    r"careers\.brivo\.com": "Brivo",
    r"crunchtime\.com/open-positions": "CrunchTime",
    r"careers\.merzaesthetics\.com": "Merz North America",
    r"jolera\.com": "Jolera",
    r"boomi\.com": "Boomi",
}

JUNK_SUBDOMAIN_PATTERNS = [
    r".*\d{4,}.*",
    r".*earlycareer.*",
    r".*internship.*",
    r".*careers?$",
    r".*jobs?$",
    r".*wd\d+$",
    r"^recruiting$",
    r"^recruiting2$",
    r"^ultipro$",
    r"^jobvite$",
    r"^jobvite2$",
    r"^jobs\.jobvite$",
    r"^ultipro$",
    r"^icims$",
    r"^taleo$",
    r"^workday$",
    r"^lever$",
    r"^greenhouse$",
    r"^smartrecruiters$",
    r"^jobvite$",
    r"^applicantpro$",
    r"^paylocity$",
    r"^paycom$",
    r"^adp$",
    r"^brassring$",
    r"^clearcompany$",
    r"^rippling$",
]

COMPANY_SLUG_MAPPING = {
    "sig": "Susquehanna International Group",
    "spgi": "S&P Global",
    "hhmi": "Howard Hughes Medical Institute",
    "nrel": "National Renewable Energy Laboratory",
    "sbdinc": "Stanley Black & Decker",
    "selinc": "Schweitzer Engineering Laboratories",
    "asml": "ASML",
    "abb": "ABB",
    "geico": "GEICO",
    "asteraearlycareer2026": "Astera Labs",
    "asteraearlycareer": "Astera Labs",
    "samsungresearchamericainternship": "Samsung Research America",
    "obsidiansecurity": "Obsidian Security",
    "group1001wd": "Group 1001",
    "audaxgroup": "Audax Group",
    "ridealso": "ALSO",
    "openai": "OpenAI",
    "tiktok": "TikTok",
    "linkedin": "LinkedIn",
    "paypal": "PayPal",
    "verkada": "Verkada",
    "atomicsemi": "Atomic Semi",
    "boomi": "Boomi",
    "biibhr": "Biogen",
    "icf": "ICF",
    "highmarkhealth": "Highmark Health",
    "labcorp": "LabCorp",
    "websteronline": "Webster Bank",
    "blueorigin": "Blue Origin",
    "bloomenergy": "Bloom Energy",
    "premierinc": "Premier Inc",
    "usaa": "USAA",
    "pwc": "PwC",
    "sec": "Samsung Electronics America",
    "jj": "Johnson & Johnson",
    "alcon": "Alcon",
    "kla": "KLA Corporation",
    "clear": "CLEAR",
    "mongodb": "MongoDB",
    "sift": "Sift",
    "ramp": "Ramp",
    "seatgeek": "SeatGeek",
    "twosigma": "Two Sigma",
    "cccis": "CCC Intelligent Solutions",
    "nshs": "Endeavor Health",
    "gevernova": "GE Vernova",
    "nxp": "NXP Semiconductors",
    "cmu": "Carnegie Mellon University",
    "roberthalf": "Robert Half",
    "guggenheiminvestment": "Guggenheim Partners",
    "analogdevices": "Analog Devices",
    "globalfoundries": "GlobalFoundries",
    "valmont": "Valmont Industries",
    "entegris": "Entegris",
    "boseallaboutme": "Bose Corporation",
    "iqvia": "IQVIA",
    "tencent": "Tencent America",
    "generalmotors": "General Motors",
    "stratacareers": "Strata Decision Technology",
    "launchdarkly": "LaunchDarkly",
    "gelberhandshake": "Gelber Group",
    "sigmacomputing": "Sigma Computing",
    "paycomonline": "Paycom",
    "gainwelltechnologies": "Gainwell Technologies",
    "matchgroup": "Match Group",
    "tinder": "Match Group",
    "church": "CVS Health",
    "tri-c": "Tri-C Community College",
    "cenow": "CenoW",
    "chemical": "Dow Chemical",
    "aerospace": "The Aerospace Corporation",
    "stream": "Workstream",
    "telecom": "Unknown",
    "rippling": "Unknown",
    "cloudforce": "Unknown",
    "myworkdayjobs": "Unknown",
    "atp": "ATPCO",
    "gdit": "General Dynamics IT",
    "bestegg": "Best Egg",
    "bosch group": "Bosch",
    "bose corporation": "Bose",
    "cadence design systems": "Cadence",
    "coherent corp": "Coherent",
    "commscope": "CommScope",
    "ditto ai": "Ditto",
    "dovercorporation": "Dover Corporation",
    "galileo financial technologies": "Galileo",
    "gen digital": "Gen",
    "jpmorganchase": "JPMorgan Chase",
    "lucidmotors": "Lucid Motors",
    "motorola": "Motorola Solutions",
    "paramount global": "Paramount",
    "qualcomm technologies": "Qualcomm",
    "robert bosch": "Bosch",
    "sandisk": "SanDisk",
    "samsung electronics america": "Samsung Electronics",
    "samsung research": "Samsung Electronics",
    "state street bank & trust": "State Street",
    "tmobile": "T-Mobile",
    "tandem diabetes care": "Tandem Diabetes Care",
    "the walt disney": "The Walt Disney Company",
    "tik tok": "TikTok",
    "united therapeutics corporation": "United Therapeutics",
    "wex inc": "Wex",
    "zayo group": "Zayo",
    "zoom communications": "Zoom",
    "akamai technologies": "Akamai",
    "booz allen": "Booz Allen Hamilton",
    "iheartmedia management services, inc. | ams": "iHeartMedia",
    "ryder integrated logistics, inc. (employer-payroll)": "Ryder",
    "robert w. baird &": "Robert W. Baird",
    "al warren oil": "Al Warren Oil Company",
    "al warren oil company ": "Al Warren Oil Company",
    "micron": "Micron Technology",
    "bmwgroup": "BMW Group",
    "thenewyorktimes": "The New York Times",
    "beone": "BeiGene",
    "heidelberg materials us": "Heidelberg Materials",
    "cerro wire": "Cerro Wire",
    "watkins manufacturing": "Watkins Manufacturing",
    "delta dental plan of michigan": "Delta Dental",
    "prairie view a&m university": "Prairie View A&M",
    "mayor and city council of baltimore": "City of Baltimore",
    "open tennis championships": "US Open",
    "westgate resorts": "Westgate Resorts",
    "rite-hite doors": "Rite-Hite",
    "nidec motor": "Nidec",
    "t.y. lin international": "TY Lin International",
    "layerhealth": "Layer Health",
    "radixuniversity": "Radix University",
    "macomtech": "MACOM",
    "berkley": "W.R. Berkley",
    "us-erac": "Enterprise",
    "hpiq": "HP",
    "dematic corp. (ild-us)": "Dematic",
    "clearesult consulting": "CLEAResult",
    "clearesult": "CLEAResult",
    "securitas security services": "Securitas",
    "securitas": "Securitas",
    "onto innovation inc-us": "Onto Innovation",
    "actian corporation": "Actian",
    "vertex pharmaceuticals": "Vertex",
    "cambia health": "Cambia Health",
    "blue origin": "Blue Origin",
    "goosehead insurance agency": "Goosehead Insurance",
    "nightwing intelligence": "Nightwing",
    "motion industries": "Motion Industries",
    "primerica life insurance": "Primerica",
    "oklahoma": "Unknown",
    "okgov": "Unknown",
    "edxn.fa.us2": "BXP",
    "medical systems information": "Unknown",
    "hartford fire ins": "The Hartford",
    "stewart title guaranty company - united states": "Stewart Title",
    "thermo finnigan": "Thermo Fisher Scientific",
    "government employees health association": "GEHA",
    "charles river analytics": "Charles River Analytics",
    "f8f santander bank n.a.": "Santander",
    "able": "Unknown",
    "federal": "Unknown",
    "centific global": "Centific",
    "abacusinsights": "Abacus Insights",
    "physicsx": "PhysicsX",
    "appliedintuition": "Applied Intuition",
}

COMPANY_PLACEHOLDERS = [
    "Unknown",
    "N/A",
    "Company",
    "Employer",
    "Careers",
    "Jobs",
    "External",
    "Portal",
    "Applicant",
    "Apply",
]

COMPANY_NAME_PREFIXES = [
    "lifeat",
    "joinat",
    "join",
    "careersat",
    "careers",
    "workfor",
    "workat",
    "work",
    "hiringat",
    "hiring",
]

COMPANY_NAME_STOPWORDS = [
    "Careers at ",
    "Careers | ",
    "Work at ",
    "Join ",
    " Careers",
    " Jobs",
    " - Careers",
    " Career Site",
]

JOB_ID_PATTERNS = [
    (r"/jobs?/(\d{10,})", 0.96),
    (r"gh_jid=(\d{7,})", 0.96),
    (r"[?&]token=(\d{10})", 0.96),
    (r"/jobs?/(\d{6,})", 0.94),
    (r"/careers/jobs/(\d{4,6})", 0.92),
    (r"/careers/jobs/(\d{4,6})", 0.92),
    (r"_([A-Z]{1,4}-?\d{4,})(?:-\d+)?(?:\?|$)", 0.93),
    (r"_?(REQ-\d{4,})(?:\?|$|/)", 0.92),
    (r"_([A-Z]{1,3}_\d{4,})(?:\?|$|/)", 0.91),  # e.g. JR_14561
    (r"/([A-Z]{2,3}\d{5,})(?:-\d+)?(?:\?|$)", 0.91),
    (r"(?:jobs\.)?lever\.co/[^/]+/([a-f0-9-]{36})", 0.96),
    (r"(?:jobs\.)?ashbyhq\.com/[^/]+/([a-f0-9-]{36})", 0.96),
    (r"smartrecruiters\.com/[^/]+/(\d{15})", 0.96),
    (r"/jobs/(\d+)/job", 0.94),
    (r"icims\.com/jobs/(\d+)/", 0.94),  # iCIMS job ID
    (r"successfactors\.com/.*?/(\d{6,})", 0.88),  # SuccessFactors
    (r"/job/[^/]+/(\d{7,})/", 0.88),  # SuccessFactors numeric ID in URL
    (r"REQ[_-]?(\d{6,})", 0.92),
    (r"job[/_]([A-Z0-9_-]{6,15})(?:\?|$|/)", 0.86),
    (r"[?&]reqId=([A-Z0-9_-]{4,15})(?:&|$)", 0.88),
    (r"/(\d{6,})(?:/|\?|$)", 0.76),
    (r"SALES(\d{6})", 0.85),
]

LOCATION_SELECTORS = [
    ('[data-qa="location"]', 0.92),
    ('[data-automation-id="locations"]', 0.95),
    ('[data-automation="jobLocation"]', 0.95),
    ('[itemprop="jobLocation"]', 0.92),
    (".location", 0.86),
    (".job-location", 0.86),
    (".posting-categories .location", 0.88),
]

LOCATION_METADATA_PATTERNS = [
    r"time\s+type.*$",
    r"Full\s+time.*$",
    r"Part\s+time.*$",
    r"posted\s+on.*$",
    r"Employment\s+Type.*$",
    r"Details.*$",
    r"Program.*$",
]

HTML_ARTIFACT_PATTERNS = [r"^s(?=[A-Z])", r"^p(?=[A-Z])"]

INVALID_LOCATION_KEYWORDS = [
    "Etc",
    "etc",
    "N/A",
    "TBD",
    "Various",
    "Multiple",
    "Nationwide",
    "ITC Federal, Llc",
    "Social Circle Hwy",
    ", OK County",
    "in USA",
    "in usa",
    "view all",
    "pkwy sw wy",
    "biingham",
    "woodbridge township",
    "Amrdam",
    "ITC Federal",
    "Social Circle",
    "OK County",
    "time",
    "type",
    "full",
    "part",
    "posted",
    "employment",
    "Rling",
    "DelawareOhio",
    "Westminr",
    "ORPortland",
    "MDHuntsville",
    "Technology, US",
    "Based On Business Needs",
    "Preference",
    "Assistance",
    "City, KS",
    "In, Office",
    "United States",
    "in USA",
]

DEPARTMENT_KEYWORDS = [
    "quantum",
    "performance",
    "analytics",
    "maintenance",
    "wearables",
    "search",
    "external",
    "product",
    "oracle analytics",
]

ROLE_CATEGORIES = {
    "Pure Software": {
        "keywords": ["backend", "frontend", "full stack", "web developer"],
        "exclude": ["embedded", "firmware", "hardware"],
        "action": "ACCEPT",
        "alert": "✅ SOFTWARE",
    },
    "Data & AI": {
        "keywords": ["data scien", "machine learning", "ai engineer", "analytics", "data analyst", "ml engineer", "mlops", "ml ops", "visualization", "research scientist", "research intern", "agentic", "documentation", "data engineer", "applied scientist", "decision science", "business intelligence", "bi analyst", "bi intern", "intelligence analyst"],
        "exclude": [],
        "action": "ACCEPT",
        "alert": "✅ DATA/AI",
    },
    "Product Management": {
        "keywords": ["product management", "product manager"],
        "exclude": [],
        "action": "ACCEPT",
        "alert": "🔍 PRODUCT MANAGEMENT",
    },
}

TECHNICAL_ROLE_KEYWORDS = {
    "software",
    "engineer",
    "engineering",
    "developer",
    "development",
    "developing",
    "programmer",
    "programming",
    "coding",
    "code",
    "coder",
    "data",
    "ml",
    "ai",
    "machine learning",
    "artificial intelligence",
    "full stack",
    "backend",
    "frontend",
    "web",
    "mobile",
    "cloud",
    "devops",
    "sre",
    "platform",
    "security",
    "qa",
    "test",
    "testing",
    "automation",
    "technology",
    "technical",
    "it ",
    "information technology",
    "systems",
    "digital",
    "quantitative",
    "analytics",
    "solutions",
    "infrastructure",
    "cybersecurity",
    "r&d",
    "llm",
    "nlp",
    "natural language",
    "natural language processing",
    "computer vision",
    "computer science",
    "computer",
    "cs",
    "deep learning",
    "neural network",
    "generative ai",
    "multimodal",
    "transformer",
    "reinforcement learning",
    "embedded",
    "firmware",
    "fpga",
    "gpu",
    "cuda",
    "robotics",
    "autonomous",
    "perception",
    "controls",
    "database",
    "sql",
    "nosql",
    "etl",
    "pipeline",
    "distributed systems",
    "microservices",
    "api development",
    "kubernetes",
    "docker",
    "containerization",
    "pytorch",
    "tensorflow",
    "computational",
    "bioinformatics",
    "algorithm",
    "hpc",
    "5g",
    "ran",
    "baseband",
    "wireless",
    "information services",
    "information systems",
    "site reliability",
    "platform engineering",
    "release engineering",
    "build engineer",
    "tools engineer",
    "infra engineer",
    "applied scientist",
    "research intern",
    "data platform",
    "data infrastructure",
    "data operations",
    "analytics engineer",
    "analytics engineering",
    "business analytics",
    "decision science",
    "ai/ml",
    "ml ops",
    "mlops",
    "ai ops",
    "computer vision engineer",
    "cv engineer",
    "prompt engineer",
    "ai research",
    "technical program",
    "solutions engineer",
    "solutions architect",
    "developer experience",
    "developer relations",
    "blockchain",
    "web3",
    "smart contract",
    "ar/vr",
    "xr engineer",
    "spatial computing",
    "iot",
    "internet of things",
    "edge computing",
    "application",
    "business intelligence",
    "bi analyst",
    "bi intern",
    "data analyst",
    "intelligence analyst",
    "intelligence data",
    "talent intelligence",
    "workforce analytics",
    "people analytics",
    "tableau",
    "power bi",
    "looker",
    "qlik",
    "research scientist",
    "research engineer",
    "applied research",
}

TECH_COMPANIES = {
    "google",
    "meta",
    "amazon",
    "apple",
    "netflix",
    "microsoft",
    "salesforce",
    "adobe",
    "oracle",
    "sap",
    "ibm",
    "cisco",
    "uber",
    "airbnb",
    "stripe",
    "databricks",
    "snowflake",
    "thomson reuters",
    "bloomberg",
    "palantir",
    "nvidia",
    "intel",
    "amd",
    "qualcomm",
    "broadcom",
    "texas instruments",
    "spotify",
    "twitter",
    "linkedin",
    "pinterest",
    "snap",
    "doordash",
    "instacart",
    "robinhood",
    "coinbase",
    "plaid",
    "square",
    "shopify",
    "twilio",
    "zoom",
    "slack",
    "dropbox",
    "atlassian",
    "servicenow",
    "workday",
    "zendesk",
    "hubspot",
    "asana",
    "notion",
    "figma",
    "canva",
    "miro",
}

TECHNICAL_PATTERNS = [
    r"\bprogramm(er|ing)\b",
    r"\bdevelop(er|ment|ing)\b",
    r"\bengineer(ing)?\b",
    r"\bnatural\s+language\b",
    r"\bsoftware\s+\w+",
    r"\bapplication\s+\w*develop",
    r"\bproduct\s+manag(er|ement)\b",
]

NON_TECHNICAL_PURE = {
    "supply chain",
    "supply chain management",
    "product management",
    "product manager",
    "marketing analyst",
    "sales",
    "recruiter",
    "hr specialist",
    "finance analyst",
    "accountant",
    "legal",
    "mechanical engineer",
    "biomedical engineer",
    "industrial engineer",
    "manufacturing engineer",
    "materials engineer",
    "civil engineer",
    "structural engineer",
    "chemical engineer",
    "process engineer",
    "quality engineer",
    "operations analyst",
    "market research",
    "photogrammetry",
    "geometric software",
    "geospatial",
    "remote sensing",
    "rf systems",
    "rf engineer",
    "rf design",
    "antenna engineer",
    "radar engineer",
    "partnerships",
    "data center technician",
    "technician",
    "facilities",
    "reporting analyst",
    "analytics & reporting",
    "calibration and validation",
    "calval",
    "radiometric",
    "satellite imagery",
    "space systems",
    "optical engineer",
    "laser engineer",
    "opto-mechanical",
    "hardware engineer",
    "electrical engineer",
    "noise control",
    "acoustic",
    "thermal engineer",
}

SPONSORSHIP_REJECT_PATTERNS = [
    r"(?:no|not|without).{0,100}(?:current|future).{0,50}sponsor(?:ship)?",
    r"(?:no|not).{0,50}sponsor(?:ship)?\s+(?:available|offered|provided)",
    r"sponsor(?:ship)?\s+(?:not available|unavailable|not offered)",
    r"must (?:be|have).{0,50}(?:authorized|authorization).{0,50}(?:without|no).{0,50}sponsor",
    r"(?:clearance.*required)",
    r"applicants must be eligible for any required u\.s\. export",
    r"willing and able to obtain a top secret",
    r"ability to obtain a secret clearance",
    r"ability to obtain.*secret",
    r"scheduled to obtain.*bachelor.*2028",
    r"pursuing\s+bsee\s+in\s+electrical",
    r"candidate\s+must\s+be\s+presently\s+pursuing\s+bsee",
    r"this\s+position\s+requires.*obtain.*maintain.*security\s+clearance",
    r"position\s+requires\s+access\s+to\s+u\.s\.\s+export.controlled",
    r"clearance type.*secret",
    r"must be a u\.s\. citizen or national.*permanent resident",
    r"proof of.*us citizenship.*permanent.*residency",
    r"proof of.*(?:us citizenship|permanent.*residency|protected individual)",
    r"capacity to serve in compliance with u\.s\. export controls",
    r"contingent upon.*capacity to serve.*export",
    r"ros2\s+\(robot operating system\)",
    r"agv\s+slam",
    r"willing.*able.*obtain.*(?:top secret|ts.sci|secret clearance)",
    r"must be eligible.*export.*authoriz",
    r"itar.*applicant must be",
    r"applicant must be.*(?:us citizen|u\.s\. citizen|lawful.*permanent resident)",
    r"must be a.*u\.s\. citizen.*national.*green card",
    r"publication\s+record\s+in\s+top",
    r"publications?\s+at\s+top\s+(?:ml|nlp|ai|cv)\s+(?:conference|venue)",
    r"published\s+(?:papers?|research)\s+(?:at|in)\s+(?:cvpr|iccv|neurips|icml|iclr|acl|emnlp|aaai|siggraph)",
    r"one\s+or\s+more\s+publications",
    r"minimum\s+of\s+two\s+reference\s+letters",
    r"submit.*reference\s+letters",
    r"must be eligible for.*export authoriz",
    r"export-controlled.*information",
    r"this position requires access to u\.s\. export-controlled",
    r"requires access to.*export.controlled information",

]

GMAIL_SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
]

JOB_BOARD_DOMAINS = [
    "greenhouse",
    "lever.co",
    "workday",
    "ashbyhq",
    "smartrecruiters",
    "workable",
    "rippling",
    "myworkdayjobs",
    "telecom",
    "stream",
    "church",
    "chemical",
    "atp",
    "gdit",
    "hpiq",
    "beone",
    "bmwgroup",
    "dovercorporation",
    "lucidmotors",
    "radixuniversity",
    "cenow",
    "layerhealth",
    "us-erac",
    "berkley",
    "thenewyorktimes",
    "macomtech",
    "open tennis championships",
    "prairie view a&m university",
    "ryder integrated logistics, inc. (employer-payroll)",
    "westgate resorts",
    "rite-hite doors",
    "iheartmedia management services, inc. | ams",
    "robert w. baird &",
    "heidelberg materials us",
    "t.y. lin international",
    "delta dental plan of michigan",
    "watkins manufacturing",
    "cerro wire",
    "mayor and city council of baltimore",
    "nidec motor",
    "edxn.fa.us2",
    "greenhouse",
    "government employees health association",
    "charles river analytics",
    "medical systems information",
    "solopulse",
    "welo data",
    "onto innovation inc-us",
    "thermo finnigan",
    "stewart title guaranty company - united states",
    "hartford fire ins",
    "applytojob",
    "jobvite",
    "paycomonline",
    "metroplusjobs",
    "ziprecruiter",
    "department of consumer & business",
    "analytical mechanics",
    "taylor dm brands",
    "federal",
    "aerospace",
    "icims.com",
    "myworkdayjobs",
    "simplify.jobs",
    "linkedin.com/jobs",
    "jobright.ai",
    "ziprecruiter.com",
]

STATUS_COLORS = {
    "Not Applied":  {"red": 0.6,  "green": 0.76, "blue": 1.0},
    "Tailor":       {"red": 1.0,  "green": 0.65, "blue": 0.3},   # orange - resume needs tailoring
    "Applied":      {"red": 0.58, "green": 0.93, "blue": 0.31},
    "Rejected":     {"red": 0.97, "green": 0.42, "blue": 0.42},
    "Screening":    {"red": 1.0,  "green": 0.85, "blue": 0.4},
    "OA Round 1":   {"red": 1.0,  "green": 0.95, "blue": 0.4},
    "OA Round 2":   {"red": 1.0,  "green": 0.95, "blue": 0.4},
    "Interview 1":  {"red": 0.82, "green": 0.93, "blue": 0.94},
    "Interview 2":  {"red": 0.6,  "green": 0.85, "blue": 0.95},
    "Assessment":   {"red": 0.89, "green": 0.89, "blue": 0.89},
    "Offer accepted": {"red": 0.16, "green": 0.65, "blue": 0.27},
}


def get_state_for_city(city_name):
    if US_ZIPCODE_AVAILABLE and _search_engine:
        try:
            results = _search_engine.by_city(city_name.strip().title())
            if results:
                states = [r.state for r in results if r.state]
                if states:
                    from collections import Counter

                    return Counter(states).most_common(1)[0][0]
        except Exception as _e:
            logging.debug("suppressed: %s", _e)
            pass
    return CITY_TO_STATE_FALLBACK.get(city_name.lower())


def validate_us_state_code(state_code):
    if US_LIBRARY_AVAILABLE:
        try:
            return us_library.states.lookup(state_code) is not None
        except Exception as _e:
            logging.debug("suppressed: %s", _e)
            pass
    return state_code.upper() in US_STATES_FALLBACK


def get_canadian_province(text):
    if PYCOUNTRY_AVAILABLE:
        try:
            for subdivision in pycountry.subdivisions.get(country_code="CA"):
                code = subdivision.code.split("-")[1]
                if code in text.upper() or subdivision.name.lower() in text.lower():
                    return code
        except Exception as _e:
            logging.debug("suppressed: %s", _e)
            pass
    for province_name, code in CANADA_PROVINCE_NAMES.items():
        if province_name in text.lower():
            return code
    return None


def extract_domain_and_subdomain(url):
    if TLDEXTRACT_AVAILABLE:
        try:
            extracted = tldextract.extract(url)
            return extracted.subdomain, f"{extracted.domain}.{extracted.suffix}"
        except Exception as _e:
            logging.debug("suppressed: %s", _e)
            pass
    import re

    match = re.search(r"https?://(?:www\.)?([^/]+)", url)
    if match:
        parts = match.group(1).split(".")
        return (parts[0], ".".join(parts[-2:])) if len(parts) >= 2 else (None, None)
    return None, None


def fuzzy_match_company(candidate, known_companies, threshold=85):
    if not RAPIDFUZZ_AVAILABLE:
        return None
    try:
        result = process.extractOne(candidate, known_companies, scorer=fuzz.ratio)
        return result[0] if result and result[1] >= threshold else None
    except Exception as _e:
        logging.debug("suppressed: %s", _e)
        return None


def parse_date_flexible(date_string):
    if not DATEUTIL_AVAILABLE:
        return None
    try:
        return dateutil_parser.parse(date_string, fuzzy=True)
    except Exception as _e:
        logging.debug("suppressed: %s", _e)
        return None


def is_valid_url(url):
    if VALIDATORS_AVAILABLE:
        try:
            return validators.url(url) == True
        except Exception as _e:
            logging.debug("suppressed: %s", _e)
            pass
    import re

    return bool(re.match(r"https?://.+\..+", url))


def normalize_unicode(text):
    if UNIDECODE_AVAILABLE:
        try:
            return unidecode_func(text)
        except Exception as _e:
            logging.debug("suppressed: %s", _e)
            pass
    return text.replace("é", "e").replace("è", "e").replace("à", "a").replace("ô", "o")


def get_city_state_from_zipcode(zipcode):
    if PGEOCODE_AVAILABLE and _pgeocode_nomi:
        try:
            result = _pgeocode_nomi.query_postal_code(zipcode)
            if result is not None and not result.isna().all():
                return result.get("place_name"), result.get("state_code")
        except Exception as _e:
            logging.debug("suppressed: %s", _e)
            pass
    return None, None


# FIX 12: moved from run_aggregator.py
GREENHOUSE_COMPANY_MAP = {
    "tenstorrentuniversity": "Tenstorrent",
    "alarmcom": "Alarm.com",
    "skyryse": "Skyryse",
    "trumid": "Trumid",
    "leagueinc": "League",
    "fccincinnati": "FC Cincinnati",
    "antora": "Antora Energy",
    "samsungresearchamericainternship": "Samsung Research America",
    "point72": "Point72",
    "sonatus": "Sonatus",
}

# HQ fallback locations for top companies — used when all extraction fails
COMPANY_HQ = {
    "apple": "Cupertino, CA",
    "gusto": "San Francisco, CA",
    "asm international": "San Jose, CA",
    "ptc": "Boston, MA",
    "github": "San Francisco, CA",
    "sentra": "New York, NY",
    "susquehanna international group": "Philadelphia, PA",
    "howard hughes medical institute (hhmi)": "Chevy Chase, MD",
    "deloitte": "New York, NY",
    "figma": "San Francisco, CA",
    "verkada": "San Mateo, CA",
    "verition": "Greenwich, CT",
    "acadian asset": "Boston, MA",
    "acadian asset management": "Boston, MA",
    "zip": "San Francisco, CA",
    "dryft": "San Francisco, CA",
    "ixl learning": "San Mateo, CA",
    "aloyoga": "Los Angeles, CA",
    "praxent": "Austin, TX",
    "spot & tango": "New York, NY",
    "m13": "Los Angeles, CA",
    "laxir": "San Francisco, CA",
    "tive": "Boston, MA",
    "apex analytix": "Greensboro, NC",
    "concept plus": "Reston, VA",
    "medical informatics engineering": "Fort Wayne, IN",
    "intermountain health": "Salt Lake City, UT",
    "toshiba": "Irvine, CA",
    "culture biosciences": "South San Francisco, CA",
    "healthfirst": "New York, NY",
    "rocketlawyer": "San Francisco, CA",
    "earnin": "Palo Alto, CA",
    "billiontoone": "Menlo Park, CA",
    "axos bank": "San Diego, CA",
    "sentry": "San Francisco, CA",
    "ditto": "San Francisco, CA",
    "layer health": "Boston, MA",
    "arcellx": "Gaithersburg, MD",
    "skydio": "San Mateo, CA",
    "moloco": "Redwood City, CA",
    "cohere": "San Francisco, CA",
    "together ai": "San Francisco, CA",
    "neuralink": "Fremont, CA",
    "rigetti": "Berkeley, CA",
    "psiquantum": "Palo Alto, CA",
    "block": "San Francisco, CA",
    "stripe": "San Francisco, CA",
    "notion": "San Francisco, CA",
    "linear": "San Francisco, CA",
    "roblox": "San Mateo, CA",
    "doordash": "San Francisco, CA",
    "airbnb": "San Francisco, CA",
    "lyft": "San Francisco, CA",
    "pinterest": "San Francisco, CA",
    "square": "San Francisco, CA",
    "google": "Mountain View, CA",
    "meta": "Menlo Park, CA",
    "amazon": "Seattle, WA",
    "microsoft": "Redmond, WA",
    "nvidia": "Santa Clara, CA",
    "netflix": "Los Gatos, CA",
    "salesforce": "San Francisco, CA",
    "adobe": "San Jose, CA",
    "intel": "Santa Clara, CA",
    "amd": "Santa Clara, CA",
    "qualcomm": "San Diego, CA",
    "uber": "San Francisco, CA",
    "databricks": "San Francisco, CA",
    "snowflake": "Bozeman, MT",
    "palantir": "Denver, CO",
    "openai": "San Francisco, CA",
    "twitter": "San Francisco, CA",
    "linkedin": "Sunnyvale, CA",
    "oracle": "Austin, TX",
    "ibm": "Armonk, NY",
    "cisco": "San Jose, CA",
    "vmware": "Palo Alto, CA",
    "servicenow": "Santa Clara, CA",
    "workday": "Pleasanton, CA",
    "intuit": "Mountain View, CA",
    "paypal": "San Jose, CA",
    "ebay": "San Jose, CA",
    "coinbase": "Remote",
    "robinhood": "Menlo Park, CA",
    "plaid": "San Francisco, CA",
    "asana": "San Francisco, CA",
    "zoom": "San Jose, CA",
    "slack": "San Francisco, CA",
    "dropbox": "San Francisco, CA",
    "twilio": "San Francisco, CA",
    "cloudflare": "San Francisco, CA",
    "atlassian": "San Francisco, CA",
    "hubspot": "Cambridge, MA",
    "datadog": "New York, NY",
    "mongodb": "New York, NY",
    "jpmorgan": "New York, NY",
    "goldman sachs": "New York, NY",
    "morgan stanley": "New York, NY",
    "bloomberg": "New York, NY",
    "t-mobile": "Bellevue, WA",
    "spacex": "Hawthorne, CA",
    "tesla": "Austin, TX",
    "rivian": "Normal, IL",
    "waymo": "Mountain View, CA",
    "cruise": "San Francisco, CA",
    "capital one": "McLean, VA",
    "american express": "New York, NY",
    "visa": "Foster City, CA",
    "mastercard": "Purchase, NY",
    "pwc": "New York, NY",
    "accenture": "New York, NY",
    "mckinsey": "New York, NY",
    "bain": "Boston, MA",
    "bcg": "Boston, MA",
}

GARBAGE_COMPANY_NAMES = {
    "rts",
    "rts careers",
    "icf jobs",
    "myworkdayjobs",
    "www",
    "job-boards",
    "company",
    "unknown",
    "careers",
    "jobs",
    "external",
    "portal",
    "applicant",
    "job-boards.greenhouse.io",
    "job-boards.eu.greenhouse.io",
    "your future starts here",
    "t commission",
    "corporate office",
    "learning",
    "insurance services",
    "gardacp",
    "fiveringsllc",
    "oakland",
    "3s business",
    "usa",
    "us",
    "worldwide",
    "intelligent solutions",
    "caliber holdings",
    "cardinal health 5",
    "beone medicines usa",
    "vernova",
    "calix north america",
    "sono",
    "amr-jones lang lasalle americas",
    "company 19 - john hancock life insurance company (u.s.a.)",
    "laboratories",
    "international gmbh",
    "management services",
    "fintech services",
    "employment services",
    "marketing",
    "ats",
    "retail markets",
    "y99000 general electric",
    "myworkdaysite",
    "information",
    "bank usa",
    "4001us00 dual north america",
    "4001us00",
    "c_001 transaction network",
    "re:car",
    "international gmbh",
    "information",
    "simplify",
    "applytojob",
    "campusopportunities",
    "candidate experience site",
    "career opportunities",
    "job opportunities",
    "amazon.jobs",
    "bamboohr17",
    "klaviyocampus",
    "expertnetwork",
    "userapps.support",
    "emerging talent",
    "your future starts here",
    "the future of health starts with you",
    "login",
    "home",
    "cloud",
    "health",
    "vision",
    "systems",
    "americas",
    "corporation",
    "corporation n.a.",
    "inc",
    "enterprise partners",
    "depot management",
    "fitness solutions",
    "power the world",
    "investor services",
    "insurance and financial services",
    "health care service",
    "general services",
    "our technology",
    "the dna of tech.™",
    "mhicareers",
    "jobs2web",
    "jobs.eu",
    "escholar",
    "bxp",
    "udemybedi",
    "roboforce",
    "dustyrobotics",
    "cloudforce",
    "situsaac",
    "realmalliance",
    "smartrecruiters",
    "metroplusjobs",
    "paycomonline",
    "able",
    "federal",
    "employment security commission",
    "marathon refining logistics",
    "smx",
    "unavailable",
    "candidate experience page",
    "our technology",
    "the dna of tech.",
    "login",
    "depot management",
    "fitness solutions",
    "power the world",
    "enterprise partners",
    "generac power systems",
    "precision healthcare",
    "research",
    "icims",
    "taleo",
    "greenhouse",
    "cummins talent acquisition",
}


# Moved from run_aggregator.py
# Special characters that appear in titles due to encoding issues
TITLE_ENCODING_FIXES = {
    "\u00e2\u20ac\u201c": "-",   # em dash encoded
    "\u00e2\u20ac\u2122": "'",   # apostrophe encoded
    "\u00e2\u20ac\u0153": '"',   # left quote encoded
    "\u00e2\u20ac": "",            # partial encoding artifact
    "\u00e2\xb3": "",              # hourglass character
    "\u00e2": "",                   # stray encoding artifact
    "\u2193": "",                   # down arrow
    "\u2713": "",                   # checkmark
    "&amp;": "&",
    "&lt;": "<",
    "&gt;": ">",
}

COMPANY_NAME_FIXES = {
    "pg&e": "PG&E",
    "tinder": "Match Group",
    "matchgroup": "Match Group",
    "church": "CVS Health",
    "chemical": "Dow Chemical",
    "aerospace": "The Aerospace Corporation",
    "stream": "Workstream",
    "telecom": "Unknown",
    "rippling": "Unknown",
    "tri-c": "Tri-C Community College",
    "cenow": "CenoW",
    "atp": "ATPCO",
    "gdit": "General Dynamics IT",
    "cloudforce": "Unknown",
    "myworkdayjobs": "Unknown",
    "beone": "BeiGene",
    "bmwgroup": "BMW Group",
    "dovercorporation": "Dover Corporation",
    "lucidmotors": "Lucid Motors",
    "thenewyorktimes": "The New York Times",
    "radixuniversity": "Radix",
    "macomtech": "MACOM",
    "hpiq": "HP",
    "us-erac": "Enterprise",
    "berkley": "W.R. Berkley",
    "ryder integrated logistics, inc. (employer-payroll)": "Ryder",
    "iheartmedia management services, inc. | ams": "iHeartMedia",
    "robert w. baird &": "Robert W. Baird",
    "prairie view a&m university": "Prairie View A&M",
    "mayor and city council of baltimore": "City of Baltimore",
    "delta dental plan of michigan": "Delta Dental",
    "heidelberg materials us": "Heidelberg Materials",
    "amat": "Applied Materials",
    "hp": "HP",
    "usa": "Unknown",
    "us": "Unknown",
    "worldwide": "Unknown",
    "tik tok": "TikTok",
    "tmobile": "T-Mobile",
    "job-boards": "Unknown",
    "sono": "Sonoco",
    "vernova": "GE Vernova",
    "wsp": "WSP",
    "adp": "ADP",
    "abb": "ABB",
    "sas": "SAS",
    "exl": "EXL",
    "bmo": "BMO",
    "rtx": "RTX",
    "months": "Unknown",
    "cmt": "CMT Digital",
    "energyhub": "EnergyHub",
    "premierautomation": "Premier Automation",
    "veeamsoftware": "Veeam Software",
    "nxp": "NXP",
    "impinjexternal": "Impinj",
    "abacusinsights": "Abacus Insights",
    "sigmacomputing": "Sigma Computing",
    "aloyoga": "Alo Yoga",
    "disneyland": "Disney",
    "y99000 general electric": "GE Aerospace",
    "assaabloy": "ASSA ABLOY",
    "ats": "Unknown",
    "retail markets": "Unknown",
    "gts": "GTS",
    "aegworldwide": "AEG",
    "aeg": "AEG",
    "colliers engineering & design home": "Colliers Engineering",
    "simplify": "Unknown",
    "intelligent solutions": "CCC Intelligent Solutions",
    "caliber holdings": "Caliber Collision",
    "cardinal health 5": "Cardinal Health",
    "beone medicines usa": "BeiGene",
    "calix north america": "Calix",
    "(marvell semiconductor inc.) us": "Marvell",
    "aofl": "Age of Learning",
    "pge": "PG&E",
    "001_bcbsa blue cross and blue shield association": "Blue Cross Blue Shield Association",
    "001_bcbsa": "Blue Cross Blue Shield Association",
    "bcbsa": "Blue Cross Blue Shield Association",
    "bank usa": "CIBC",
    "cibc us": "CIBC",
    "myworkdaysite": "Unknown",
    "information": "Unknown",
    "general dynamics information technology": "GDIT",
    "logistics management institute": "LMI",
    "lmi": "LMI",
    "4001us00 dual north america": "GDIT",
    "4001us00": "GDIT",
    "simmons bank": "Unknown",
    "bloomberg philanthropies": "Unknown",
    "ensemble rcm": "Ensemble Health Partners",
    "nordson efd": "Nordson",
    "eversource energy service": "Eversource Energy",
    "rakuten marketing": "Rakuten Advertising",
    "rakuten advertising": "Rakuten Advertising",
    "lumosfiber": "Lumos Fiber",
    "coherent corp. us": "Coherent",
    "coherent corp": "Coherent",
    "coherent corp.": "Coherent",
    "coherentcorpus": "Coherent",
    "coherent corporation": "Coherent",
    "tokyo electron u.s.holdings": "Tokyo Electron US Holdings",
    "cae": "CAE",
    "ibm": "IBM",
    "aecom": "AECOM",
    "hp, inc": "HP",
    "hewlett packard (hp)": "HP",
    "hp inc": "HP",
    "lilly usa": "Eli Lilly",
    "microchip -": "Microchip Technology",
    "microchip - usa": "Microchip Technology",
    "microchip technology inc": "Microchip Technology",
    "microchip technology": "Microchip Technology",
    "micron technology": "Micron Technology",
    "micron": "Micron Technology",

    # Concatenated/slug names
    "lifeattiktok": "TikTok",
    "thetradedesk": "The Trade Desk",
    "purestorage": "Pure Storage",
    "phoenixcontact": "Phoenix Contact",
    "andurilindustries": "Anduril Industries",
    "harbingermotors": "Harbinger Motors",
    "detroitlions": "Detroit Lions",
    "openfarminc": "Open Farm",
    "gametimeunited": "GameTime United",
    "bluelabsanalyticsinc": "Blue Labs Analytics",
    "humansignal": "HumanSignal",
    "kairospower": "Kairos Power",
    "aevexaerospace": "AEVEX Aerospace",
    "agwestfarmcredit": "AgWest Farm Credit",
    "laskoproducts": "Lasko Products",
    "sparksoftcorporation": "SparkSoft Corporation",
    "cliftonlarsonallen": "CliftonLarsonAllen",
    "fccincinnati": "FC Cincinnati",
    "stcu": "STCU",
    "bcbsm": "BCBSM",
    "saltxc": "SaltXC",
    "viseai": "Vise AI",
    "eightfold": "Eightfold AI",
    "respec": "RESPEC",
    "anglogoldashanti": "AngloGold Ashanti",
    "polyai": "PolyAI",
    "patientpoint": "PatientPoint",
    "betterhelpcom": "BetterHelp",
    "bamboohr17": "Unknown",
    "klaviyocampus": "Klaviyo",
    "skhynixamerica": "SK Hynix America",
    "bluestaq": "Bluestaq",
    "campusopportunities": "Unknown",
    "expertnetwork": "Unknown",
    "userapps.support": "Unknown",
    "applytojob": "Unknown",
    "mhicareers": "MHI",
    "acuityinc": "Acuity",

    # Incomplete names
    "the walt disney": "The Walt Disney Company",
    "disney": "The Walt Disney Company",
    "bc": "Unknown",
    "at micron": "Micron Technology",

    # Code-prefixed names
    "us001 genmab us": "Genmab",
    "hc1316 ge precision healthcare": "GE HealthCare",
    "a01098 ge vernova international": "GE Vernova",
    "ls3002 ge vernova operations": "GE Vernova",
    "cp0052 ge healthcare technologies canada": "GE HealthCare",
    "f4479 alcentra limited": "Alcentra",
    "f1600 legg mason & co.": "Legg Mason",
    "(0207) sanofi us services": "Sanofi",
    "6j2 - zoetis services": "Zoetis",
    "zmc-us ma zoll medical": "ZOLL Medical",
    "us01 valeo north america": "Valeo",
    "us10064-aal american air liquide": "Air Liquide",
    "*us amr-jones lang lasalle americas": "JLL",
    "amr-jones lang lasalle americas": "JLL",
    "jones lang lasalle real estate services": "JLL",
    "cmeol cme operations": "CME Group",
    "sapinc saputo": "Saputo",
    "le043 quality technology services": "QTS",
    "co_31 bird construction management services": "Bird Construction",
    "us_orb_4000​ orbis": "Orbis",
    "phinia delphi uk ltd -": "PHINIA",
    "robert bosch venture capital": "Bosch",
    "bosch group": "Bosch",

    # Non-US that slip through
    "autodesk canada co.": "Autodesk",
    "canadian imperial bank of commerce (canada)": "Unknown",
    "bank of montreal": "Unknown",
    "royal bank of canada": "Unknown",
    "canadian tire bank": "Unknown",
    "canadian tire": "Unknown",
    "ontario lottery and gaming": "Unknown",
    "sun life": "Unknown",
    "cogeco connexion": "Unknown",
    "hitachi rail gts canada": "Unknown",
    "canada wind river": "Wind River",
    "canada operations ulc": "Unknown",
    "brock university": "Unknown",
    "mcgill university": "Unknown",
    "strathcona county": "Unknown",
    "acciona infrastructure canada": "Unknown",
    "first canadian title company limited": "Unknown",
    "university health network": "Unknown",
    "alayacare": "Unknown",
    "league": "Unknown",
    "leonardo uk": "Unknown",
    "boeing united kingdom limited": "Unknown",
    "anglian water services": "Unknown",
    "aveva software": "AVEVA",
    "thales uk limited": "Unknown",
    "radius limited": "Unknown",
    "penny & giles controls limited": "Unknown",
    "limson trading": "Unknown",
    "sky star eight limited": "Unknown",
    "image frame investment (uk) limited": "Unknown",
    "apollo fire detectors limited": "Unknown",
    "blackberry limited": "BlackBerry",
    "rolls-royce motorcars": "Rolls-Royce",
    "rolls royce": "Rolls-Royce",
    "ultra pcs": "Unknown",
    "the data school powered by the information lab": "Unknown",
    "evonik operations gmbh_28": "Evonik",
    "international gmbh": "Unknown",
    "wheely": "Unknown",
    "keyrock": "Unknown",
    "john lewis": "Unknown",
    "primetals technologies": "Primetals Technologies",
    "birmingham": "Unknown",

    # Misc fixes
    "sanofi": "Sanofi",
    "zoetis": "Zoetis",
    "valeo": "Valeo",
    "ge vernova": "GE Vernova",
    "ge precision healthcare": "GE HealthCare",
    "ge healthcare technologies canada": "GE HealthCare",
    "ge healthcare": "GE HealthCare",
    "genmab us": "Genmab",
    "us001 genmab": "Genmab",
    "field-ai": "Field AI",
    "field ai": "Field AI",
    "smx": "Unknown",
    "3s business": "Unknown",
    "g2": "G2",
    "ercot": "ERCOT",
    "fintech services": "Unknown",
    "management services": "Unknown",
    "employment services": "Unknown",
    "credit union": "Unknown",
    "health": "Unknown",
    "vision": "Unknown",
    "systems": "Unknown",
    "research": "Unknown",
    "cloud": "Unknown",
    "corporation": "Unknown",
    "learning": "Unknown",
    "americas": "Unknown",
    "inc": "Unknown",
    "laboratories": "Unknown",
    "home": "Unknown",
    "login": "Unknown",
    "taleo": "Unknown",
    "icims": "Unknown",
    "greenhouse": "Unknown",
    "cummins talent acquisition": "Cummins",
    "samsung electronics america": "Samsung",
    "samsung research america": "Samsung Research America",
    "harman becker automotive systems": "HARMAN",
    "harman international": "HARMAN",
    "merck sharp & dohme": "Merck",
    "rheem manufacturing company": "Rheem",
    "emerson electric": "Emerson",
    "bhg gr harman": "HARMAN",
    "fiserv solutions": "Fiserv",
    "the elevance health companies": "Elevance Health",
    "the aerospace": "The Aerospace Corporation",
    "the wonderful": "The Wonderful Company",
    "logistic management institute": "LMI",
    "borgwarner pds (usa)": "BorgWarner",
    "first american trust, f.s.b.": "First American",
    "baker tilly advisory group, lp": "Baker Tilly",
    "bc forward": "BCforward",
    "drivewealth": "DriveWealth",
    "drivewealthadvisers": "DriveWealth",
    "regal beloit america": "Regal Rexnord",
    "momentive performance materials 1015": "Momentive",
    "orb": "Orbis",
    "us_orb": "Orbis",
    "hcompany": "HCompany",
    "grad": "Unknown",
    "unavailable": "Unknown",
    "jobs2web": "Unknown",
    "c_001 transaction network": "TNS",
    "transaction network services": "TNS",
    "tnsi": "TNS",
    "doubleverify": "DoubleVerify",
    "genmab a/s": "Genmab",
    "flextronics international usa": "Flex Ltd",
    "flextronics": "Flex Ltd",
    "flex ltd": "Flex Ltd",
    "msx international": "MSX International",
    "re:car": "Unknown",
    "mbrdna": "Mercedes-Benz R&D North America",
    "fei": "Thermo Fisher Scientific",
    "sanmar": "SanMar",
    "rfsmart": "RFSmart",
    "coverdash": "Coverdash",
    "shorepoint": "ShorePoint",
    "erickson senior living": "Erickson Senior Living",
    "farsight ai": "Farsight AI",
    "quadric dot i o inc": "Quadric",
    "dat": "DAT Freight & Analytics",
    "wwfus": "World Wildlife Fund",
    "lilasciences": "Lila Sciences",
    "temporaltechnologies": "Temporal Technologies",
    "foxnew fox news network": "Fox News",
    "podium81": "Podium",
    "starsling": "StarSling",
    "plus-2": "PlusAI",
    "plus 2": "PlusAI",
    "bytedance": "ByteDance",
    "ispottv": "iSpot.tv",
    "nationalpublicradioinc": "NPR",
    "microchiphr": "Microchip Technology",
    "magnite, inc.us": "Magnite",
    "tencent america": "Tencent",
    "1600 nio usa": "NIO",
    "ameres": "Ameresco",
    "murphy-brown": "Smithfield Foods",
    "ekkf": "Samsonite",
    "tyson shared": "Tyson Foods",
    "renault techno roumanie": "Renault",
    "ejia": "S&C Electric Company",
    "ken garff automotive group": "Ken Garff",
    "scaleai": "Scale AI",
    "oneclick ui": "Bosch",
    "rivianvw.tech": "XPENG",
    "magnera corporation": "Magnera",
    "leonardodrs": "Leonardo DRS",
    "fourhands": "FourHands",
    "ancestry.com operations": "Ancestry",
    "ancestry.com": "Ancestry",
    "snap finance": "Snap Finance",
    "dpr family of companies": "DPR Construction",
    "valeo north america": "Valeo",
}

# City to state mapping for location normalization
CITY_TO_STATE_EXTRA = {
    "clearwater": "FL",
    "raleigh": "NC", "durham": "NC", "charlotte": "NC", "chapel hill": "NC",
    "san francisco": "CA", "san jose": "CA", "santa clara": "CA", "sunnyvale": "CA",
    "mountain view": "CA", "palo alto": "CA", "menlo park": "CA", "cupertino": "CA",
    "redwood city": "CA", "san mateo": "CA", "emeryville": "CA", "oakland": "CA",
    "los angeles": "CA", "san diego": "CA", "irvine": "CA", "santa monica": "CA",
    "seattle": "WA", "bellevue": "WA", "redmond": "WA", "kirkland": "WA",
    "new york": "NY", "new york city": "NY", "brooklyn": "NY", "manhattan": "NY",
    "boston": "MA", "cambridge": "MA", "waltham": "MA", "somerville": "MA",
    "chicago": "IL", "austin": "TX", "dallas": "TX", "houston": "TX",
    "denver": "CO", "boulder": "CO", "atlanta": "GA", "miami": "FL",
    "portland": "OR", "pittsburgh": "PA", "philadelphia": "PA",
    "minneapolis": "MN", "detroit": "MI", "ann arbor": "MI",
    "chaska": "MN", "eden prairie": "MN", "bloomington": "MN",
    "washington": "DC", "arlington": "VA", "mclean": "VA",
    "salt lake city": "UT", "phoenix": "AZ", "scottsdale": "AZ",
    "nashville": "TN", "columbus": "OH", "cleveland": "OH", "cincinnati": "OH",
    "indianapolis": "IN", "milwaukee": "WI", "madison": "WI",
    "kansas city": "MO", "st louis": "MO", "omaha": "NE",
    "new orleans": "LA", "memphis": "TN", "louisville": "KY",
    "hartford": "CT", "bridgeport": "CT", "stamford": "CT", "windsor": "CT",
    "jersey city": "NJ", "newark": "NJ", "princeton": "NJ",
    "richmond": "VA", "norfolk": "VA", "charlottesville": "VA",
    "baltimore": "MD", "bethesda": "MD", "silver spring": "MD",
    "san antonio": "TX", "fort worth": "TX", "plano": "TX",
    "sacramento": "CA", "fresno": "CA", "san ramon": "CA", "los gatos": "CA",
    "tucson": "AZ", "tempe": "AZ", "mesa": "AZ",
    "albuquerque": "NM", "las vegas": "NV", "reno": "NV",
    "boise": "ID", "spokane": "WA", "tacoma": "WA", "lexington": "KY",
    "oklahoma city": "OK", "tulsa": "OK",
    "birmingham": "AL", "montgomery": "AL",
    "little rock": "AR", "fayetteville": "AR",
    "columbia": "SC", "charleston": "SC",
    "jackson": "MS", "biloxi": "MS",
    "des moines": "IA", "cedar rapids": "IA",
    "sioux falls": "SD", "fargo": "ND",
    "billings": "MT", "missoula": "MT",
    "cheyenne": "WY", "casper": "WY",
    "anchorage": "AK", "juneau": "AK",
    "honolulu": "HI",
    "burlington": "VT", "montpelier": "VT",
    # "portland": "ME" removed - it overrode the earlier "portland": "OR",
    # and Portland in tech postings is Oregon far more often than Maine.
    "bangor": "ME",
    "manchester": "NH", "concord": "NH",
    "providence": "RI", "newport": "RI",
    "dover": "DE", "wilmington": "DE",
}

# ── Self-healing: regex patterns to strip internal company code prefixes ──
# Matches patterns like: "HC1316 ", "US001 ", "(0207) ", "6J2 - ", "*US AMR-"
# Applied during company name normalization in processors.py
COMPANY_CODE_PREFIX_PATTERN = r"""
    (?:
        ^[\*\(]?                          # Optional leading * or (
        [A-Z]{0,4}[\-_]?                  # Optional country code
        \d{2,6}                           # Numeric code
        [\-_ ]+                           # Separator
    |
        ^\([A-Z0-9]{3,6}\)\s+             # (CODE) prefix
    |
        ^[A-Z]{2,4}\d{2,4}\s+            # LETTER+NUMBER prefix like HC1316
    |
        ^[A-Z]{1,3}_\d{2,4}\s+           # LETTER_NUMBER like CO_31
    |
        ^[\*]+[A-Z]{2}\s+[A-Z]           # *US AMR- style
    )
"""

# Aliases for backward compatibility (run_aggregator.py uses COMPANY_BLACKLIST)
COMPANY_BLACKLIST = PLATFORM_BLACKLIST
COMPANY_BLACKLIST_REASONS = PLATFORM_BLACKLIST_REASONS


# ── H1B Sponsorship Database ──
H1B_KNOWN_SPONSORS = {
    "google", "meta", "amazon", "microsoft", "apple", "nvidia", "tesla",
    "stripe", "coinbase", "databricks", "snowflake", "palantir",
    "salesforce", "oracle", "ibm", "intel", "cisco", "adobe",
    "uber", "lyft", "airbnb", "doordash", "instacart",
    "tiktok", "bytedance", "pinterest", "snap", "reddit",
    "robinhood", "plaid", "affirm", "chime", "sofi",
    "twilio", "okta", "datadog", "cloudflare", "mongodb",
    "elastic", "confluent", "hubspot", "figma", "notion",
    "airtable", "asana", "canva", "miro", "loom",
    "openai", "anthropic", "cohere", "scale ai",
    "ramp", "brex", "toast", "squarespace",
    "crowdstrike", "sentinelone", "zscaler",
    "neuralink", "rivian", "lucid motors", "aurora",
    "nuro", "cruise", "zoox", "motional",
    "cerebras", "groq", "sambanova", "together ai",
    "jpmorgan", "goldman sachs", "morgan stanley", "citadel",
    "two sigma", "jane street", "de shaw",
    "bloomberg", "capital one", "american express",
    "walmart", "target", "costco",
    "johnson & johnson", "pfizer", "merck",
    "deloitte", "accenture", "mckinsey", "bcg",
    "spotify", "netflix", "discord", "slack",
    "rippling", "verkada", "anduril", "vanta",
    "samsara", "draftkings", "hubspot", "pinterest",
    "dell", "hp", "vmware", "qualcomm", "broadcom",
    "micron", "applied materials", "lam research",
    "visa", "mastercard", "paypal", "block",
    "twitter", "x", "linkedin", "indeed",
    "zillow", "redfin", "opendoor",
    "coupang", "grab", "gojek",
    "bytedance", "tencent", "baidu",
    "sap", "workday", "servicenow",
    "palo alto networks", "fortinet", "splunk",
    "atlassian", "gitlab", "hashicorp",
    "mongodb", "cockroachdb", "redis",
    "vercel", "supabase", "postman",
    "waymo", "argo ai", "plus ai", "xpeng",
    "bill.com", "marqeta", "nuvei",
    "docusign", "dropbox", "box",
    "lendbuzz", "prosper", "earnin",
}

H1B_NO_SPONSOR = {
    "spacex",
    "boeing", "lockheed martin", "northrop grumman",
    "raytheon", "rtx", "general dynamics", "bae systems",
    "l3harris", "leidos", "saic", "caci", "mantech",
    "kbr", "amentum", "gdit", "peraton",
    "travelers", "progressive", "geico",
    "cox automotive", "cox communications",
    "excelitas", "hermeus", "captivation",
    "sierra space", "parsons", "textron",
}

H1B_SPONSOR_JD_YES = [
    r"(?:visa|h-?1b|immigration)\s+(?:sponsorship|support)\s+(?:is\s+)?(?:available|offered|provided)",
    r"(?:we|company|will)\s+(?:sponsor|provide)\s+(?:visa|h-?1b|work\s+authorization)",
    r"(?:cpt|opt)\s+(?:accepted|welcome|eligible)",
    r"open\s+to\s+(?:international|all)\s+(?:students?|candidates?)",
    r"sponsorship\s+(?:is\s+)?available",
    r"willing\s+to\s+sponsor",
]

H1B_SPONSOR_JD_NO = [
    r"(?:no|not|without|unable)\s+(?:to\s+)?(?:provide|offer)\s+(?:visa|immigration|h-?1b)?\s*(?:sponsorship|support)",
    r"(?:must|should)\s+be\s+(?:legally\s+)?authorized\s+to\s+work.*(?:without|no).*sponsor",
    r"(?:not|no)\s+(?:eligible|available)\s+(?:for\s+)?(?:visa\s+)?sponsorship",
    r"(?:us|u\.s\.)\s+citizen(?:ship)?\s+(?:required|only)",
    r"permanent\s+(?:us|u\.s\.)\s+work\s+authorization\s+required",
    r"must\s+be\s+(?:a\s+)?(?:us|u\.s\.)\s+citizen",
    r"(?:unable|not\s+able)\s+to\s+sponsor",
    r"not\s+(?:eligible|open)\s+to\s+(?:candidates?\s+)?(?:on|requiring)\s+(?:opt|cpt|f-?1|student\s+visa)",
    r"authorization.*without.*(?:need|requiring).*sponsor",
    r"(?:not|isn'?t)\s+eligible\s+for\s+(?:visa\s+)?sponsorship",
    r"position\s+is\s+not\s+eligible\s+for\s+(?:visa\s+)?sponsorship",
]

# ── Known company career page search URLs ──
# Used when pipeline can't get direct job URL — gives career search instead of Google search
KNOWN_CAREER_URLS = {
    "american express": "https://aexp.eightfold.ai/careers/search?query=",
    "the coca-cola company": "https://careers.coca-colacompany.com/search-jobs?q=",
    "tesla": "https://www.tesla.com/careers/search/?query=",
    "apple": "https://jobs.apple.com/en-us/search?search=",
    "google": "https://www.google.com/about/careers/applications/jobs/results?q=",
    "meta": "https://www.metacareers.com/jobs?q=",
    "amazon": "https://www.amazon.jobs/en/search?base_query=",
    "microsoft": "https://careers.microsoft.com/v2/global/en/search?q=",
    "nvidia": "https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite?q=",
    "stripe": "https://stripe.com/jobs/search?q=",
    "databricks": "https://www.databricks.com/company/careers?q=",
    "salesforce": "https://careers.salesforce.com/en/jobs/?q=",
    "spotify": "https://www.lifeatspotify.com/jobs?query=",
    "netflix": "https://jobs.netflix.com/search?q=",
    "uber": "https://www.uber.com/us/en/careers/search/?q=",
    "lyft": "https://www.lyft.com/careers/search?q=",
    "airbnb": "https://careers.airbnb.com/search/?q=",
    "snap": "https://careers.snap.com/jobs?q=",
    "discord": "https://discord.com/careers?q=",
    "reddit": "https://www.redditinc.com/careers?q=",
    "doordash": "https://careers.doordash.com/search?q=",
    "plaid": "https://plaid.com/careers/?q=",
    "robinhood": "https://robinhood.com/careers/search?q=",
    "coinbase": "https://www.coinbase.com/careers/search?q=",
    "pinterest": "https://www.pinterestcareers.com/search-results?q=",
    "instacart": "https://instacart.careers/search?q=",
}

# Companies that are actually generic ATS slugs, not real company names
GENERIC_COMPANY_SLUGS = [
    "biotech", "careers", "jobs", "hiring", "talent",
    "recruitment", "opportunities", "openings",
]

COMPANY_BLACKLIST_PATTERNS = [
    "gulfstream", "solar turbines",
    "university of texas at austin", "north orange county",
    "community college", "school district",
    "children's hospital", "nationwide children",
]
