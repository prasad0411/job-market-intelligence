"""
Direct ATS API sources — Greenhouse, Lever, Ashby, Hacker News.
Pulls intern/new-grad jobs directly from company career page APIs.
Zero errors, real URLs, correct company names.
"""

import json
import logging
import re
import time
import urllib.request
import ssl
from typing import List, Dict, Optional

log = logging.getLogger(__name__)

# SSL context for HTTPS requests
_CTX = ssl.create_default_context()

# ═══════════════════════════════════════════════════════════════════
# COMPANY LISTS — add/remove companies here
# ═══════════════════════════════════════════════════════════════════

GREENHOUSE_COMPANIES = {
    # Company slug → Display name
    "stripe": "Stripe", "coinbase": "Coinbase", "figma": "Figma",
    "notion": "Notion", "databricks": "Databricks", "ramp": "Ramp",
    "brex": "Brex", "verkada": "Verkada", "scaleai": "Scale AI",
    "anduril": "Anduril", "neuralink": "Neuralink",
    "billiontoone": "BillionToOne", "lucidmotors": "Lucid Motors",
    "anthropic": "Anthropic", "openai": "OpenAI",
    "discord": "Discord", "instacart": "Instacart",
    "doordash": "DoorDash", "reddit": "Reddit", "snap": "Snap",
    "lyft": "Lyft", "affirm": "Affirm", "chime": "Chime",
    "plaid": "Plaid", "robinhood": "Robinhood", "sofi": "SoFi",
    "toast": "Toast", "squarespace": "Squarespace",
    "hubspot": "HubSpot", "datadog": "Datadog",
    "elastic": "Elastic", "confluent": "Confluent",
    "twilio": "Twilio", "okta": "Okta",
    "crowdstrike": "CrowdStrike", "sentinelone": "SentinelOne",
    "snyk": "Snyk", "vanta": "Vanta", "cloudflare": "Cloudflare",
    "airtable": "Airtable", "asana": "Asana",
    "loom": "Loom", "calendly": "Calendly", "miro": "Miro",
    "canva": "Canva", "tempus": "Tempus",
    "color": "Color Health", "insitro": "Insitro",
    "recursion": "Recursion", "skydio": "Skydio",
    "samsara": "Samsara", "formlabs": "Formlabs",
    "draftkings": "DraftKings", "flywire": "Flywire",
    "earnin": "EarnIn", "stash": "Stash",
    "genbioinc": "GenBio AI", "fieldai": "Field AI",
    "fourkites": "FourKites", "ispottv": "iSpot.tv",
    "podium81": "Podium", "temporal": "Temporal",
    "cockroachlabs": "CockroachDB", "mongodb": "MongoDB",
    "hashicorp": "HashiCorp", "gitlabinc": "GitLab",
    "postman": "Postman", "twitch": "Twitch",
    "duolingo": "Duolingo", "quora": "Quora",
    "grammarly": "Grammarly", "wealthfront": "Wealthfront",
    "flexport": "Flexport", "gusto": "Gusto",
    "benchling": "Benchling", "sentry": "Sentry",
    "amplitude": "Amplitude", "launchdarkly": "LaunchDarkly",
    "pagerduty": "PagerDuty", "yelp": "Yelp",
    "thumbtack": "Thumbtack", "mapbox": "Mapbox",
    "segment": "Segment", "mixpanel": "Mixpanel",
    "fullstory": "FullStory", "braze": "Braze",
    "appdynamics": "AppDynamics", "newrelic": "New Relic",
    "sumologic": "Sumo Logic", "fastly": "Fastly",
    "digitalocean": "DigitalOcean", "linode": "Linode",
    "render": "Render", "fly": "Fly.io",
    "planetscale": "PlanetScale", "neon": "Neon",
    "turso": "Turso", "upstash": "Upstash",
    "copart": "Copart", "zipline": "Zipline",
    "applovin": "AppLovin", "unity": "Unity",
    "niantic": "Niantic", "roblox": "Roblox",
    "epic-games": "Epic Games", "riot-games": "Riot Games",
    "scopely": "Scopely", "kabam": "Kabam",
    "movableink": "Movable Ink", "contentful": "Contentful",
    "sanity": "Sanity", "prisma": "Prisma",
    "truveta": "Truveta",
    "veracyte": "Veracyte", "guardanthealth": "Guardant Health",
    "nuvei": "Nuvei", "billcom": "Bill.com",
    "tipalti": "Tipalti", "paylocity": "Paylocity",
    "paycor": "Paycor", "justworks": "Justworks",
    "greenhouse": "Greenhouse",
    "andurilindustries": "Anduril",
    "dashlane": "Dashlane",
    "pinterest": "Pinterest",
    "waymo": "Waymo",
    "vonage": "Vonage",
    "iterable": "Iterable",
    "blockchain": "Blockchain.com",
    "gemini": "Gemini",
    "alpaca": "Alpaca ",
    "sezzle": "Sezzle",
    "marqeta": "Marqeta",
    "jetbrains": "JetBrains",
    "coreweave": "CoreWeave",
    "sweetgreen": "Sweetgreen",
    "peloton": "Peloton",
    "zscaler": "Zscaler",
    "rocketlab": "Rocket Lab",
    "spacex": "SpaceX",
    "attentive": "Attentive",
    "klaviyo": "Klaviyo",
    "adyen": "Adyen",
    "rubrik": "Rubrik", "lever": "Lever",
    "ashby": "Ashby", "lattice": "Lattice",
    "culture-amp": "Culture Amp", "betterup": "BetterUp",
    "springhealth": "Spring Health", "headspace": "Headspace",
    "cerebral": "Cerebral", "ro": "Ro",
    "hims": "Hims & Hers", "nurx": "Nurx",
    "ziprecruiter": "ZipRecruiter", "indeed": "Indeed",
    "handshake": "Handshake", "wellfound": "Wellfound",
}

