#!/usr/bin/env python3
"""
Permanent scheduler daemon — single KeepAlive process managed by launchd.
Replaces all individual launchd plists.
"""
import time
import datetime
import subprocess
import threading
import logging
import os
import json
import signal
import sys
import socket

BASE = "/Users/prasadkanade/Documents/Prasad Kanade/Job Hunt Tracker"
LOG_FILE = f"{BASE}/.local/scheduler.log"
CRON = f"{BASE}/scripts/cron_runner.sh"
STATE_FILE = f"{BASE}/.local/scheduler_state.json"
STATE_TMP = f"{BASE}/.local/scheduler_state.json.tmp"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [scheduler] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(sys.stdout)
    ]
)
log = logging.getLogger(__name__)

# Types the dispatch loop in main() actually handles.
# A type not listed here will NEVER be scheduled.
KNOWN_JOB_TYPES = {"times", "interval", "post_write"}

JOBS = [
    {"name":"aggregator","module":"aggregator","type":"times","times":[(8,0),(15,0),(21,0)],"timeout":5400,"max_gap":8*3600},
    {"name":"send_scheduled","module":"scripts/send_scheduled","type":"times","times":[(9,0),(10,30),(11,30),(12,30)],"timeout":300,"max_gap":24*3600},
    {"name":"outreach","module":"outreach","type":"interval","interval_hours":2,"timeout":3600,"max_gap":30*3600},
    {"name":"nightly_digest","module":"scripts/nightly_digest","type":"times","times":[(0,22)],"timeout":120,"max_gap":30*3600},
    {"name":"build_auto_blacklist","module":"scripts/build_auto_blacklist","type":"times","times":[(0,30)],"timeout":120,"max_gap":30*3600},
    {"name":"ats_discovery","module":"scripts/ats_discovery","type":"interval","interval_hours":24,"timeout":600,"max_gap":48*3600},
    {"name":"discarded_auditor","module":"scripts/discarded_auditor","type":"interval","interval_hours":72,"timeout":300,"max_gap":96*3600},
    {"name":"quality_gate","module":"scripts/quality_gate","type":"post_write","timeout":180},
    {"name":"health_heartbeat","module":"scripts/health_heartbeat","type":"post_write","timeout":60},
    {"name":"cleanup_not_applied","module":"scripts/cleanup_not_applied","type":"times","times":[(7,30)],"timeout":300,"max_gap":30*3600},
    {"name":"retry_simplify","module":"scripts/retry_simplify","type":"times","times":[(6,0)],"timeout":300,"max_gap":30*3600},
    {"name":"process_bounces","module":"scripts/process_bounces","type":"interval","interval":1800,"timeout":120,"max_gap":3600},
    {"name":"watchdog","module":None,"type":"interval","interval":1800,"timeout":60,"max_gap":3600},
]

_running = {}
_running_lock = threading.Lock()

# ── Startup config validation (self-healing) ──
def _validate_jobs_config():
    """Validate JOBS at import time — catch config bugs before they crash the loop."""
    for job in JOBS:
        name = job.get("name", "UNNAMED")
        if "type" not in job:
            log.error(f"Config error: job '{name}' has no 'type' key")
        elif job["type"] not in KNOWN_JOB_TYPES:
            log.error(
                f"Config error: job '{name}' has type '{job['type']}' which the "
                f"dispatch loop does not handle - IT WILL NEVER RUN. "
                f"Known types: {sorted(KNOWN_JOB_TYPES)}"
            )
        if job.get("type") == "interval":
            if not any(k in job for k in ("interval", "interval_hours", "interval_minutes")):
                log.error(f"Config error: job '{name}' has type=interval but no interval/interval_hours key")
        if job.get("type") == "times" and "times" not in job:
            log.error(f"Config error: job '{name}' has type=times but no 'times' key")
        if "timeout" not in job:
            log.warning(f"Config warning: job '{name}' has no timeout — defaulting to 300s")
            job["timeout"] = 300

_validate_jobs_config()

def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except Exception:
        return {}

