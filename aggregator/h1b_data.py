"""
H-1B sponsorship lookup from USCIS Employer Data Hub.

SOURCE
  https://www.uscis.gov/sites/default/files/document/data/h1b_datahubexport-YYYY.csv
  Official USCIS data: first decisions on H-1B petitions, aggregated per
  employer per fiscal year. Columns:
    Fiscal Year, Employer, Initial Approval, Initial Denial,
    Continuing Approval, Continuing Denial, NAICS, Tax ID, State, City, ZIP

WHY THIS AND NOT DOL
  DOL LCA data is per-application (500k+ rows, 50-150MB files) and only shows
  who FILED. USCIS is pre-aggregated and shows who was APPROVED - a stronger
  signal and a far smaller download. DOL also 403s direct requests.

WHY OLDER FISCAL YEARS ARE FINE
  FY2024+ sits behind a Tableau export flow. It does not matter much:
  sponsorship is a stable company property. A company with 3,000 approvals in
  FY2023 sponsors. Unlike a job posting, that does not go stale in a quarter.
  We combine the last 3 available years so a company that skipped one still
  counts.

MATCHING
  Company names differ between sources ("GOOGLE LLC" vs "Google"). We
  normalise aggressively and require a confident match. Anything uncertain
  stays Unknown rather than guessing - a wrong "Yes" is worse than no answer.
"""
import csv
import io
import json
import logging
import os
import re
import urllib.request

log = logging.getLogger(__name__)

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE_FILE = os.path.join(BASE, ".local", "h1b_sponsors.json")
_URL = "https://www.uscis.gov/sites/default/files/document/data/h1b_datahubexport-{}.csv"
_YEARS = (2026, 2025, 2024, 2023, 2022, 2021)  # newest first; missing years skip silently
_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36")

# Suffixes that differ between USCIS and job boards but mean the same company
_SUFFIXES = re.compile(
    r"\b(inc|incorporated|llc|l\.l\.c|ltd|limited|corp|corporation|co|company|"
    r"plc|lp|llp|pllc|pc|group|holdings|holding|technologies|technology|"
    r"systems|solutions|services|labs|laboratories|usa|us|america|"
    r"international|worldwide|global|na|north|the)\b", re.I)


def normalize(name):
    """Aggressive normalisation so 'GOOGLE LLC' and 'Google, Inc.' collide."""
    if not name:
        return ""
    n = str(name).lower()
    n = re.sub(r"[^a-z0-9\s&]", " ", n)   # punctuation out
    n = _SUFFIXES.sub(" ", n)             # legal suffixes out
    n = re.sub(r"\s+", "", n)             # all whitespace out
    return n


LOCAL_DIR = os.path.join(BASE, ".local", "h1b")

# The Tableau "Download to Excel -> CSV" export differs from the direct
# fiscal-year CSVs: UTF-16, TAB separated, and approvals split across six
# columns instead of two. It is the only way to get FY2024+ since those
# direct files are no longer published.
_APPROVAL_COLS = ("New Employment Approval", "Continuation Approval",
                  "Change with Same Employer Approval", "New Concurrent Approval",
                  "Change of Employer Approval", "Amended Approval",
                  "Initial Approval", "Continuing Approval")


