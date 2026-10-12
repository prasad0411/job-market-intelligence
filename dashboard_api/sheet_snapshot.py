"""
Builds the public dashboard snapshot from the live Google Sheet ("H1B visa").

Only two worksheets are read, read only: "Valid Entries" (postings in the tracker now) and
"Discarded Entries" (postings the pipeline rejected, with the reason). Every other worksheet
(applications, outreach contacts, notes) is never opened.

From "Valid Entries" only public posting fields are kept. Personal columns (Status, Date Applied,
Notes) are dropped before anything is built, so they cannot reach the public site.

Lifetime pipeline numbers (postings evaluated, where every posting went, run times) still come from
run_history.db, because rejected postings are only counted there, never written to the Sheet.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timezone

from dashboard_api import queries

VALID_SHEET = "Valid Entries"
DISCARD_SHEET = "Discarded Entries"
PUBLIC_FIELDS = ("company", "title", "location", "source", "url", "job_type", "remote", "sponsored", "track", "entry_date")
JOB_TYPES = {"Internship", "Full Time", "Co-op"}
TRACKS = {"SDE": "Software engineering", "ML": "Machine learning", "DA": "Data and analytics", "Tailored": "Tailored"}
_FORMATS = ("%d %B, %I:%M %p", "%d %B %I:%M %p", "%d %b, %I:%M %p", "%d %B", "%d %b", "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d", "%b %d, %Y", "%d-%b-%Y", "%m/%d/%Y", "%d %B %Y")


def parse_entry_date(text: str, today: date | None = None) -> date | None:
    """Sheet dates often omit the year ("05 October, 09:37 AM"). Assume the most recent such date."""
    s = re.sub(r"\s+", " ", str(text or "").strip())
    if not s or s.upper() == "N/A":
        return None
    today = today or date.today()
    for fmt in _FORMATS:
        has_year = "%Y" in fmt
        try:
            # Python 3.13 deprecates parsing a day of month without a year, so parse yearless
            # formats against a placeholder leap year (2000) and set the real year afterwards.
            dt = datetime.strptime(s if has_year else f"{s} 2000", fmt if has_year else f"{fmt} %Y")
        except ValueError:
            continue
        if not has_year:
            for year in (today.year, today.year - 1):
                try:
                    d = dt.replace(year=year).date()
                except ValueError:  # 29 February outside a leap year
                    continue
                if d <= today:
                    return d
            return None
        return dt.date()
    return None


def records(values: list[list[str]]) -> list[dict]:
    """Rows of a worksheet keyed by header, first non empty header wins on duplicates."""
    if not values:
        return []
    header = [h.strip() for h in values[0]]
    out = []
    for row in values[1:]:
        rec = {}
        for i, h in enumerate(header):
            if h and h not in rec:
                rec[h] = row[i].strip() if i < len(row) else ""
        if any(rec.values()):
            out.append(rec)
    return out


def public_posting(rec: dict, idx: int, today: date | None = None) -> dict | None:
    company, title, url = rec.get("Company", ""), rec.get("Title", ""), rec.get("Job URL", "")
    if not company or not title:
        return None
    d = parse_entry_date(rec.get("Entry Date", ""), today)
    job_type = rec.get("Job Type", "")
    return {
        "id": idx,
        "company": company,
        "title": title,
        "location": rec.get("Location") or "Unknown",
        "source": rec.get("Source") or "Unknown",
        "url": url if url.startswith("http") else "",
        "job_type": job_type if job_type in JOB_TYPES else "Other",
        "remote": rec.get("Remote?", "").lower() == "remote",
        "sponsored": rec.get("Sponsorship", "").lower() == "yes",
        "track": rec.get("Resume", "") if rec.get("Resume", "") in TRACKS else "Other",
        "entry_date": d.isoformat() if d else None,
    }


def discard_reason(text: str) -> str:
    """Group detailed reasons: "Expired: 2+ days" and "Expired: 9 days" both become "Expired"."""
    s = re.split(r"[:(\[]| - ", str(text or "").strip(), maxsplit=1)[0].strip()
    return (s[:60] or "Unspecified")


def _iso_week(d: date) -> str:
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def build(valid_values: list[list[str]], discard_values: list[list[str]], runs_con, today: date | None = None) -> dict:
    valid = [p for i, r in enumerate(records(valid_values), 1) if (p := public_posting(r, i, today))]
    discarded = records(discard_values)
    valid.sort(key=lambda p: (p["entry_date"] or "", p["id"]), reverse=True)
    jobs = [{k: p[k] for k in ("id", *PUBLIC_FIELDS)} for p in valid]

    by_company: dict[str, dict] = {}
    for p in valid:
        c = by_company.setdefault(p["company"], {"valid": 0, "sponsored": 0, "sources": set()})
        c["valid"] += 1
        c["sponsored"] += p["sponsored"]
        c["sources"].add(p["source"])
    companies = sorted(
        ({"company": k, "total": v["valid"], "valid": v["valid"], "sponsored": v["sponsored"],
          "sponsorship_rate": round(v["sponsored"] / v["valid"], 4), "valid_rate": 1.0, "sources": len(v["sources"])}
         for k, v in by_company.items() if queries.clean_name(k)),
        key=lambda c: (-c["valid"], c["company"]))

    count = lambda xs: [{"label": k, "count": n} for k, n in sorted(_tally(xs).items(), key=lambda kv: -kv[1])]
    states = [s for s in (queries._state_of(p["location"]) for p in valid) if s]
    sponsoring = [c for c in companies if c["sponsored"] > 0]
    n = len(valid)
    insights = {
        "valid_postings": n,
        "remote_share": round(sum(p["remote"] for p in valid) / n, 4) if n else 0.0,
        "roles": count(p["job_type"] for p in valid),
        "tracks": [{"label": TRACKS.get(x["label"], "Other"), "count": x["count"]} for x in count(p["track"] for p in valid)],
        "states": count(states)[:12],
        "top_hiring": companies[:10],
        "companies_total": len(companies),
        "companies_sponsoring": len(sponsoring),
        "top_sponsors": sorted(sponsoring, key=lambda c: (-c["sponsored"], c["company"]))[:10],
    }

    weeks: dict[str, dict] = {}
    for p in valid:
        if p["entry_date"]:
            w = weeks.setdefault(_iso_week(date.fromisoformat(p["entry_date"])), {"postings": 0, "valid": 0, "sponsored": 0, "sources": set()})
            w["postings"] += 1
            w["valid"] += 1
            w["sponsored"] += p["sponsored"]
            w["sources"].add(p["source"])
    for r in discarded:
        d = parse_entry_date(r.get("Entry Date", ""), today)
        if d:
            w = weeks.setdefault(_iso_week(d), {"postings": 0, "valid": 0, "sponsored": 0, "sources": set()})
            w["postings"] += 1
            w["sources"].add(r.get("Source") or "Unknown")
    weekly = [{"week": k, "postings": v["postings"], "valid": v["valid"], "sponsored": v["sponsored"], "sources": len(v["sources"])}
              for k, v in sorted(weeks.items())]

    src_valid = _tally(p["source"] for p in valid)
    src_disc = _tally(r.get("Source") or "Unknown" for r in discarded)
    sources = sorted(
        ({"source": s, "accepted": src_valid.get(s, 0), "rejected": src_disc.get(s, 0),
          "total": src_valid.get(s, 0) + src_disc.get(s, 0),
          "yield_rate": round(src_valid.get(s, 0) / (src_valid.get(s, 0) + src_disc.get(s, 0)), 4)}
         for s in set(src_valid) | set(src_disc)),
        key=lambda r: -r["total"])

    reasons = _tally(discard_reason(r.get("Discard Reason", "")) for r in discarded)
    nd = sum(reasons.values()) or 1
    quarantine = [{"family": "pipeline", "reason": k, "rows": v, "share": round(v / nd, 4)}
                  for k, v in sorted(reasons.items(), key=lambda kv: -kv[1])[:12]]

    s = queries.summary(runs_con, _NoJobs(), _NoMarts())
    s.update({
        "valid_jobs": n,
        "sponsored_share": round(sum(p["sponsored"] for p in valid) / n, 4) if n else 0.0,
        "remote_jobs": sum(p["remote"] for p in valid),
        "companies": len(companies),
        "sources": len(set(src_valid) | set(src_disc)),
    })
    return {
        "summary": s,
        "weekly": weekly,
        "sources": sources,
        "companies": companies,
        "jobs": jobs,
        "job_sources": [{"source": k, "count": v} for k, v in sorted(src_valid.items(), key=lambda kv: -kv[1])],
        "pipeline": queries.pipeline_funnel(runs_con),
        "runs": queries.runs(runs_con),
        "quarantine": quarantine,
        "insights": insights,
        "funnel": [],
        "meta": {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "source": "google_sheet",
                 "unparsed_dates": sum(1 for p in valid if not p["entry_date"])},
    }


def _tally(xs) -> dict:
    out: dict = {}
    for x in xs:
        out[x] = out.get(x, 0) + 1
    return out


class _Empty:
    """Stand in for the stores summary() reads when the Sheet supplies those numbers instead."""
    def execute(self, sql, params=()):
        return self
    def fetchall(self):
        return [(0, 0, 0)]


_NoJobs = _Empty


class _NoMarts(_Empty):
    def fetchall(self):
        return [(0,)]


def read_sheet(credentials: str = ".local/credentials.json") -> tuple[list[list[str]], list[list[str]]]:
    import gspread
    from aggregator.config import SHEET_NAME
    from dashboard_api.gcp_secrets import service_account_info
    if hasattr(gspread, "service_account_from_dict"):
        gc = gspread.service_account_from_dict(service_account_info(credentials))
    else:
        gc = gspread.service_account(filename=credentials)
    sh = gc.open(SHEET_NAME)
    return sh.worksheet(VALID_SHEET).get_all_values(), sh.worksheet(DISCARD_SHEET).get_all_values()