LEVER_COMPANIES = {
    "rippling": "Rippling", "anduril": "Anduril",
    "nuro": "Nuro", "cruise": "Cruise",
    "aurora-innovation": "Aurora", "zoox": "Zoox",
    "motional": "Motional", "cerebras": "Cerebras",
    "groq": "Groq", "sambanova": "SambaNova",
    "together-ai": "Together AI", "cohere": "Cohere",
    "langchain": "LangChain", "pinecone": "Pinecone",
    "replit": "Replit", "vercel": "Vercel",
    "supabase": "Supabase", "linear": "Linear",
    "retool": "Retool", "webflow": "Webflow",
    "mercury": "Mercury", "deel": "Deel",
    "lattice": "Lattice", "drata": "Drata",
    "clerk": "Clerk", "lendbuzz": "Lendbuzz",
    "field-ai": "Field AI", "plus-2": "PlusAI",
    "sunwatercapital": "Sunwater Capital",
    "applied-intuition": "Applied Intuition",
    "shield-ai": "Shield AI", "reliable-robotics": "Reliable Robotics",
    "skydio": "Skydio", "zipline": "Zipline",
    "loft-orbital": "Loft Orbital", "astranis": "Astranis",
    "vannevar-labs": "Vannevar Labs",
    "labelbox": "Labelbox", "scale": "Scale AI",
    "snorkel-ai": "Snorkel AI", "tecton": "Tecton",
    "determined-ai": "Determined AI", "wandb": "Weights & Biases",
    "helion-energy": "Helion Energy",
    "commonwealthfusion": "Commonwealth Fusion",
    "sila-nano": "Sila Nanotechnologies",
    "redwoodmaterials": "Redwood Materials",
    "quantumscape": "QuantumScape",
    "palantir": "Palantir",
}

ASHBY_COMPANIES = {
    "notion": "Notion", "ramp": "Ramp", "nash": "Nash",
    "stash": "Stash", "wealth-com": "Wealth.com",
    "pebl": "Pebl", "vanta": "Vanta", "linear": "Linear",
    "retool": "Retool", "clerk": "Clerk",
    "webflow": "Webflow", "mercury": "Mercury",
    "deel": "Deel", "lattice": "Lattice",
    "loom": "Loom", "calendly": "Calendly",
    "drata": "Drata", "snyk": "Snyk",
    "solink": "Solink", "rivianvw.tech": "XPENG",
    "anthropic": "Anthropic", "openai": "OpenAI",
    "cohere": "Cohere", "mistral": "Mistral AI",
    "perplexity": "Perplexity", "together": "Together AI",
    "modal": "Modal", "replicate": "Replicate",
    "runway": "Runway", "stability": "Stability AI",
    "midjourney": "Midjourney", "jasper": "Jasper",
    "adept": "Adept", "inflection": "Inflection AI",
    "character": "Character.AI",
}

