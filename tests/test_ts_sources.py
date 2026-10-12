"""The Python side of the TypeScript ingestion bridge, run against fake services."""
import json
import sys

from aggregator import ts_sources

GOOD = {"company": "Acme", "title": "Software Engineer Intern", "location": "Boston, MA",
        "url": "https://www.themuse.com/jobs/acme/1", "job_id": "1", "source": "themuse_ts",
        "published_at": "2026-10-05T14:00:00Z"}


def fake(stdout_lines, code=0, stderr='{"summary": [], "total": 0}'):
    body = "\n".join(stdout_lines)
    return [sys.executable, "-c",
            f"import sys; sys.stdout.write({body!r}); sys.stderr.write({stderr!r}); sys.exit({code})"]


def test_reads_good_records():
    assert ts_sources.run_ts_ingest(fake([json.dumps(GOOD)])) == [GOOD]


def test_skips_bad_lines_but_keeps_good_ones():
    lines = ["not json", json.dumps({"title": "missing fields"}), json.dumps({**GOOD, "url": "javascript:x"}),
             "", json.dumps(GOOD)]
    assert ts_sources.run_ts_ingest(fake(lines)) == [GOOD]


def test_nonzero_exit_still_returns_parsed_records():
    assert ts_sources.run_ts_ingest(fake([json.dumps(GOOD)], code=1)) == [GOOD]


def test_timeout_fails_open():
    slow = [sys.executable, "-c", "import time; time.sleep(5)"]
    assert ts_sources.run_ts_ingest(slow, timeout=1) == []


def test_missing_binary_fails_open():
    assert ts_sources.run_ts_ingest(["/nonexistent/node-binary"]) == []


def test_missing_build_fails_open(monkeypatch, tmp_path):
    monkeypatch.setattr(ts_sources, "ENTRY", tmp_path / "missing.js")
    monkeypatch.setattr(ts_sources, "find_node", lambda: sys.executable)
    assert ts_sources.run_ts_ingest() == []
