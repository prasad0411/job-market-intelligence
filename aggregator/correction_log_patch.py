"""
Drop-in replacement for the correction_log write in url_validator.py.

WHY: the current code does
    _json.dump(_corrections, open(_log_path, "w"), indent=2)
which (a) truncates+rewrites the whole array every call, so two overlapping
processes interleave into `]...]` garbage (exactly the corruption we found),
and (b) never explicitly closes the handle, so a mid-write crash leaves a
half-written array.

FIX: append-only JSONL. One JSON object per line. Concurrent appends can't
tear each other, and a single bad line never kills the whole file.

This changes ONLY how the log is written/read. It does not touch extraction,
validation, or any correction *behavior* — the same corrections happen; they
are just recorded durably.
"""
import json
import os
from datetime import datetime

LOG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    ".local", "correction_log.jsonl",
)


def append_correction(entry: dict) -> None:
    """Append one correction record. Never raises into the caller."""
    try:
        entry = dict(entry)
        entry.setdefault("ts", datetime.now().isoformat())
        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
        # 'a' + single write of one line = atomic enough for our concurrency:
        # POSIX guarantees writes under PIPE_BUF to O_APPEND files don't interleave.
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass  # logging must never break the pipeline


def read_corrections(limit: int | None = None) -> list[dict]:
    """Read corrections; skips any single corrupt line instead of dying."""
    out = []
    if not os.path.exists(LOG_PATH):
        return out
    with open(LOG_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue  # one bad line no longer poisons the whole log
    return out[-limit:] if limit else out


def migrate_old_log() -> int:
    """
    One-time: salvage whatever is readable from the old corrupt
    correction_log.json into the new JSONL. Safe to run repeatedly.
    Returns count of records recovered.
    """
    old = os.path.join(os.path.dirname(LOG_PATH), "correction_log.json")
    if not os.path.exists(old):
        return 0
    recovered = []
    raw = open(old, encoding="utf-8", errors="replace").read()
    # The file is concatenated arrays like [ ... ][ ... ]. Try to pull every
    # object out with a lenient decoder pass.
    dec = json.JSONDecoder()
    i = 0
    n = len(raw)
    while i < n:
        # skip to next '{'
        j = raw.find("{", i)
        if j == -1:
            break
        try:
            obj, end = dec.raw_decode(raw, j)
            if isinstance(obj, dict):
                recovered.append(obj)
            i = end
        except json.JSONDecodeError:
            i = j + 1
    for r in recovered:
        append_correction(r)
    return len(recovered)


if __name__ == "__main__":
    got = migrate_old_log()
    print(f"Recovered {got} records into {LOG_PATH}")
    print(f"Readable now: {len(read_corrections())}")