# Title keywords that indicate intern/new-grad eligibility
_INTERN_KW = re.compile(
    r"\b(?:intern|co-op|coop|new\s*grad|entry\s*level|junior|early\s*career"
    r"|apprentice|fellow|rotational"
    r"|software\s*engineer|software\s*developer|sde|swe"
    r"|data\s*engineer|data\s*scientist|machine\s*learning\s*engineer"
    r"|associate\s*engineer|graduate\s*engineer|backend\s*engineer"
    r"|frontend\s*engineer|full\s*stack\s*engineer)\b",
    re.I,
)

# Seniority markers that disqualify a role regardless of the keyword match
_SENIOR_KW = re.compile(
    r"\b(?:senior|sr\.?|staff|principal|lead|manager|director|head\s+of"
    r"|architect|vp|distinguished|II|III|IV|2|3|4)\b",
    re.I,
)


from aggregator.job_age import pick_age as _pick_age


def _fetch_json(url: str, timeout: int = 5) -> Optional[dict]:
    """Fetch JSON from URL, return None on failure."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        resp = urllib.request.urlopen(req, timeout=timeout, context=_CTX)
        return json.loads(resp.read())
    except Exception:
        return None


def _is_intern_or_newgrad(title: str) -> bool:
    """Check if title is intern/new-grad OR entry-level full-time (not senior)."""
    if not _INTERN_KW.search(title):
        return False
    # Always allow explicit intern/new-grad even if oddly worded
    if re.search(r"\b(?:intern|co-op|coop|new\s*grad)\b", title, re.I):
        return True
    return not _SENIOR_KW.search(title)


# ═══════════════════════════════════════════════════════════════════
# GREENHOUSE
# ═══════════════════════════════════════════════════════════════════

def scrape_greenhouse() -> List[Dict]:
    """Fetch intern/new-grad jobs from all Greenhouse company boards."""
    jobs = []
    for slug, company_name in GREENHOUSE_COMPANIES.items():
        data = _fetch_json(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs")
        if not data:
            continue
        for job in data.get("jobs", []):
            title = job.get("title", "")
            if not _is_intern_or_newgrad(title):
                continue
            # Extract location
            location = "Unknown"
            loc_data = job.get("location", {})
            if isinstance(loc_data, dict):
                location = loc_data.get("name", "Unknown")
            elif isinstance(loc_data, str):
                location = loc_data
            url = job.get("absolute_url", "")
            job_id = str(job.get("id", "N/A"))
            jobs.append({
                "company": company_name,
                "title": title,
                "location": location,
                "url": url,
                "job_id": job_id,
                "source": "greenhouse_direct",
                "age": _pick_age(job, ("updated_at", "first_published", "created_at")),
                "is_closed": False,
            })
    log.info(f"Greenhouse direct: {len(jobs)} jobs from {len(GREENHOUSE_COMPANIES)} companies")
    return jobs


# ═══════════════════════════════════════════════════════════════════
# LEVER
# ═══════════════════════════════════════════════════════════════════

def scrape_lever() -> List[Dict]:
    """Fetch intern/new-grad jobs from all Lever company boards."""
    jobs = []
    for slug, company_name in LEVER_COMPANIES.items():
        data = _fetch_json(f"https://api.lever.co/v0/postings/{slug}?mode=json")
        if not data or not isinstance(data, list):
            continue
        for job in data:
            title = job.get("text", "")
            if not _is_intern_or_newgrad(title):
                continue
            location = "Unknown"
            categories = job.get("categories", {})
            if isinstance(categories, dict):
                location = categories.get("location", "Unknown")
            url = job.get("hostedUrl", "") or job.get("applyUrl", "")
            jobs.append({
                "company": company_name,
                "title": title,
                "location": location,
                "url": url,
                "job_id": "N/A",
                "source": "lever_direct",
                "age": _pick_age(job, ("createdAt", "created_at")),
                "is_closed": False,
            })
    log.info(f"Lever direct: {len(jobs)} jobs from {len(LEVER_COMPANIES)} companies")
    return jobs


# ═══════════════════════════════════════════════════════════════════
# ASHBY
# ═══════════════════════════════════════════════════════════════════

def scrape_ashby() -> List[Dict]:
    """Fetch intern/new-grad jobs from all Ashby company boards."""
    jobs = []
    for slug, company_name in ASHBY_COMPANIES.items():
        data = _fetch_json(f"https://api.ashbyhq.com/posting-api/job-board/{slug}")
        if not data:
            continue
        for job in data.get("jobs", []):
            title = job.get("title", "")
            if not _is_intern_or_newgrad(title):
                continue
            location = "Unknown"
            if job.get("location"):
                location = job["location"]
            elif job.get("locationName"):
                location = job["locationName"]
            url = job.get("jobUrl", "") or f"https://jobs.ashbyhq.com/{slug}/{job.get('id', '')}"
            jobs.append({
                "company": company_name,
                "title": title,
                "location": location,
                "url": url,
                "job_id": "N/A",
                "source": "ashby_direct",
                "age": _pick_age(job, ("publishedAt", "updatedAt", "publishedDate")),
                "is_closed": False,
            })
    log.info(f"Ashby direct: {len(jobs)} jobs from {len(ASHBY_COMPANIES)} companies")
    return jobs


# ═══════════════════════════════════════════════════════════════════
# HACKER NEWS "WHO IS HIRING"
# ═══════════════════════════════════════════════════════════════════

def scrape_hackernews_hiring(max_comments: int = 100) -> List[Dict]:
    """Fetch job postings from latest HN 'Who is Hiring' thread."""
    jobs = []
    # Find latest "Who is Hiring" thread
    user_data = _fetch_json("https://hacker-news.firebaseio.com/v0/user/whoishiring.json")
    if not user_data:
        return jobs
    
    thread_id = None
    for sub_id in user_data.get("submitted", [])[:10]:
        item = _fetch_json(f"https://hacker-news.firebaseio.com/v0/item/{sub_id}.json")
        if item and "hiring" in item.get("title", "").lower() and "who" in item.get("title", "").lower():
            thread_id = sub_id
            break
    
    if not thread_id:
        return jobs
    
    thread = _fetch_json(f"https://hacker-news.firebaseio.com/v0/item/{thread_id}.json")
    if not thread:
        return jobs
    
    comment_ids = thread.get("kids", [])[:max_comments]
    
    for cid in comment_ids:
        comment = _fetch_json(f"https://hacker-news.firebaseio.com/v0/item/{cid}.json")
        if not comment or comment.get("deleted"):
            continue
        text = comment.get("text", "")
        if not text:
            continue
        
        # Parse HN hiring format: "Company | Location | Role | ..."
        # First line usually has company info
        lines = text.replace("<p>", "\n").split("\n")
        first_line = re.sub(r"<[^>]+>", "", lines[0]).strip()
        parts = [p.strip() for p in first_line.split("|")]
        
        if len(parts) >= 2:
            company = parts[0]
            # Try to find intern/entry mentions
            full_text = " ".join(parts).lower()
            if any(kw in full_text for kw in ["intern", "new grad", "entry", "junior", "early career"]):
                title = parts[2] if len(parts) > 2 else parts[1]
                location = parts[1] if len(parts) > 1 else "Unknown"
                
                # Extract URL from text
                url_match = re.search(r'href="([^"]+)"', text)
                url = url_match.group(1) if url_match else "URL_CONFLICT"
                
                jobs.append({
                    "company": company[:50],
                    "title": title[:80],
                    "location": location[:50],
                    "url": url,
                    "job_id": "N/A",
                    "source": "hackernews_hiring",
                    "age": "0d",
                    "is_closed": False,
                })
    
    log.info(f"HackerNews hiring: {len(jobs)} intern/newgrad jobs from {len(comment_ids)} comments")
    return jobs


# ═══════════════════════════════════════════════════════════════════
# MAIN: Fetch all direct sources
# ═══════════════════════════════════════════════════════════════════

def _is_us_location(location: str) -> bool:
    """Strict US-only check for direct ATS sources."""
    if not location or location == "Unknown":
        return True  # Let pipeline decide
    loc = location.lower().strip()
    
    # Reject obvious international
    intl = ["canada", "uk", "united kingdom", "germany", "france", "india",
            "singapore", "australia", "brazil", "mexico", "japan", "china",
            "korea", "taiwan", "israel", "ireland", "netherlands", "sweden",
            "norway", "denmark", "finland", "switzerland", "austria", "poland",
            "czech", "romania", "hungary", "portugal", "spain", "italy",
            "toronto", "vancouver", "london", "berlin", "tokyo", "sydney",
            "melbourne", "dublin", "amsterdam", "paris", "mumbai", "pune",
            "bangalore", "hyderabad", "chennai", "delhi", "kolkata",
            "são paulo", "sao paulo", "mexico city", "shanghai", "beijing",
            "shenzhen", "hangzhou", "guangzhou", "seoul", "taipei",
            "tel aviv", "haifa", "stockholm", "oslo", "copenhagen",
            "helsinki", "zurich", "vienna", "warsaw", "gdansk", "prague",
            "bucharest", "budapest", "lisbon", "madrid", "barcelona",
            "milan", "rome", "munich", "hamburg", "frankfurt",
            "british columbia", "ontario", "quebec", "alberta",
            "prc", "apac", "emea", "latam", "bgr", "sofia", "bogota", "buenos aires", "santiago"]
    if any(kw in loc for kw in intl):
        return False
    
    # Accept US indicators
    us_states = [", al", ", ak", ", az", ", ar", ", ca", ", co", ", ct",
        ", de", ", fl", ", ga", ", hi", ", id", ", il", ", in", ", ia",
        ", ks", ", ky", ", la", ", me", ", md", ", ma", ", mi", ", mn",
        ", ms", ", mo", ", mt", ", ne", ", nv", ", nh", ", nj", ", nm",
        ", ny", ", nc", ", nd", ", oh", ", ok", ", or", ", pa", ", ri",
        ", sc", ", sd", ", tn", ", tx", ", ut", ", vt", ", va", ", wa",
        ", wv", ", wi", ", wy", ", dc"]
    us_keywords = ["remote", "usa", "united states", "us-", "usa-"]
    
    if any(kw in loc for kw in us_states):
        return True
    if any(kw in loc for kw in us_keywords):
        return True
    
    # Check for US state codes in format "US-CA-Santa Clara"
    if re.match(r"us-[a-z]{2}", loc):
        return True
    
    # Ambiguous (like "3 Locations", "Multiple") — reject for direct sources
    # Better to miss a job than include international ones
    # Exception: if it contains a known US city
    us_cities = ["new york", "san francisco", "palo alto", "mountain view",
        "sunnyvale", "san jose", "santa clara", "los angeles", "seattle",
        "austin", "boston", "chicago", "denver", "atlanta", "dallas",
        "houston", "phoenix", "portland", "san diego", "pittsburgh",
        "raleigh", "charlotte", "nashville", "minneapolis", "detroit",
        "philadelphia", "baltimore", "columbus", "indianapolis",
        "salt lake", "irvine", "bellevue", "redmond", "fremont",
        "milpitas", "cupertino", "menlo park", "foster city",
        "south san francisco", "burlingame", "redwood city"]
    if any(city in loc for city in us_cities):
        return True
    
    # No US signal found — skip this job (conservative for direct sources)
    return False




# ═══════════════════════════════════════════════════════════════════
# WORKDAY SEARCH API
# ═══════════════════════════════════════════════════════════════════

WORKDAY_COMPANIES = {
    # (display_name, domain, tenant, site)
    "Intel": ("intel.wd1.myworkdayjobs.com", "intel", "external"),
    "NVIDIA": ("nvidia.wd5.myworkdayjobs.com", "nvidia", "NVIDIAExternalCareerSite"),
    "Cisco": ("cisco.wd5.myworkdayjobs.com", "cisco", "cisco_careers"),
    "Salesforce": ("salesforce.wd12.myworkdayjobs.com", "salesforce", "Slack"),
    "PayPal": ("paypal.wd1.myworkdayjobs.com", "paypal", "jobs"),
    "Micron": ("micron.wd1.myworkdayjobs.com", "micron", "External"),
    "Medtronic": ("medtronic.wd1.myworkdayjobs.com", "medtronic", "redeploymentmedtroniccareers"),
    "State Street": ("statestreet.wd1.myworkdayjobs.com", "statestreet", "global"),
    "TD Bank": ("td.wd3.myworkdayjobs.com", "td", "TD_Bank_Careers"),
    "Iron Mountain": ("ironmountain.wd5.myworkdayjobs.com", "ironmountain", "iron-mountain-jobs"),
    "Boeing": ("boeing.wd1.myworkdayjobs.com", "boeing", "external_careers"),
}

def scrape_workday() -> List[Dict]:
    """Fetch intern/new-grad jobs from Workday company search APIs."""
    jobs = []
    for company_name, (domain, tenant, site) in WORKDAY_COMPANIES.items():
        for query in ["software engineer intern", "data science intern", "machine learning intern"]:
            url = f"https://{domain}/wday/cxs/{tenant}/{site}/jobs"
            payload = json.dumps({
                "appliedFacets": {},
                "limit": 20,
                "offset": 0,
                "searchText": query,
            }).encode()
            try:
                req = urllib.request.Request(url,
                    data=payload,
                    headers={"User-Agent": "Mozilla/5.0", "Content-Type": "application/json"},
                    method="POST")
                resp = urllib.request.urlopen(req, timeout=10, context=_CTX)
                data = json.loads(resp.read())
            except Exception:
                continue

            for posting in data.get("jobPostings", []):
                title = posting.get("title", "")
                if not _is_intern_or_newgrad(title):
                    continue

                # Build URL
                external_path = posting.get("externalPath", "")
                job_url = f"https://{domain}{external_path}" if external_path else ""

                # Location
                loc_parts = []
                if posting.get("locationsText"):
                    loc_parts.append(posting["locationsText"])
                location = ", ".join(loc_parts) if loc_parts else "Unknown"

                # Job ID from bulletFields
                job_id = "N/A"
                for field in posting.get("bulletFields", []):
                    if field and re.match(r"^[A-Z0-9_-]{4,20}$", str(field)):
                        job_id = str(field)
                        break

                jobs.append({
                    "company": company_name,
                    "title": title,
                    "location": location,
                    "url": job_url,
                    "job_id": job_id,
                    "source": "workday_direct",
                    # was _pick_age(j, ...) - `j` is undefined in this loop,
                    # whose variable is `posting`. Fourth instance of this
                    # loop-variable mismatch across the ATS scrapers.
                    "age": _pick_age(posting, ("startDate",), fallback_key="postedOn"),
                    "is_closed": False,
                })

    # Dedup by company+title
    seen = set()
    unique = []
    for j in jobs:
        key = re.sub(r"[^a-z0-9]", "", f"{j['company']}_{j['title']}".lower())
        if key not in seen:
            seen.add(key)
            unique.append(j)

    log.info(f"Workday direct: {len(unique)} jobs from {len(WORKDAY_COMPANIES)} companies")
    return unique


# ═══════════════════════════════════════════════════════════════════
# SMARTRECRUITERS API
# ═══════════════════════════════════════════════════════════════════

SMARTRECRUITERS_COMPANIES = {
    "BoschGroup": "Bosch",
    "EVERSANA1": "EVERSANA",
    "Sandisk": "SanDisk",
    "Visa": "Visa",
    "SquareTrade1": "SquareTrade",
    "Talan": "Talan",
    "Eurofins": "Eurofins",
    "ServiceNow": "ServiceNow",
}

def scrape_smartrecruiters() -> List[Dict]:
    """Fetch intern/new-grad jobs from SmartRecruiters company APIs."""
    jobs = []
    for company_id, company_name in SMARTRECRUITERS_COMPANIES.items():
        for query in ["intern", "co-op", "new grad", "entry level", "junior"]:
            data = _fetch_json(
                f"https://api.smartrecruiters.com/v1/companies/{company_id}/postings?limit=50&q={query}",
                timeout=8
            )
            if not data or not data.get("content"):
                continue

            for posting in data["content"]:
                title = posting.get("name", "")
                if not _is_intern_or_newgrad(title):
                    continue

                # Location
                loc = posting.get("location", {})
                city = loc.get("city", "")
                region = loc.get("region", "")
                country = loc.get("country", "")
                if country and country.upper() != "US":
                    continue  # US only
                location = f"{city}, {region}" if city and region else city or region or "Unknown"

                # URL
                job_url = posting.get("ref", "")
                if not job_url:
                    pid = posting.get("id", "")
                    job_url = f"https://jobs.smartrecruiters.com/{company_id}/{pid}" if pid else ""

                job_id = posting.get("id", "N/A")

                jobs.append({
                    "company": company_name,
                    "title": title,
                    "location": location,
                    "url": job_url,
                    "job_id": str(job_id),
                    "source": "smartrecruiters_direct",
                    "age": _pick_age(posting, ("releasedDate", "createdOn")),
                    "is_closed": False,
                })

    # Dedup
    seen = set()
    unique = []
    for j in jobs:
        key = re.sub(r"[^a-z0-9]", "", f"{j['company']}_{j['title']}".lower())
        if key not in seen:
            seen.add(key)
            unique.append(j)

    log.info(f"SmartRecruiters direct: {len(unique)} jobs from {len(SMARTRECRUITERS_COMPANIES)} companies")
    return unique


WORKABLE_COMPANIES = {}


def scrape_workable() -> List[Dict]:
    """Fetch intern/new-grad jobs from Workable company boards."""
    jobs = []
    for slug, company_name in WORKABLE_COMPANIES.items():
        data = _fetch_json(
            f"https://apply.workable.com/api/v1/widget/accounts/{slug}?details=true",
            timeout=8,
        )
        if not data:
            continue
        for job in data.get("jobs", []):
            title = job.get("title", "")
            if not _is_intern_or_newgrad(title):
                continue
            location = job.get("location") or "Unknown"
            if isinstance(location, dict):
                city = location.get("city", "")
                region = location.get("region", "")
                location = ", ".join([x for x in (city, region) if x]) or "Unknown"
            url = job.get("url") or job.get("application_url") or ""
            if not url and job.get("shortcode"):
                url = f"https://apply.workable.com/{slug}/j/{job['shortcode']}/"
            jobs.append({
                "company": company_name,
                "title": title,
                "location": location,
                "url": url,
                "job_id": str(job.get("shortcode", "N/A")),
                "source": "workable_direct",
                "age": _pick_age(job, ("published_on", "created_at")),
                "is_closed": False,
            })
    log.info(f"Workable direct: {len(jobs)} jobs from {len(WORKABLE_COMPANIES)} companies")
    return jobs


RIPPLING_COMPANIES = {}


def scrape_rippling() -> List[Dict]:
    """Fetch jobs from Rippling ATS company boards."""
    jobs = []
    for slug, company_name in RIPPLING_COMPANIES.items():
        data = _fetch_json(
            f"https://api.rippling.com/platform/api/ats/v1/board/{slug}/jobs",
            timeout=8,
        )
        if not isinstance(data, list):
            continue
        for job in data:
            title = job.get("name", "")
            if not _is_intern_or_newgrad(title):
                continue
            loc = job.get("workLocation") or {}
            location = loc.get("label") or "Unknown" if isinstance(loc, dict) else "Unknown"
            jobs.append({
                "company": company_name,
                "title": title,
                "location": location,
                "url": job.get("url", ""),
                "job_id": str(job.get("uuid", "N/A")),
                "source": "rippling_direct",
                "age": _pick_age(job, ("createdAt", "updatedAt")),
                "is_closed": False,
            })
    log.info(f"Rippling direct: {len(jobs)} jobs from {len(RIPPLING_COMPANIES)} companies")
    return jobs


WORKDAY_TENANTS = {}


def _workday_fetch(tenant, wd, site, query, limit=20):
    """POST to a Workday tenant's public job search API."""
    import requests as _rq
    url = f"https://{tenant}.{wd}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs"
    body = {"appliedFacets": {}, "limit": limit, "offset": 0, "searchText": query}
    try:
        r = _rq.post(url, json=body, timeout=12, headers={
            "User-Agent": "Mozilla/5.0",
            "Content-Type": "application/json",
            "Accept": "application/json",
        })
        if r.status_code != 200:
            return None
        return r.json().get("jobPostings", [])
    except Exception as e:
        log.debug(f"workday {tenant} failed: {e}")
        return None


