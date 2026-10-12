"""
Atomic JSON writes.

json.dump(data, open(path, "w")) truncates the target BEFORE writing. A crash
or a second process mid-write leaves a half-written or interleaved file — this
is what corrupted correction_log.json. Writing to a temp file and renaming is
atomic on POSIX, so the file is either the old version or the new one, never
a mixture.
"""
import json
import os
import tempfile


def write_json(path, data, indent=2):
    """Atomically replace `path` with `data`. Never raises into the caller."""
    try:
        d = os.path.dirname(os.path.abspath(path)) or "."
        os.makedirs(d, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=indent, ensure_ascii=False)
            os.replace(tmp, path)
            return True
        except Exception:
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except Exception:
                    pass
            raise
    except Exception:
        return False


def read_json(path, default=None):
    """Read JSON, returning `default` on any failure."""
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default if default is not None else {}
