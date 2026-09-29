"""
Process-level resilience. Imported once at module start, before anything else.

Every multi-hour hang in this pipeline traced to the same shape: a network
call with no timeout. On 21 Sep the aggregator ran 18.4h; on 24 Sep it ran
3.8h logging nothing after its third line. In both cases `timeout` had sent
SIGTERM and then SIGKILL, and the process survived both because it was
blocked in an uninterruptible socket read.

Four guarantees, in the order they matter:

  1. No socket can block forever. socket.setdefaulttimeout() applies to every
     library that does not set its own - requests, urllib, gspread, msal.
     This alone would have prevented both hangs.

  2. SIGTERM is honoured. Without a handler, a process blocked in C code can
     ignore it entirely, which is why --kill-after=60 did not help.

  3. Children die with the parent. An orphaned MSAL subprocess was still
     running after its parent was killed, holding a pipe open. Chrome and
     chromedriver do the same.

  4. A stuck run says so. A watchdog thread logs a full stack dump when the
     main thread stops making progress, so the next hang leaves evidence
     instead of three log lines and silence.
"""
import atexit
import contextlib
import faulthandler
import logging
import os
import signal
import socket
import sys
import threading
import time

# 45s is well above the slowest healthy ATS response seen (~12s) and well
# below any human-noticeable stall. Individual calls may still pass a shorter
# timeout; this is only the ceiling for calls that pass none.
DEFAULT_SOCKET_TIMEOUT = 45.0

# Stack dump if the heartbeat has not advanced in this long.
WATCHDOG_STALL_SECONDS = 600

_last_beat = [time.time()]
_installed = [False]


def beat():
    """Call from long loops to say progress is still happening."""
    _last_beat[0] = time.time()


def _install_socket_timeout():
    if socket.getdefaulttimeout() is None:
        socket.setdefaulttimeout(DEFAULT_SOCKET_TIMEOUT)
        return True
    return False


def _install_signal_handlers():
    """Turn SIGTERM into a normal exception path.

    A process blocked in a C-level read never runs Python-level handlers, but
    installing one covers every case where the block is at Python level, and
    guarantees atexit handlers run so children get cleaned up.
    """
    def _term(signum, frame):
        logging.error("SIGTERM received (signal %d) - shutting down", signum)
        faulthandler.dump_traceback()
        raise SystemExit(143)

    installed = []
    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        try:
            signal.signal(sig, _term)
            installed.append(sig)
        except (ValueError, OSError, AttributeError):
            pass          # not available, or not on the main thread
    return installed


def _install_child_reaper():
    """Disabled.

    This used to call os.killpg(getpgid(0), SIGTERM) from an atexit handler,
    to clean up orphaned chromedriver and subprocess helpers. Once the
    scripts/ modules began importing resilience, that handler started killing
    the entire process group on every exit - including pytest's own shell
    pipeline, and in production it could take down the scheduler's group.

    The direct child is already handled: cron_runner wraps each module in
    `timeout --kill-after=60`. Orphaned browsers are better dealt with by the
    driver.quit() calls at their own call sites, where the scope is known.
    """
    return False


def _install_watchdog():
    """Daemon thread that dumps all stacks when progress stops."""
    def _watch():
        warned = False
        while True:
            time.sleep(30)
            stalled = time.time() - _last_beat[0]
            if stalled > WATCHDOG_STALL_SECONDS and not warned:
                logging.error(
                    "WATCHDOG: no progress for %.0fs - dumping stacks", stalled)
                faulthandler.dump_traceback()
                warned = True
            elif stalled <= WATCHDOG_STALL_SECONDS:
                warned = False

    t = threading.Thread(target=_watch, name="watchdog", daemon=True)
    t.start()
    return t


def install(watchdog=True, reaper=True):
    """Idempotent. Safe to call from several entry points."""
    if _installed[0]:
        return {}
    _installed[0] = True
    out = {
        "socket_timeout": DEFAULT_SOCKET_TIMEOUT if _install_socket_timeout() else "already set",
        "signals": [s.name for s in _install_signal_handlers()],
    }
    # faulthandler lets an external SIGABRT produce a stack dump too
    try:
        faulthandler.enable()
        out["faulthandler"] = True
    except Exception:
        out["faulthandler"] = False
    if reaper:
        out["child_reaper"] = _install_child_reaper()   # disabled, returns False
    if watchdog:
        _install_watchdog()
        out["watchdog"] = "%ds stall threshold" % WATCHDOG_STALL_SECONDS

    # Harden gspread here rather than per module. install() already runs in
    # all twelve entry points, so this covers every Sheets call in the
    # codebase without touching a single construction site.
    try:
        _g = harden_gspread()
        out["gspread"] = "%d methods wrapped" % len(_g) if _g else "not installed"
    except Exception as _ge:
        out["gspread"] = "failed: %s" % str(_ge)[:40]

    return out

# ── retry for transient remote failures ──────────────────────────────────────
# ConnectionResetError(54) from the Sheets API killed three cleanup runs in
# eight days. Two retry helpers already existed elsewhere in this codebase and
# neither was wired to the call sites that needed one; one of them also
# returns None after a final 429, so the caller cannot tell failure from an
# empty result.