def save_state(state):
    try:
        with open(STATE_TMP, "w") as f:
            json.dump(state, f, indent=2)
        os.replace(STATE_TMP, STATE_FILE)
    except Exception as e:
        log.error(f"Failed to save state: {e}")

def has_network(timeout=5):
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(timeout)
        s.connect(("8.8.8.8", 53))
        s.close()
        return True
    except Exception:
        return False

def wait_for_network(max_wait=300):
    waited = 0
    while waited < max_wait:
        if has_network():
            if waited > 0:
                log.info(f"Network up after {waited}s")
            return True
        log.warning(f"No network, waiting... ({waited}s/{max_wait}s)")
        time.sleep(15)
        waited += 15
    log.warning("No network after 5min — proceeding anyway")
    return False

def _lock_path(name):
    return os.path.join(BASE, ".local", f"task_{name}.lock")

def _acquire_disk_lock(name):
    """Cross-process lock that survives scheduler restarts.
    Returns True if we got the lock, False if task genuinely running."""
    lp = _lock_path(name)
    os.makedirs(os.path.dirname(lp), exist_ok=True)
    if os.path.exists(lp):
        try:
            old_pid = int(open(lp).read().strip())
            os.kill(old_pid, 0)  # raises if pid is dead
            return False          # pid alive -> really running
        except (ValueError, ProcessLookupError, PermissionError):
            log.warning(f"Reclaiming stale lock for {name} (dead pid)")
        except Exception:
            return False
    open(lp, "w").write(str(os.getpid()))
    return True

def _release_disk_lock(name):
    try:
        os.remove(_lock_path(name))
    except Exception:
        pass

def run_job(job):
    name = job["name"]
    timeout = job.get("timeout", 600)
    with _running_lock:
        if _running.get(name):
            log.info(f"⏭ Skipping {name} — already running (in-process)")
            return
        _running[name] = True
    if not _acquire_disk_lock(name):
        log.info(f"⏭ Skipping {name} — already running (another process)")
        with _running_lock:
            _running[name] = False
        return
    try:
        wait_for_network(300)
        log.info(f"▶ Running: {name}")
        if name == "watchdog":
            cmd = ["bash", f"{BASE}/scripts/watchdog.sh"]
        else:
            cmd = ["bash", CRON, job["module"]]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=BASE)
        if result.returncode == 0:
            log.info(f"✓ {name} done (exit 0)")
        else:
            log.warning(f"✗ {name} failed (exit {result.returncode})")
            if result.stderr:
                log.warning(f"  stderr: {result.stderr[:300]}")
    except subprocess.TimeoutExpired:
        log.error(f"✗ {name} timed out after {timeout}s")
    except Exception as e:
        log.error(f"✗ {name} error: {e}")
    finally:
        _release_disk_lock(name)
        with _running_lock:
            _running[name] = False

_job_failures: dict = {}  # tracks consecutive failures per job

def run_post_write_jobs(state):
    """Run every type=post_write job. Called after a successful aggregator run.

    Before this existed, the main() dispatch loop only handled "times" and
    "interval", so quality_gate and health_heartbeat never executed at all.
    """
    for pj in JOBS:
        if pj.get("type") != "post_write":
            continue
        try:
            log.info(f"post_write: running {pj['name']}")
            run_job(pj)
            state[pj["name"]] = datetime.datetime.now().isoformat()
            save_state(state)
        except Exception as e:
            log.error(f"post_write job {pj['name']} failed: {e}")


def run_job_async(job, state):
    name = job["name"]

    def _run():
        run_job(job)
        health_f = f"{BASE}/.local/health_{name}.json"
        succeeded = True
        try:
            import json as _j
            h = _j.load(open(health_f))
            if h.get("exit_code", 1) != 0:
                succeeded = False
                _job_failures[name] = _job_failures.get(name, 0) + 1
                if _job_failures[name] == 1:
                    log.warning(f"{name} failed - will retry in 30 min")
                    time.sleep(1800)
                    log.info(f"Retrying {name} (attempt 2)")
                    run_job(job)
                    try:
                        h2 = _j.load(open(health_f))
                        succeeded = h2.get("exit_code", 1) == 0
                    except Exception:
                        succeeded = False
                else:
                    log.warning(f"{name} failed twice - skipping until next window")
                    _job_failures[name] = 0
            else:
                _job_failures[name] = 0
        except Exception:
            pass

        state[name] = datetime.datetime.now().isoformat()
        save_state(state)

        if name == "aggregator" and succeeded:
            run_post_write_jobs(state)

    t = threading.Thread(target=_run, name=f"job-{name}", daemon=True)
    t.start()


