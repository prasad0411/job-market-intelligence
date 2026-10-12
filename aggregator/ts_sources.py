"""
Bridge to the TypeScript ingestion service in ingest-ts/ (The Muse and Remotive APIs).

The service prints one JSON record per line on stdout and a JSON summary on stderr.
Everything here fails open: a missing Node, a missing build, a timeout, or a bad line
costs those records, never the run.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from pathlib import Path

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
ENTRY = ROOT / "ingest-ts" / "dist" / "index.js"
REQUIRED = ("company", "title", "location", "url", "job_id", "source")
# launchd starts jobs with a minimal PATH, so look in the usual Homebrew locations too.
NODE_CANDIDATES = ("/opt/homebrew/bin/node", "/usr/local/bin/node")


def find_node() -> str | None:
    found = shutil.which("node")
    if found:
        return found
    return next((p for p in NODE_CANDIDATES if os.access(p, os.X_OK)), None)


def parse_records(stdout: str) -> list[dict]:
    """Keeps well formed records; skips and logs anything else."""
    records = []
    for n, line in enumerate(stdout.splitlines(), 1):
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError as exc:
            log.warning(f"ts_sources: line {n} is not JSON ({exc})")
            continue
        if not isinstance(rec, dict) or not all(isinstance(rec.get(k), str) and rec[k] for k in REQUIRED):
            log.warning(f"ts_sources: line {n} missing required fields")
            continue
        if not rec["url"].startswith("http"):
            log.warning(f"ts_sources: line {n} has a non http url")
            continue
        records.append(rec)
    return records


def run_ts_ingest(cmd: list[str] | None = None, timeout: int = 180) -> list[dict]:
    if cmd is None:
        node = find_node()
        if node is None:
            log.warning("ts_sources: node not found, skipping TypeScript sources")
            return []
        if not ENTRY.exists():
            log.warning(f"ts_sources: {ENTRY} missing, run `npm run build` in ingest-ts")
            return []
        cmd = [node, str(ENTRY)]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=ROOT)
    except subprocess.TimeoutExpired:
        log.error(f"ts_sources: timed out after {timeout}s")
        return []
    except OSError as exc:
        log.error(f"ts_sources: could not start ({exc})")
        return []
    for line in proc.stderr.splitlines():
        if line.startswith("{"):
            log.info(f"ts_sources summary: {line}")
    if proc.returncode != 0:
        log.error(f"ts_sources: exited {proc.returncode}: {proc.stderr[-500:]}")
    return parse_records(proc.stdout)
