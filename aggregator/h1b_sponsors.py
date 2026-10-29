"""
Employer level H1B sponsorship, from US Department of Labor filings.

The previous signal was a hand written list of 152 names, which answered
"yes" or "unknown" and nothing else. 569 rows in the sheet sat in that
unknown bucket with no way to rank them.

This loads 9,867 employers that filed certified H1B labor condition
applications in FY2025, each with the number of positions filed. Volume
matters: a company filing 2,815 positions is a far safer bet than one
filing 2.

An LCA is the step before a petition, so a match is strong evidence rather
than proof, and no match is not evidence of refusal. That is why this feeds
ranking and not a hard filter.

Source: github.com/flexapply/companies-that-sponsor-h1b, rebuilt quarterly.
Cached locally so a run never depends on GitHub being reachable.
"""
import contextlib
import json
import logging
import os
import re
import time

log = logging.getLogger(__name__)

URL = ("https://raw.githubusercontent.com/flexapply/"
       "companies-that-sponsor-h1b/main/data/h1b-sponsors-fy2025.json")
CACHE = os.path.join(".local", "h1b_sponsors.json")
MAX_AGE_DAYS = 90

TIER_STRONG, TIER_MODERATE, TIER_LOW, TIER_UNKNOWN, TIER_NO = 1, 2, 3, 4, 5

_SUFFIX = re.compile(
    r"\b(inc|llc|ltd|limited|corp|corporation|company|co|group|holdings|"
    r"services|technologies|technology|usa|us|america|americas|"
    r"international|plc|lp|llp|the)\b", re.I)

_index = {}

# Short brand names that prefix matching cannot resolve safely. Lowering the
# prefix threshold to 4 matched Meta and Citi correctly but also matched Hunt
# to Huntington National Bank and CACI to CA, Inc. These are verified by hand.
_ALIAS = {
    "meta": "metaplatforms",
    "citi": "citibankna",
    "visa": "visaoperations",
    "zoom": "zoomcommunications",
    "ramp": "rampbusiness",
    "sofi": "sofibankna",
    "cboe": "cboeglobalmarkets",
    "nice": "nicesystems",
    "axon": "axonenterprise",
    "dell": "dellproductslp",
}


def _norm(s):
    s = (s or "").lower()
    s = re.sub(r"[.,&/()-]", " ", s)
    s = _SUFFIX.sub(" ", s)
    return re.sub(r"[^a-z0-9]", "", s)


def _valid(data):
    """A cache file of the wrong shape is worse than no cache: an earlier
    module wrote a plain list of names here, which crashed the loader."""
    if isinstance(data, list) and data and isinstance(data[0], dict):
        return data
    return None


def _fetch():
    import requests
    r = requests.get(URL, timeout=60)
    r.raise_for_status()
    data = r.json()
    os.makedirs(os.path.dirname(CACHE), exist_ok=True)
    tmp = CACHE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f)
    os.replace(tmp, CACHE)          # atomic, so a crash cannot leave a partial file
    return data


def _load():
    if _index:
        return _index
    data = None
    with contextlib.suppress(Exception):
        age = (time.time() - os.path.getmtime(CACHE)) / 86400.0
        if age < MAX_AGE_DAYS:
            data = _valid(json.load(open(CACHE)))
    if data is None:
        try:
            data = _fetch()
        except Exception as e:
            log.warning("h1b sponsor list unavailable: %s", e)
            with contextlib.suppress(Exception):
                data = _valid(json.load(open(CACHE)))   # stale beats nothing
            if data is None:
                return _index
    for e in data:
        k = _norm(e.get("name", ""))
        n = e.get("lcas", 0) or 0
        # "E-Technologies Group, LLC." strips down to the single letter "e",
        # which then prefix matched Esri, EnFi and anything else starting
        # with e. A key this short carries no identity.
        if len(k) < 4:
            continue
        if k not in _index or n > _index[k][1]:
            _index[k] = (e.get("name", ""), n)
    return _index


def lookup(company):
    """Returns (tier, positions, matched_name). Never raises."""
    idx = _load()
    if not idx:
        return TIER_UNKNOWN, 0, ""
    k = _norm(company)
    if not k:
        return TIER_UNKNOWN, 0, ""
    hit = idx.get(k)
    if not hit and k in _ALIAS:
        hit = idx.get(_ALIAS[k])
    if not hit and len(k) >= 5:
        # longest prefix either way: "amazonwebservices" vs "amazon"
        best = None
        for ik, iv in idx.items():
            if ik.startswith(k) or k.startswith(ik):
                if best is None or iv[1] > best[1]:
                    best = iv
        hit = best
    if not hit:
        return TIER_UNKNOWN, 0, ""
    name, n = hit
    if n >= 100:
        return TIER_STRONG, n, name
    if n >= 20:
        return TIER_MODERATE, n, name
    return TIER_LOW, n, name