def scrape_workday_tenants() -> List[Dict]:
    """Fetch entry-level jobs from discovered Workday tenants."""
    jobs = []
    queries = ["software engineer", "intern", "new grad", "data engineer"]
    for key, company_name in WORKDAY_TENANTS.items():
        try:
            tenant, wd, site = key.split("|")
        except ValueError:
            continue
        seen_paths = set()
        for q in queries:
            posts = _workday_fetch(tenant, wd, site, q)
            if not posts:
                continue
            for jp in posts:
                title = jp.get("title", "")
                path = jp.get("externalPath", "")
                if not title or not path or path in seen_paths:
                    continue
                if not _is_intern_or_newgrad(title):
                    continue
                seen_paths.add(path)
                loc = jp.get("locationsText", "Unknown")
                bullets = jp.get("bulletFields") or []
                jobs.append({
                    "company": company_name,
                    "title": title,
                    "location": loc,
                    "url": f"https://{tenant}.{wd}.myworkdayjobs.com/{site}{path}",
                    "job_id": str(bullets[0]) if bullets else "N/A",
                    "source": "workday_tenant",
                    "age": _pick_age(jp, ("startDate",), fallback_key="postedOn"),
                    "is_closed": False,
                })
    log.info(f"Workday tenants: {len(jobs)} jobs from {len(WORKDAY_TENANTS)} tenants")
    return jobs