def _load_local_exports():
    """Read any Tableau exports dropped in .local/h1b/. Returns {} if none."""
    if not os.path.isdir(LOCAL_DIR):
        return {}, []
    files = [f for f in os.listdir(LOCAL_DIR) if f.lower().endswith((".csv", ".tsv"))]
    if not files:
        return {}, []

    sponsors, years = {}, set()
    for fname in files:
        path = os.path.join(LOCAL_DIR, fname)
        text = None
        for enc in ("utf-16", "utf-16-le", "utf-8-sig", "latin-1"):
            try:
                with open(path, encoding=enc) as fh:
                    text = fh.read()
                if "\t" in text[:4000] or "," in text[:4000]:
                    break
            except Exception:
                continue
        if not text:
            log.warning("h1b local: could not decode %s", fname)
            continue

        delim = "\t" if text.count("\t") > text.count(",") else ","
        try:
            rows = list(csv.DictReader(io.StringIO(text), delimiter=delim))
        except Exception as e:
            log.warning("h1b local: parse failed for %s: %s", fname, str(e)[:60])
            continue

        for r in rows:
            emp = ""
            for k in r:
                if k and "Employer" in k:
                    emp = (r.get(k) or "").strip()
                    break
            if not emp or emp.lower() == "null":
                continue
            key = normalize(emp)
            if len(key) < 3:
                continue

            total = 0
            for col in _APPROVAL_COLS:
                v = (r.get(col) or "0").replace(",", "").strip()
                try:
                    total += int(float(v))
                except Exception:
                    pass
            if total <= 0:
                continue

            # The Tableau export prefixes a "Line by line" column, so the
            # header key may carry a BOM or leading text. Match by suffix.
            fy = ""
            for k in r:
                if k and k.strip().endswith("Fiscal Year"):
                    fy = (r.get(k) or "").strip()
                    break
            if fy.isdigit():
                years.add(int(fy))

            prev = sponsors.get(key)
            if prev is None:
                sponsors[key] = [total, emp]
            else:
                prev[0] += total
                if len(emp) > len(prev[1]):
                    prev[1] = emp

        log.info("h1b local: %s -> %d rows, %d employers so far",
                 fname, len(rows), len(sponsors))

    return sponsors, sorted(years, reverse=True)


