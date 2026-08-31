"""
FIX 3 — Make the aggregator USE what the quality gate learned. (The whack-a-mole killer.)

THE PROBLEM (plain terms):
Right now your system works like a student who writes every mistake in a
notebook... and then never opens the notebook again. The quality gate
carefully records "leonardodrs really means Leonardo DRS" and "this title
is non-tech" into brain.json. But the aggregator — the part that actually
processes new jobs — never reads those notes. So the next time the same bad
slug shows up, it makes the same mistake, and YOU fix it by hand and commit.
That's the 20 `fix:` commits in your git log.

THE FIX:
Before the aggregator applies its hardcoded cleanup rules, it first checks
the notebook (the learned_* lists in brain.json). If the answer is already
known, use it. If not, fall through to exactly what it does today.

WHY IT'S SAFE:
If the learned store is empty or doesn't have an entry, these functions
return the input unchanged — so behavior is identical to today for anything
not already learned. It can only change output for cases you've ALREADY
corrected once. Nothing that's currently right can become wrong.

RESULT:
New bad slug -> the gate learns it once -> the aggregator applies it forever.
No more commit per edge case.
"""
import json
import os

BRAIN_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".local", "brain.json"
)

_cache = {"data": None, "mtime": 0}


def _brain():
    """Load brain.json, cached, refreshing only when the file changes."""
    try:
        mtime = os.path.getmtime(BRAIN_FILE)
    except OSError:
        return {}
    if _cache["data"] is None or mtime != _cache["mtime"]:
        try:
            with open(BRAIN_FILE, encoding="utf-8") as f:
                _cache["data"] = json.load(f)
            _cache["mtime"] = mtime
        except Exception:
            _cache["data"] = {}
    return _cache["data"] or {}


def fix_company_slug(company: str) -> str:
    """
    Turn a known bad slug into its proper name.
    'leonardodrs' -> 'Leonardo DRS' if the gate has learned it, else unchanged.
    """
    if not company:
        return company
    key = company.strip().lower()
    b = _brain()
    return (b.get("learned_slugs", {}).get(key)
            or b.get("learned_company_names", {}).get(key)
            or company)


def is_learned_non_tech(title: str) -> bool:
    """
    True if the gate previously learned this title pattern is non-tech
    (so the aggregator can discard it up front instead of after the fact).
    Empty learned list -> always False -> today's behavior.
    """
    if not title:
        return False
    t = title.lower()
    for pattern in _brain().get("learned_non_tech", []):
        if pattern.lower() in t:
            return True
    return False


def is_learned_clearance(company: str) -> bool:
    """
    True if this company was previously learned to be a security-clearance
    shop you don't want. Empty list -> always False -> today's behavior.
    """
    if not company:
        return False
    c = company.strip().lower()
    cl = set(_brain().get("learned_clearance", []))
    if c in cl:
        return True
    # Prefix match both ways, same rule is_user_blacklisted already uses.
    # Exact matching meant a learned "north atlantic industries" did not
    # block "North Atlantic Industries, Inc." as the feeds spell it, so the
    # company came back every run despite having been learned.
    for b in cl:
        if len(b) < 4 or len(c) < 4:
            continue
        if c.startswith(b) or b.startswith(c):
            return True
    return False


def is_user_blacklisted(company: str) -> bool:
    """
    True if YOU decided not to apply to this company, for any reason.

    Distinct from learned_clearance, which is what the system discovered.
    This is a personal decision - Philips does not hire from Northeastern,
    so no amount of page checking would ever reveal it.

    The key was write-only for a few hours after it was created: entries went
    in and nothing read them, so blacklisted companies would have reappeared
    on the next run. Same bug class as the learning loop that stayed dead for
    months. A key nobody reads is a feature that does not exist.
    """
    if not company:
        return False
    c = company.strip().lower()
    bl = set(_brain().get("user_blacklist_companies", []))
    if c in bl:
        return True
    # substring both ways: "Philips" should block "Philips North America",
    # and a blacklist entry of "morse corp" should block "MORSE Corp Co-op
    # Opportunities" as it appears in the feeds.
    for b in bl:
        if len(b) < 4 or len(c) < 4:
            continue
        # The blacklist entry must be a prefix of the company or vice versa,
        # not merely a substring. "philips" should block "Philips North
        # America" but a bare "Phi" must stay allowed - checking only the
        # entry's length let any 3 letter company match.
        if c.startswith(b) or b.startswith(c):
            return True
    return False


def is_user_blacklisted_title(title: str):
    """Terms YOU decided not to see, stored in brain.json.

    Sits beside user_blacklist_companies. The point is that YOU add entries
    rather than someone editing a pattern list in config: "robotics" is a
    preference, not a defect, and this morning's filters deliberately KEEP
    "Robotics Software Engineer" because it is a real SWE role at Zoox and
    Waymo. Whether you want it is your call, not the filter's.

    Whole-word matching, so "quant" does not catch "Quantitative" and
    "ml" does not catch "HTML". Returns the matched term, or None.

    Add one:
        b = Brain.get()
        b._data.setdefault("user_blacklist_titles", []).append("robotics")
        b.save()
    """
    import re as _r
    if not title:
        return None
    terms = _brain().get("user_blacklist_titles", []) or []
    hay = " " + _r.sub(r"[^a-z0-9]+", " ", str(title).lower()).strip() + " "
    for t in terms:
        t = _r.sub(r"[^a-z0-9]+", " ", str(t).lower()).strip()
        if t and " " + t + " " in hay:
            return t
    return None


# ---- Where to plug this in (aggregator/extractors.py or processors.py) ----
#
# Wherever a job's company/title is finalized, BEFORE the hardcoded rules:
#
#     from apply_learned import fix_company_slug, is_learned_non_tech, is_learned_clearance
#
#     company = fix_company_slug(company)          # apply learned name fix first
#     if is_learned_clearance(company):
#         discard(job, reason="learned_clearance"); continue
#     if is_learned_non_tech(title):
#         discard(job, reason="learned_non_tech"); continue
#
#     # ... your existing hardcoded cleanup runs after, unchanged ...
