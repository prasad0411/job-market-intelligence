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
    """Kill the whole process group on exit.

    Chrome, chromedriver and subprocess helpers outlive a killed parent and
    hold pipes open, which is part of why the wrapper could not finish.
    """
    def _reap():
        try:
            pgid = os.getpgid(0)
            if pgid == os.getpid():          # only if we lead the group
                signal.signal(signal.SIGTERM, signal.SIG_IGN)
                os.killpg(pgid, signal.SIGTERM)
        except Exception as e:
            # Runs during interpreter shutdown, where logging and even stderr
            # may already be torn down. os.write to fd 2 is the lowest-level
            # option available and needs no live file object.
            with contextlib.suppress(Exception):
                os.write(2, ("child reaper failed: %s\n" % e).encode())
    atexit.register(_reap)


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
        _install_child_reaper()
        out["child_reaper"] = True
    if watchdog:
        _install_watchdog()
        out["watchdog"] = "%ds stall threshold" % WATCHDOG_STALL_SECONDS
    return out
