"""
Swallowed-exception accounting.

`except Exception: pass` is not wrong in itself - a failed cache write should
not kill a run. It becomes wrong when it hides a SYSTEMATIC failure. The
learning loop was dead for months behind one of these, and a job-id save that
ran 1,200 times per run cost 12 seconds behind another.

Converting all 165 sites to log lines would produce noise nobody reads. What
actually distinguishes "exceptional" from "broken" is FREQUENCY: one swallow
in a run is noise, five hundred is a feature that stopped working.

So each guarded site records a count, and the run prints a summary. Nothing
is raised, nothing changes behaviour.

    from aggregator.swallowed import swallow

    try:
        brain.save()
    except Exception as e:
        swallow("brain.save", e)
"""
import logging
import threading

log = logging.getLogger(__name__)

_counts = {}
_first = {}
_lock = threading.Lock()

# A site firing more than this in one run is a broken feature, not bad luck.
ALERT_THRESHOLD = 25


def swallow(site, exc=None):
    """Record a swallowed exception. Never raises."""
    try:
        with _lock:
            _counts[site] = _counts.get(site, 0) + 1
            if site not in _first and exc is not None:
                _first[site] = "{}: {}".format(type(exc).__name__, str(exc)[:120])
    except Exception:
        pass


def report(verbose=True):
    """Summarise the run. Returns [(site, count, first_error), ...] sorted."""
    with _lock:
        rows = sorted(_counts.items(), key=lambda kv: -kv[1])
        out = [(s, n, _first.get(s, "")) for s, n in rows]

    if verbose and out:
        loud = [r for r in out if r[1] >= ALERT_THRESHOLD]
        # print() as well as log(): the run summary reaches the cron logs via
        # stdout, so a log-only report was invisible in every run ever
        # recorded. A silent-failure detector that fails silently is worse
        # than none, because it looks like proof that nothing is wrong.
        _emit = lambda m: (log.info(m), print(m))
        _emit("=" * 64)
        _emit("SWALLOWED EXCEPTIONS: {} site(s), {} total".format(
            len(out), sum(n for _, n, _ in out)))
        for site, n, first in out[:15]:
            mark = "  !! " if n >= ALERT_THRESHOLD else "     "
            _emit("{}{:<34} {:5d}   {}".format(mark, site, n, first[:70]))
        if loud:
            log.warning(
                "%d site(s) swallowed >=%d exceptions - that is a broken "
                "feature, not an edge case", len(loud), ALERT_THRESHOLD)
        _emit("=" * 64)
    return out


def reset():
    with _lock:
        _counts.clear()
        _first.clear()
