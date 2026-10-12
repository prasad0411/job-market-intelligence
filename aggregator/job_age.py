"""
Posting-date extraction for ATS APIs.

Every ATS returns a real posting date. Before this module existed, all 8
scrapers in direct_sources.py hardcoded "age": "0d", so every direct-ATS
job claimed it was posted today and the 3-day filter had nothing real to
act on. That was the root cause of months-old jobs reaching the sheet.

Returns "unknown" when nothing parses, so the age gate can drop the job
rather than assume it is fresh.
"""
import re
import datetime as _dt
from typing import Optional

_MAX_SANE_DAYS = 3650


def age_from_timestamp(value) -> Optional[str]:
    """Convert an ATS date field to a '<N>d' age string. None if unparseable."""
    if value in (None, "", 0):
        return None
    now = _dt.datetime.now(_dt.timezone.utc)
    dt = None
    try:
        if isinstance(value, bool):
            return None
        if isinstance(value, (int, float)):
            ts = float(value)
            # Lever uses epoch milliseconds
            if ts > 1e11:
                ts = ts / 1000.0
            dt = _dt.datetime.fromtimestamp(ts, tz=_dt.timezone.utc)
        elif isinstance(value, str):
            s = value.strip()
            if not s:
                return None
            if s.isdigit():
                return age_from_timestamp(int(s))
            s = s.replace("Z", "+00:00")
            dt = _dt.datetime.fromisoformat(s)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=_dt.timezone.utc)
    except Exception:
        return None
    if dt is None:
        return None
    days = (now - dt).days
    if days < 0 or days > _MAX_SANE_DAYS:
        return None
    return "{}d".format(days)


def age_from_workday(posted_on) -> Optional[str]:
    """Workday returns human text: 'Posted Today', 'Posted 3 Days Ago',
    'Posted 30+ Days Ago'. Bucketed, not exact, but far better than '0d'."""
    if not posted_on:
        return None
    t = str(posted_on).lower()
    if "today" in t or "just posted" in t:
        return "0d"
    if "yesterday" in t:
        return "1d"
    m = re.search(r"(\d+)\+?\s*day", t)
    if m:
        return "{}d".format(int(m.group(1)))
    m = re.search(r"(\d+)\+?\s*month", t)
    if m:
        return "{}d".format(int(m.group(1)) * 30)
    m = re.search(r"(\d+)\+?\s*week", t)
    if m:
        return "{}d".format(int(m.group(1)) * 7)
    return None


def pick_age(job: dict, keys, fallback_key: str = None) -> str:
    """Try each date key in order; fall back to Workday text. 'unknown' if none."""
    if not isinstance(job, dict):
        return "unknown"
    for k in keys:
        got = age_from_timestamp(job.get(k))
        if got is not None:
            return got
    if fallback_key:
        got = age_from_workday(job.get(fallback_key))
        if got is not None:
            return got
    return "unknown"
