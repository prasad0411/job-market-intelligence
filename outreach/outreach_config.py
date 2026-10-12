#!/usr/bin/env python3
"""Outreach Pipeline — Configuration."""

import os, datetime

_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_DIR)

SHEETS_CREDS = os.path.join(_ROOT, ".local", "credentials.json")
GMAIL_CREDS = os.path.join(_ROOT, ".local", "gmail_credentials.json")
GMAIL_TOKEN = os.path.join(_ROOT, ".local", "gmail_token.pickle")
GMAIL_SCOPES = [
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/gmail.readonly",
]

SPREADSHEET = "H1B visa"
VALID_TAB = "Valid Entries"
OUTREACH_TAB = "Outreach Tracker"

V_COMPANY, V_TITLE, V_JOBID, V_RESUME, V_LOCATION = 2, 3, 6, 9, 8

O_HEADERS = [
    "Sr. No.",              # A (0)
    "Company",              # B (1)
    "Job Title",            # C (2)
    "Extract",              # D (3)
    "Job ID",               # E (4)
    "HM Name",              # F (5)
    "HM LinkedIn URL",      # G (6)
    "HM Email",             # H (7)
    "HM LinkedIn Msg",      # I (8)
    "Recruiter Name",       # J (9)
    "Recruiter LinkedIn URL",  # K (10)
    "Recruiter Email",      # L (11)
    "Rec LinkedIn Msg",     # M (12)
    "Send At",              # N (13)
    "Sent Date",            # O (14)
    "Notes",                # P (15)
    "Confidence",           # Q (16)
]

C = {
    "sr": 0,
    "company": 1,
    "title": 2,
    "extract": 3,
    "job_id": 4,
    "hm_name": 5,
    "hm_li": 6,
    "hm_email": 7,
    "hm_li_msg": 8,
    "rec_name": 9,
    "rec_li": 10,
    "rec_email": 11,
    "rec_li_msg": 12,
    "send_at": 13,
    "sent_dt": 14,
    "notes": 15,
    "confidence": 16,
}

SENDER_NAME = "Prasad Kanade"
SENDER_EMAIL = "prasadckanade@gmail.com"      # Gmail — used for bounce scanning only
MS_SENDER_EMAIL = "kanade.pra@northeastern.edu"  # Microsoft — used for sending
MS_SENDER_NAME = "Prasad Kanade"

# Microsoft public client ID (works with any M365 account, no Azure registration needed)
# This is Microsoft's own Office desktop client ID — universally trusted
MS_CLIENT_ID = "d3590ed6-52b3-4102-aeff-aad2292ab01c"
MS_AUTHORITY = "https://login.microsoftonline.com/common"
MS_SCOPES = ["https://graph.microsoft.com/Mail.Send"]

# ═══════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════
# ROLE-SPECIFIC EMAIL TEMPLATES
# ═══════════════════════════════════════════════════════════

HM_SUBJ = "Prasad Kanade \u2014 Application for {title} | {job_id}"

_HM_BODY_SDE = (
    "Hi {first},\n\n"
    "I recently applied for the {title} | {job_id} role at {company} and wanted to reach out "
    "directly. I've been following {company}'s engineering work closely and this role is exactly "
    "where I want to be.\n\n"
    "I bring around 2 years of production engineering experience building microservices, optimizing "
    "databases, and deploying cloud infrastructure. Not just academic projects, but systems running "
    "in production at scale. I'm also a published researcher (Springer) which shows I go beyond "
    "surface level and deliver rigorous, impactful work.\n\n"
    "I'm finishing my Master's in CS at Northeastern (3.66 GPA) and I'm ready to hit the ground "
    "running. That experience directly translates here and I'm confident I can contribute from "
    "day one.\n\n"
    "I would love the opportunity to discuss this further and demonstrate what I can bring. "
    "Resume attached.\n\n"
    "Best regards,\nPrasad Kanade"
)

_HM_BODY_ML = (
    "Hi {first},\n\n"
    "I recently applied for the {title} | {job_id} role at {company} and wanted to reach out "
    "directly. I've been following {company}'s work in AI/ML closely and this role is exactly "
    "where I want to be.\n\n"
    "I bring both published ML research (Springer) and around 2 years of production engineering "
    "experience. I don't just build models, I build systems that ship. That combination of research "
    "depth and real world delivery is what I'm bringing to this role.\n\n"
    "I'm finishing my Master's in CS at Northeastern (3.66 GPA) and I'm ready to hit the ground "
    "running. That experience directly translates here and I'm confident I can contribute from "
    "day one.\n\n"
    "I would love the opportunity to discuss this further and demonstrate what I can bring. Resume attached.\n\n"
    "Best regards,\nPrasad Kanade"
)