def _cache_read():
    try:
        with open(CACHE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _cache_write(data):
    try:
        from aggregator.atomic_json import write_json
        write_json(CACHE_FILE, data, indent=0)
    except Exception as e:
        log.warning("h1b cache write failed: %s", e)


def build_cache(force=False):
    """Download USCIS data and build {normalized_name: approvals}. Cached."""
    if not force:
        cached = _cache_read()
        if cached and cached.get("sponsors"):
            return cached

    # Local Tableau exports first - they are the only route to FY2024+.
    sponsors, years_used = _load_local_exports()
    if sponsors:
        log.info("h1b: using LOCAL export - %d employers, FY%s",
                 len(sponsors), ", FY".join(str(y) for y in years_used))
        data = {"sponsors": sponsors, "years": years_used, "source": "local"}
        _cache_write(data)
        return data

    for year in _YEARS:
        url = _URL.format(year)
        try:
            req = urllib.request.Request(url, headers={"User-Agent": _UA})
            raw = urllib.request.urlopen(req, timeout=120).read()
        except Exception as e:
            log.warning("H-1B FY%s download failed: %s", year, str(e)[:70])
            continue

        try:
            text = raw.decode("utf-8-sig", "replace")
            rows = list(csv.DictReader(io.StringIO(text)))
        except Exception as e:
            log.warning("H-1B FY%s parse failed: %s", year, str(e)[:70])
            continue

        added = 0
        for r in rows:
            emp = (r.get("Employer") or "").strip()
            if not emp:
                continue
            key = normalize(emp)
            if len(key) < 3:
                continue

            def num(field):
                v = (r.get(field) or "0").replace(",", "").strip()
                try:
                    return int(float(v))
                except Exception:
                    return 0

            approvals = num("Initial Approval") + num("Continuing Approval")
            if approvals <= 0:
                continue
            # SUM, do not max. One company files under many entities and many
            # worksites: "AMAZON.COM SERVICES LLC", "AMAZON SERVICES LLC",
            # "AMAZON WEB SERVICES INC" are all Amazon. Taking the max of one
            # row returned 1 approval for Amazon.
            prev = sponsors.get(key)
            if prev is None:
                sponsors[key] = [approvals, emp]
                added += 1
            else:
                prev[0] += approvals
                # keep the longest display name - usually the fullest legal one
                if len(emp) > len(prev[1]):
                    prev[1] = emp
        years_used.append(year)
        log.info("H-1B FY%s: %d employer rows, %d sponsors so far",
                 year, len(rows), len(sponsors))

    if not sponsors:
        log.warning("H-1B: no data retrieved, sponsorship lookup disabled")
        return {"sponsors": {}, "years": []}

    data = {"sponsors": sponsors, "years": years_used}
    _cache_write(data)
    log.info("H-1B cache built: %d employers from FY%s",
             len(sponsors), ", FY".join(str(y) for y in years_used))
    return data


_MEM = {"data": None}


def lookup(company):
    """Return (verdict, approvals, matched_name).

    verdict is "Yes" when the company is a confirmed sponsor, otherwise
    "Unknown". We never return "No" - absence from the file could mean a small
    or new company, not a refusal to sponsor.
    """
    if not company:
        return "Unknown", 0, ""
    if _MEM["data"] is None:
        _MEM["data"] = build_cache()
    sponsors = _MEM["data"].get("sponsors") or {}
    if not sponsors:
        return "Unknown", 0, ""

    key = normalize(company)
    if len(key) < 3:
        return "Unknown", 0, ""

    # Collect the exact hit AND every prefix variant, then take the largest.
    # Stopping at the exact match returned "AMAZON SOLUTIONS, INC." (2
    # approvals) for Amazon, because the real entities normalise to
    # "amazoncom" / "amazonweb" and never to a bare "amazon".
    candidates = []
    hit = sponsors.get(key)
    if hit:
        candidates.append(hit)

    # WORD-BOUNDARY matching. Character prefixes are too crude in both
    # directions: "advanced" matched "ADVANCED TECHNOLOGY LABORATORIES"
    # (wrong company, false Yes), while "meta" missed "META PLATFORMS INC"
    # because the length gap was too large.
    #
    # Compare the FIRST WORD of the query against the first word of the
    # employer name instead. "Meta" vs "META PLATFORMS" matches on "meta";
    # "Advanced Space" vs "ADVANCED TECHNOLOGY" does not, because the query
    # has a second word that must also appear.
    # Run word-boundary matching even when an exact key hit exists: the
    # exact key 'amazon' matches a tiny unrelated 'AMAZON SOLUTIONS INC',
    # while the real entities are 'AMAZON.COM SERVICES' / 'AMAZON WEB
    # SERVICES'. Gather all candidates, then take the largest.
    if True:
        q_words = [w for w in re.split(r"[^a-z0-9]+", str(company).lower()) if w]
        q_words = [w for w in q_words if not _SUFFIXES.fullmatch(w)]
        if q_words and len(q_words[0]) >= 4:
            first = q_words[0]
            need_second = q_words[1] if len(q_words) > 1 else None
            best = None
            for k, v in sponsors.items():
                disp = str(v[1]).lower()
                d_words = [w for w in re.split(r"[^a-z0-9]+", disp) if w]
                d_words = [w for w in d_words if not _SUFFIXES.fullmatch(w)]
                if not d_words or d_words[0] != first:
                    continue
                # A single-word query ("Meta", "Stripe") matches on the first
                # word alone. A multi-word query must corroborate.
                if need_second and need_second not in d_words:
                    continue
                if best is None or v[0] > best[0]:
                    best = v
            if best:
                candidates.append(best)

    if candidates:
        win = max(candidates, key=lambda c: c[0])
        return "Yes", win[0], win[1]

    return "Unknown", 0, ""


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    d = build_cache(force=True)
    print("\nemployers cached:", len(d.get("sponsors", {})))
    print("fiscal years:", d.get("years"))
    print()
    for c in ["Google", "Amazon", "Stripe", "Notion", "Anthropic", "Zoox",
              "Databricks", "Nightwing", "Some Tiny Startup XYZ"]:
        v, n, m = lookup(c)
        print("  {:<24} {:<8} {:>7} approvals   {}".format(c, v, n, m[:38]))