def _load_discovered_companies():
    """Load auto-discovered companies from brain.json."""
    import os
    try:
        if os.path.exists(".local/brain.json"):
            with open(".local/brain.json") as f:
                brain = json.load(f)
            discovered = brain.get("discovered_ats", {})
            # Merge into main dicts
            for slug, name in discovered.get("greenhouse", {}).items():
                if slug not in GREENHOUSE_COMPANIES:
                    GREENHOUSE_COMPANIES[slug] = name
            for slug, name in discovered.get("lever", {}).items():
                if slug not in LEVER_COMPANIES:
                    LEVER_COMPANIES[slug] = name
            for slug, name in discovered.get("ashby", {}).items():
                if slug not in ASHBY_COMPANIES:
                    ASHBY_COMPANIES[slug] = name
            for slug, name in discovered.get("smartrecruiters", {}).items():
                if slug not in SMARTRECRUITERS_COMPANIES:
                    SMARTRECRUITERS_COMPANIES[slug] = name
            for slug, name in discovered.get("workable", {}).items():
                if slug not in WORKABLE_COMPANIES:
                    WORKABLE_COMPANIES[slug] = name
            for slug, name in discovered.get("rippling", {}).items():
                if slug not in RIPPLING_COMPANIES:
                    RIPPLING_COMPANIES[slug] = name
            for key, name in discovered.get("workday_tenants", {}).items():
                if key not in WORKDAY_TENANTS:
                    WORKDAY_TENANTS[key] = name
    except Exception:
        pass