_HM_BODY_DA = (
    "Hi {first},\n\n"
    "I recently applied for the {title} | {job_id} role at {company} and wanted to reach out "
    "directly. I've been following {company}'s approach to data closely and this role is exactly "
    "where I want to be.\n\n"
    "I bring around 2 years of production experience building data pipelines, optimizing query "
    "performance, and delivering analytics that drive decisions. Not just coursework, but production "
    "systems at enterprise scale. I also have published research applying ML to real world "
    "datasets.\n\n"
    "I'm finishing my Master's in CS at Northeastern (3.66 GPA) and I'm ready to hit the ground "
    "running. That experience directly translates here and I'm confident I can contribute from "
    "day one.\n\n"
    "I would love the opportunity to discuss this further and demonstrate what I can bring. Resume attached.\n\n"
    "Best regards,\nPrasad Kanade"
)

HM_BODIES = {"SDE": _HM_BODY_SDE, "ML": _HM_BODY_ML, "DA": _HM_BODY_DA}
HM_BODY = _HM_BODY_SDE

REC_SUBJ = "Prasad Kanade \u2014 Application for {title} | {job_id}"

_REC_BODY_SDE = (
    "Hi {first},\n\n"
    "I applied for the {title} | {job_id} role at {company} and I'm genuinely excited about this "
    "one. I've been following {company}'s engineering work and the direction the team is taking is "
    "exactly what drew me to apply.\n\n"
    "I have around 2 years of hands on production engineering experience combined with a Master's "
    "in CS from Northeastern (3.66 GPA). My past work directly translates to this role and I'm "
    "confident I can contribute from day one.\n\n"
    "I would love the opportunity to discuss this further and demonstrate what I can bring. "
    "Resume attached.\n\n"
    "Best regards,\nPrasad Kanade"
)

_REC_BODY_ML = (
    "Hi {first},\n\n"
    "I applied for the {title} | {job_id} role at {company} and this is genuinely the opportunity "
    "I've been looking for. I've been following {company}'s engineering work and the direction the "
    "team is taking is exactly what drew me to apply.\n\n"
    "I bring published ML research (Springer) combined with around 2 years of production "
    "engineering. That combination of research and delivery is what I bring to the table. I'm "
    "finishing my Master's in CS at Northeastern (3.66 GPA) and that experience directly translates "
    "here. I'm confident I can contribute from day one.\n\n"
    "I would love the opportunity to discuss this further and demonstrate what I can bring. Resume attached.\n\n"
    "Best regards,\nPrasad Kanade"
)

_REC_BODY_DA = (
    "Hi {first},\n\n"
    "I applied for the {title} | {job_id} role at {company} and I'm genuinely excited about this "
    "opportunity. I've been following {company}'s engineering work and the direction the team is "
    "taking is exactly what drew me to apply.\n\n"
    "I have around 2 years of production data engineering experience combined with published ML "
    "research and a Master's in CS from Northeastern (3.66 GPA). That experience directly "
    "translates here and I'm confident I can contribute from day one.\n\n"
    "I would love the opportunity to discuss this further and demonstrate what I can bring. Resume attached.\n\n"
    "Best regards,\nPrasad Kanade"
)

REC_BODIES = {"SDE": _REC_BODY_SDE, "ML": _REC_BODY_ML, "DA": _REC_BODY_DA}
REC_BODY = _REC_BODY_SDE

PAT_A = ["{first}.{last}", "{f}{last}", "{first}{last}"]
PAT_B = [
    "{first}_{last}",
    "{first}",
    "{f}.{last}",
    "{first}{l}",
    "{last}.{first}",
    "{last}{f}",
    "{first}.{l}",
    "{last}.{f}",
    "{first}-{last}",
    "{last}",
]
PAT_C = [
    "{last}_{first}",
    "{last}-{first}",
    "{last}{first}",
    "{f}_{last}",
    "{f}-{last}",
]