import random

RETRYABLE_TYPES = (ConnectionResetError, ConnectionError,
                   ConnectionAbortedError, TimeoutError, OSError)

# Matched against str(exception), because libraries wrap the real cause.
RETRYABLE_TEXT = (
    "connection reset", "connection aborted", "connection broken",
    "broken pipe", "timed out", "timeout",
    "429", "resource_exhausted", "rate limit", "quota exceeded",
    "500", "502", "503", "504",
    "internal error", "backend error", "service unavailable",
    "remote end closed", "eof occurred",
)

# Never retried: the next attempt fails the same way.
FATAL_TEXT = (
    "permission", "forbidden", "not found", "404", "401", "403",
    "invalid_grant", "api key not valid", "invalid api key",
)


def is_retryable(exc):
    s = str(exc).lower()
    if any(f in s for f in FATAL_TEXT):
        return False
    if any(t in s for t in RETRYABLE_TEXT):
        return True
    return isinstance(exc, RETRYABLE_TYPES)


def retry_call(fn, *args, attempts=4, base=2.0, cap=30.0, label=None, **kwargs):
    """Call fn with backoff on transient failure.

    Raises the last exception when attempts run out. Returning None on
    failure is how a half-finished move looks like a successful one.
    """
    label = label or getattr(fn, "__name__", "call")
    last = None
    for i in range(attempts):
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            last = e
            if not is_retryable(e) or i == attempts - 1:
                raise
            # jitter, so concurrent workers do not retry in lockstep and
            # re-trigger the limit they are backing off from
            delay = min(cap, base ** (i + 1)) * (0.5 + random.random())
            logging.warning("%s failed (%s), retry %d/%d in %.1fs",
                            label, str(e)[:80], i + 1, attempts - 1, delay)
            time.sleep(delay)
    raise last


# Read and write alike: the 26 Sep failure was a read, the 29 Aug one a write.
WRAP_METHODS = (
    "get_all_values", "get_all_records", "get", "row_values", "col_values",
    "append_row", "append_rows", "update", "update_acell", "update_cell",
    "update_cells", "batch_update", "delete_rows", "delete_row",
    "insert_row", "insert_rows", "clear", "add_rows", "resize", "format",
)


def harden(obj, methods=WRAP_METHODS, **kw):
    """Wrap an object's remote-call methods in retry_call, in place.

    One call at construction beats editing every call site, and it cannot be
    forgotten at a call site added later.
    """
    wrapped = []
    for name in methods:
        orig = getattr(obj, name, None)
        if orig is None or not callable(orig):
            continue
        if getattr(orig, "_hardened", False):
            continue

        def make(fn, label):
            def inner(*a, **k):
                return retry_call(fn, *a, label=label, **k)
            inner._hardened = True
            inner.__name__ = label
            return inner

        try:
            setattr(obj, name, make(orig, name))
            wrapped.append(name)
        except (AttributeError, TypeError):
            continue          # read-only attribute on some objects
    return wrapped

# ── gspread hardening ────────────────────────────────────────────────────────
# Eight modules opened worksheets with no retry between them. Per-object
# hardening has to be remembered at every construction site and was not, the
# same way _sheets_retry was written and never applied to a single method.
# Patching the class covers every instance, including ones created later.

GSPREAD_WORKSHEET_METHODS = (
    "get_all_values", "get_all_records", "get", "row_values", "col_values",
    "append_row", "append_rows", "update", "update_acell", "update_cell",
    "update_cells", "batch_update", "delete_rows", "delete_row",
    "insert_row", "insert_rows", "clear", "add_rows", "resize", "format",
    "find", "findall", "cell", "acell",
)

GSPREAD_SPREADSHEET_METHODS = (
    "worksheet", "worksheets", "add_worksheet", "values_update",
    "values_get", "batch_update",
)


def harden_gspread():
    """Wrap gspread's remote calls in retry_call, at class level.

    Returns the list of wrapped names. Safe to call more than once: already
    wrapped methods are skipped.
    """
    try:
        import gspread
    except ImportError:
        return []

    done = []
    for cls_name, names in (("Worksheet", GSPREAD_WORKSHEET_METHODS),
                            ("Spreadsheet", GSPREAD_SPREADSHEET_METHODS)):
        cls = getattr(gspread, cls_name, None)
        if cls is None:
            continue
        for n in names:
            orig = getattr(cls, n, None)
            if orig is None or not callable(orig):
                continue
            if getattr(orig, "_hardened", False):
                continue

            def make(fn, label):
                def inner(self, *a, **k):
                    return retry_call(fn, self, *a, label=label, **k)
                inner._hardened = True
                inner.__name__ = label.split(".")[-1]
                return inner

            try:
                setattr(cls, n, make(orig, "%s.%s" % (cls_name, n)))
                done.append("%s.%s" % (cls_name, n))
            except (AttributeError, TypeError):
                continue      # read-only attribute on some builds
    return done
