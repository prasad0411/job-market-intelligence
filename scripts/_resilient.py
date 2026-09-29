"""Installed for every scripts/*.py entry point.

resilience.py was wired into aggregator/__main__.py and outreach/__main__.py
only. Everything under scripts/ runs as `python3 scripts/NAME.py`, so it had
no socket ceiling - the 26 Sep cleanup ran 4,432s against a 600s cap.

Importing this at the top of a script installs the same guarantees.
"""
import os
import os as _os
import sys
import sys as _sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from aggregator import resilience as _r
    _r.install()
except Exception as _e:          # never block a script from starting
    import logging as _l
    _l.warning("resilience not installed: %s", _e)

# ── failure accounting ───────────────────────────────────────────────────────
# Ten of twelve scheduled modules always exited 0, whatever happened. The
# alerter infers failure from the log, but a process should say so itself.

import contextlib as _ctx
import atexit as _atexit
import logging as _logging

_failures = []
_exit_reporter_installed = [False]

# health_heartbeat already uses its exit code to mean "issues found".
_OWN_EXIT_CODE = ("health_heartbeat",)


def record_failure(where, exc=None):
    """Note that something failed. Never raises, never alters control flow."""
    try:
        msg = "%s: %s" % (where, str(exc)[:160]) if exc else str(where)
        _failures.append(msg)
        _logging.error("FAILURE %s", msg)
    except Exception:
        # Recording a failure must never itself raise: this is called from
        # inside except blocks that are already handling something.
        _logging.debug("record_failure could not record %r", where)


def failure_count():
    return len(_failures)


def _report_and_exit():
    """Exit non-zero if anything was recorded.

    os._exit rather than sys.exit: inside an atexit handler SystemExit is
    swallowed by the interpreter and the process still returns 0.
    """
    if not _failures:
        return
    # Interpreter shutdown: stderr may already be closed. The exit code
    # below is what matters, not the report.
    with _ctx.suppress(Exception):
        _sys.stderr.write("\n" + "=" * 70 + "\n")
        _sys.stderr.write("RUN FAILED: %d failure(s) recorded\n" % len(_failures))
        for _f in _failures[:10]:
            _sys.stderr.write("  - %s\n" % _f)
        if len(_failures) > 10:
            _sys.stderr.write("  ... and %d more\n" % (len(_failures) - 10))
        _sys.stderr.write("=" * 70 + "\n")
        _sys.stderr.flush()
    _os._exit(1)


def install_exit_reporter(module_name=None):
    """Idempotent. Skips modules whose exit code already means something."""
    if _exit_reporter_installed[0]:
        return False
    name = module_name or _os.path.basename(_sys.argv[0] or "").replace(".py", "")
    _exit_reporter_installed[0] = True
    if any(name.endswith(m) for m in _OWN_EXIT_CODE):
        return False
    _atexit.register(_report_and_exit)
    return True


# ── log formatting ───────────────────────────────────────────────────────────
# nightly_digest and build_auto_blacklist print instead of logging, so their
# output has no level, timestamp or module name.

_LOG_FORMAT = "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"
_LOG_DATEFMT = "%Y-%m-%d %H:%M:%S"


def install_logging(level=None):
    """Idempotent, and defers to a format the module chose deliberately."""
    level = _logging.INFO if level is None else level
    root = _logging.getLogger()
    if root.handlers:
        # Only add timestamps where there are none, so a module that picked
        # its own format keeps it.
        adjusted = 0
        for h in root.handlers:
            fmt = getattr(getattr(h, "formatter", None), "_fmt", "") or ""
            if "asctime" not in fmt:
                h.setFormatter(_logging.Formatter(_LOG_FORMAT, _LOG_DATEFMT))
                adjusted += 1
        return "adjusted %d handler(s)" % adjusted
    _logging.basicConfig(level=level, format=_LOG_FORMAT,
                         datefmt=_LOG_DATEFMT, stream=_sys.stdout)
    return "configured"


# Installed on import, because every scheduled script imports this module.
try:
    install_logging()
    install_exit_reporter()
except Exception as _e:
    _logging.warning("failure reporting not installed: %s", _e)