CLEARBIT_URL = "https://autocomplete.clearbit.com/v1/companies/suggest"
TLDS = [".com", ".io", ".co", ".us", ".org", ".ai", ".dev"]

APIS = {
    "apollo": {"limit": 120, "url": "https://api.apollo.io/api/v1/people/match"},
    "hunter": {"limit": 25, "url": "https://api.hunter.io/v2/email-finder"},
    "snov": {"limit": 50, "url": "https://api.snov.io/v1/get-emails-from-names"},
    "prospeo": {"limit": 1500, "url": "https://api.prospeo.io/email-finder"},
}

REACHER_URL = "http://localhost:8080/v0/check_email"
REACHER_FROM = "test@example.org"
REACHER_TIMEOUT = 15
REACHER_WORKERS = 5
MAX_DAILY = 450
MAX_HOURLY = 50
DELAY_MIN = 30
DELAY_MAX = 90
API_TIMEOUT = 10
API_RETRIES = 3
SHEET_PAUSE = 1.5
HUNTER_CONF = 70

WARMUP_ON = True
WARMUP_START = "2026-02-15"
WARMUP = [(7, 10), (14, 25), (21, 50), (999, MAX_DAILY)]

CREDITS_FILE = os.path.join(_ROOT, ".local", "outreach_credits.json")
PATTERNS_FILE = os.path.join(_ROOT, ".local", "outreach_patterns.json")
LOG_FILE = os.path.join(_ROOT, ".local", "outreach.log")
DRAFT_HISTORY_FILE = os.path.join(_ROOT, ".local", "draft_history.json")
MS_TOKEN_FILE = os.path.join(_ROOT, ".local", "ms_token.json")
RESUME_SDE = os.path.join(_ROOT, ".local", "Prasad Kanade SWE Resume.pdf")
RESUME_ML = os.path.join(_ROOT, ".local", "Prasad Kanade ML Resume.pdf")
RESUME_DA = os.path.join(_ROOT, ".local", "Prasad Kanade Data Resume.pdf")

# ATS/internal domains that should never receive outreach emails
SUSPICIOUS_EMAIL_DOMAINS = [
    "oraclecloud.com",
    "onseminar.com",        # Clearbit error for onsemi.com
    "kariera-onsemi.cz",    # Czech ATS routing domain
    "myworkdayjobs.com",    # ATS platform not real email domain
    "greenhouse.io",        # ATS platform
    "lever.co",             # ATS platform
    "jobvite.com",          # ATS platform
    "taleo.net",            # ATS platform
    "icims.com",            # ATS platform
    "avfirewalls.co.il",    # Clearbit error for fortinet.com
    "i-voce.jp",            # Clearbit error for ivo.com
    "edel.fa.us2.oraclecloud.com",  # Oracle ATS subdomain
    "myworkdaysite.com",
    "myworkdayjobs.com",
    "successfactors.com",
    "icims.com",
    "ultipro.com",
    "taleo.net",
    "brassring.com",
    "jobvite.com",
    "greenhouse.io",
    "lever.co",
    "ashbyhq.com",
    "smartrecruiters.com",
    "paylocity.com",
    "paycom.com",
    "adp.com",
    "workday.com",
    "applytojob.com",
    "bamboohr.com",
    "clearcompany.com",
    "gserviceaccount.com",
    "eightfold.ai",
    "disneycareers.com",
    "careers.com",
    "jobs.com",
    "recruiting.com",
    "hire.com",
    "avature.net",
    "phenom.com",
    "beamery.com",
    "recruitingbypaycor.com",
    "hirebridge.com",
    "newton.co",
]

STRIP_PRE = {
    "dr.",
    "dr",
    "mr.",
    "mr",
    "mrs.",
    "mrs",
    "ms.",
    "ms",
    "prof.",
    "prof",
    "sir",
    "dame",
}
STRIP_SUF = {
    "jr.",
    "jr",
    "sr.",
    "sr",
    "ii",
    "iii",
    "iv",
    "phd",
    "ph.d.",
    "md",
    "m.d.",
    "esq",
    "esq.",
    "mba",
    "m.b.a.",
    "cpa",
    "c.p.a.",
}