def should_run_timed(job, state, now):
    name = job["name"]
    last_run_str = state.get(name)
    for (h, m) in job["times"]:
        scheduled = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if abs((now - scheduled).total_seconds()) > 180:  # 3-min window
            continue
        if last_run_str:
            last_run = datetime.datetime.fromisoformat(last_run_str)
            if (now - last_run).total_seconds() < 600:
                return False
        return True
    return False

def _resolve_interval_seconds(job):
    """Self-healing: accept interval (seconds), interval_hours, or interval_minutes.
    Logs a warning if the config is ambiguous so we catch it early."""
    if "interval" in job:
        return job["interval"]
    if "interval_hours" in job:
        return job["interval_hours"] * 3600
    if "interval_minutes" in job:
        return job["interval_minutes"] * 60
    log.warning(f"Job '{job.get('name', '?')}' has type=interval but no interval key — defaulting to 1hr")
    return 3600

def should_run_interval(job, state, now):
    name = job["name"]
    last_run_str = state.get(name)
    if not last_run_str:
        return True
    last_run = datetime.datetime.fromisoformat(last_run_str)
    return (now - last_run).total_seconds() >= _resolve_interval_seconds(job)

def check_missed_on_wake(state, now):
    log.info("Checking for missed jobs since last run...")
    wait_for_network(300)
    for job in JOBS:
        name = job["name"]
        max_gap = job.get("max_gap", 30 * 3600)
        last_run_str = state.get(name)
        if not last_run_str:
            log.info(f"  {name}: no prior state — initializing")
            state[name] = (now - datetime.timedelta(seconds=max_gap - 60)).isoformat()
            save_state(state)
            continue
        last_run = datetime.datetime.fromisoformat(last_run_str)
        elapsed = (now - last_run).total_seconds()
        if elapsed > max_gap:
            log.info(f"  Missed: {name} ({elapsed/3600:.1f}h ago) — running now")
            run_job(job)
            state[name] = datetime.datetime.now().isoformat()
            save_state(state)
            time.sleep(5)
        else:
            log.info(f"  OK: {name} ({elapsed/3600:.1f}h ago)")

def rotate_log_if_needed():
    try:
        if not os.path.exists(LOG_FILE):
            return
        with open(LOG_FILE) as f:
            lines = f.readlines()
        if len(lines) > 1000:
            with open(LOG_FILE, "w") as f:
                f.writelines(lines[-700:])
    except Exception:
        pass

def main():
    log.info("=" * 50)
    log.info("Scheduler daemon started")
    log.info("=" * 50)

    def handle_signal(sig, frame):
        log.info("Scheduler stopping (signal received)")
        sys.exit(0)
    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    state = load_state()
    check_missed_on_wake(state, datetime.datetime.now())

    last_minute = -1
    loop_count = 0

    while True:
        try:
            now = datetime.datetime.now()
            loop_count += 1
            if loop_count % 360 == 0:
                rotate_log_if_needed()
            if now.minute == last_minute:
                time.sleep(10)
                continue
            last_minute = now.minute
            state = load_state()
            for job in JOBS:
                name = job["name"]
                should_run = False
                if job["type"] == "times":
                    should_run = should_run_timed(job, state, now)
                elif job["type"] == "interval":
                    should_run = should_run_interval(job, state, now)
                if should_run:
                    state[name] = now.isoformat()
                    save_state(state)
                    run_job_async(job, state)
            time.sleep(10)
        except Exception as e:
            log.error(f"Scheduler loop error: {e}")
            time.sleep(30)

if __name__ == "__main__":
    main()
