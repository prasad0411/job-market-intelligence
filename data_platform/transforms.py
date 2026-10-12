"""
Pure transformation rules for the medallion layers.

Kept free of Spark and Parquet so the business logic is testable without a
cluster or a Spark install. The engine modules call these.
"""

# Silver: rows that fail these are quarantined, not dropped silently.
REQUIRED_FIELDS = ("url", "company", "title")
PLACEHOLDER_COMPANIES = {"", "unknown", "n/a", "na", "none", "null", "-"}


def normalize_company(name):
    """Canonical company key: lowercase, punctuation-stripped, suffixes removed."""
    if not name:
        return ""
    s = str(name).strip().lower()
    for ch in ",.'\"()&/":
        s = s.replace(ch, " ")
    parts = [p for p in s.split() if p]
    suffixes = {"inc", "llc", "ltd", "limited", "corp", "corporation",
                "co", "company", "plc", "gmbh", "sa", "ag", "holdings",
                "group", "technologies", "technology", "labs", "the"}
    while parts and parts[-1] in suffixes:
        parts.pop()
    return " ".join(parts)


def is_valid_row(row):
    """Silver gate. Returns (ok, reason)."""
    for f in REQUIRED_FIELDS:
        v = row.get(f)
        if v is None or str(v).strip() == "":
            return False, "missing_%s" % f
    if normalize_company(row.get("company")) in PLACEHOLDER_COMPANIES:
        return False, "placeholder_company"
    url = str(row.get("url", ""))
    if not url.startswith(("http://", "https://")):
        return False, "malformed_url"
    if "google.com/search" in url:
        return False, "search_fallback_url"
    return True, ""


def dedup_key(row):
    """Silver dedup grain: one row per company + title + source."""
    return (
        normalize_company(row.get("company")),
        str(row.get("title", "")).strip().lower(),
        str(row.get("source", "")).strip().lower(),
    )


def partition_key(row):
    """Physical partition: source and ISO week, so scans prune by both."""
    src = str(row.get("source") or "unknown").strip().lower().replace(" ", "_")
    run = str(row.get("run_id") or "")
    # run_YYYYMMDD_HHMMSS -> YYYY-Www
    week = "unknown"
    if run.startswith("run_") and len(run) >= 12:
        ymd = run[4:12]
        try:
            import datetime
            d = datetime.date(int(ymd[:4]), int(ymd[4:6]), int(ymd[6:8]))
            iso = d.isocalendar()
            week = "%d-W%02d" % (iso[0], iso[1])
        except Exception:
            week = "unknown"
    return src, week


def gold_grain(row):
    """Gold aggregate grain: company + resume track."""
    return (normalize_company(row.get("company")),
            str(row.get("resume_type") or "UNKNOWN").upper())