STATE_TO_TIMEZONE = {
    "AL": "US/Central",
    "AK": "US/Alaska",
    "AZ": "US/Mountain",
    "AR": "US/Central",
    "CA": "US/Pacific",
    "CO": "US/Mountain",
    "CT": "US/Eastern",
    "DE": "US/Eastern",
    "FL": "US/Eastern",
    "GA": "US/Eastern",
    "HI": "US/Hawaii",
    "ID": "US/Mountain",
    "IL": "US/Central",
    "IN": "US/Eastern",
    "IA": "US/Central",
    "KS": "US/Central",
    "KY": "US/Eastern",
    "LA": "US/Central",
    "ME": "US/Eastern",
    "MD": "US/Eastern",
    "MA": "US/Eastern",
    "MI": "US/Eastern",
    "MN": "US/Central",
    "MS": "US/Central",
    "MO": "US/Central",
    "MT": "US/Mountain",
    "NE": "US/Central",
    "NV": "US/Pacific",
    "NH": "US/Eastern",
    "NJ": "US/Eastern",
    "NM": "US/Mountain",
    "NY": "US/Eastern",
    "NC": "US/Eastern",
    "ND": "US/Central",
    "OH": "US/Eastern",
    "OK": "US/Central",
    "OR": "US/Pacific",
    "PA": "US/Eastern",
    "RI": "US/Eastern",
    "SC": "US/Eastern",
    "SD": "US/Central",
    "TN": "US/Central",
    "TX": "US/Central",
    "UT": "US/Mountain",
    "VT": "US/Eastern",
    "VA": "US/Eastern",
    "WA": "US/Pacific",
    "WV": "US/Eastern",
    "WI": "US/Central",
    "WY": "US/Mountain",
    "DC": "US/Eastern",
}

TZ_DISPLAY = {
    "US/Eastern": "ET",
    "US/Central": "CT",
    "US/Mountain": "MT",
    "US/Pacific": "PT",
    "US/Alaska": "AKT",
    "US/Hawaii": "HT",
}

SEND_HOUR = 9


def _load_env():
    p = os.path.join(_ROOT, ".env")
    if os.path.exists(p):
        for ln in open(p):
            ln = ln.strip()
            if ln and not ln.startswith("#") and "=" in ln:
                k, v = ln.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


def key(name):
    _load_env()
    return os.environ.get(name, "")


def warmup_limit():
    if not WARMUP_ON:
        return MAX_DAILY
    try:
        d = (
            datetime.datetime.now()
            - datetime.datetime.strptime(WARMUP_START, "%Y-%m-%d")
        ).days
        if d < 0:
            return 5
        for th, lim in WARMUP:
            if d < th:
                return lim
    except Exception as _e:
        pass  # suppressed: use log.debug(_e) to investigate
    return MAX_DAILY

HM_LI_MSG_TEMPLATE = (
    "Hi {first}, I applied for the {title} at {company} "
    "and believe I'd be a strong fit for this role. Would love to connect "
    "and discuss how I can contribute to your team. Looking forward to hearing from you."
)
REC_LI_MSG_TEMPLATE = (
    "Hi {first}, I applied for the {title} at {company} "
    "and am eager to invest my skills into the right opportunity. "
    "Would love to connect and discuss how I can contribute and what the next steps look like."
)
LI_MSG_MAX = 300


def get_ranked_patterns():
    """
    Return PAT_A, PAT_B reordered by Brain's global success rates.
    Falls back to hardcoded order if Brain has < 10 data points.
    Cached per-process so Brain is only read once per run.
    """
    if hasattr(get_ranked_patterns, "_cache"):
        return get_ranked_patterns._cache
    try:
        from outreach.brain import Brain
        b = Brain.get()
        total = b._data.get("patterns", {}).get("total_attempts", 0)
        if total < 10:
            get_ranked_patterns._cache = (PAT_A, PAT_B, PAT_C)
            return get_ranked_patterns._cache
        all_pats = list(dict.fromkeys(PAT_A + PAT_B + PAT_C))
        ranked = b.rank_patterns_for("_global_", all_pats)
        # Split back into A/B/C tiers preserving coverage
        # A tier: top 3 patterns
        # B tier: next patterns
        # C tier: remainder
        new_a = ranked[:3] if len(ranked) >= 3 else PAT_A
        new_b = [p for p in ranked[3:] if p in PAT_B + PAT_C] or PAT_B
        new_c = [p for p in PAT_C if p not in new_b]
        get_ranked_patterns._cache = (new_a, new_b, new_c)
        return get_ranked_patterns._cache
    except Exception:
        return PAT_A, PAT_B, PAT_C