def fetch_all_direct_sources() -> List[Dict]:
    """Fetch from all direct ATS APIs. Returns list of job dicts."""
    _load_discovered_companies()  # Auto-expand company list from brain
    all_jobs = []
    
    log.info("Fetching direct sources: Greenhouse, Lever, Ashby, HackerNews")
    
    # Indeed via JobSpy. Narrow scope on purpose: LinkedIn/Glassdoor need
    # proxies and block hard, but Indeed has no rate limiting and covers
    # employers who never post to Greenhouse/Lever or the GitHub lists.
    # Wrapped so a missing package or a bad response cannot break a run.
    try:
        from aggregator.jobspy_source import scrape_indeed
        all_jobs.extend(scrape_indeed())
    except Exception as e:
        log.error(f"Indeed (JobSpy) scrape failed: {e}")

    try:
        all_jobs.extend(scrape_greenhouse())
    except Exception as e:
        log.error(f"Greenhouse scrape failed: {e}")
    
    try:
        all_jobs.extend(scrape_lever())
    except Exception as e:
        log.error(f"Lever scrape failed: {e}")
    
    try:
        all_jobs.extend(scrape_ashby())
    except Exception as e:
        log.error(f"Ashby scrape failed: {e}")
    
    try:
        all_jobs.extend(scrape_hackernews_hiring())
    except Exception as e:
        log.error(f"HackerNews scrape failed: {e}")

    try:
        all_jobs.extend(scrape_workday())
    except Exception as e:
        log.error(f"Workday scrape failed: {e}")

    try:
        all_jobs.extend(scrape_smartrecruiters())
    except Exception as e:
        log.error(f"SmartRecruiters scrape failed: {e}")

    try:
        all_jobs.extend(scrape_workable())
    except Exception as e:
        log.error(f"Workable scrape failed: {e}")

    try:
        all_jobs.extend(scrape_rippling())
    except Exception as e:
        log.error(f"Rippling scrape failed: {e}")

    try:
        all_jobs.extend(scrape_workday_tenants())
    except Exception as e:
        log.error(f"Workday tenants scrape failed: {e}")
    
    # Filter US-only
    us_jobs = [j for j in all_jobs if _is_us_location(j.get("location", "Unknown"))]
    log.info(f"Total direct source jobs: {len(us_jobs)} (filtered from {len(all_jobs)})")
    return us_jobs


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    jobs = fetch_all_direct_sources()
    print(f"\nTotal: {len(jobs)} jobs")
    print(f"\nBy source:")
    from collections import Counter
    for source, count in Counter(j["source"] for j in jobs).items():
        print(f"  {source}: {count}")
    print(f"\nSample jobs:")
    for j in jobs[:10]:
        print(f"  {j['company']:20s} | {j['title'][:40]:40s} | {j['location'][:20]:20s} | {j['source']}")
