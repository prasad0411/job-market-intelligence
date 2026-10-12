"""
Write-Ahead Log (WAL) for Google Sheets mutations.

Guarantees crash-safe writes: if a sheet write fails mid-way,
the next run detects the uncommitted entry and replays it.

Inspired by database transaction log architecture.

Usage:
    wal = WriteAheadLog()
    tx = wal.begin("add_valid_jobs", {"jobs": [...], "start_row": 42})
    try:
        sheets.add_valid_jobs(...)  # actual sheet write
        wal.commit(tx)
    except Exception:
        wal.rollback(tx)
    
    # On next startup:
    wal.replay_pending()  # replays any uncommitted transactions
"""
import os
import json
import time
import logging
import hashlib
from datetime import datetime
from typing import Optional, List, Dict
from dataclasses import dataclass, field, asdict

log = logging.getLogger(__name__)

WAL_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    ".local", "wal"
)


@dataclass
class Transaction:
    """Single WAL transaction."""
    tx_id: str
    operation: str              # add_valid_jobs | add_discarded_jobs | update_cell | batch_update
    payload: Dict               # operation-specific data
    status: str = "pending"     # pending | committed | rolled_back | replayed
    created_at: str = ""
    committed_at: str = ""
    retries: int = 0
    max_retries: int = 3
    error: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Transaction":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


class WriteAheadLog:
    """
    Write-Ahead Log for idempotent sheet mutations.
    
    Flow:
    1. begin() — writes transaction to pending/ directory
    2. (caller performs actual sheet write)
    3. commit() — moves to committed/ directory
    4. On failure: rollback() or leave as pending
    5. On next startup: replay_pending() retries uncommitted transactions
    """

    def __init__(self, wal_dir: str = None):
        self.wal_dir = wal_dir or WAL_DIR
        self.pending_dir = os.path.join(self.wal_dir, "pending")
        self.committed_dir = os.path.join(self.wal_dir, "committed")
        self.failed_dir = os.path.join(self.wal_dir, "failed")
        os.makedirs(self.pending_dir, exist_ok=True)
        os.makedirs(self.committed_dir, exist_ok=True)
        os.makedirs(self.failed_dir, exist_ok=True)

    def begin(self, operation: str, payload: Dict) -> Transaction:
        """Create a new pending transaction."""
        tx_id = self._generate_tx_id(operation)
        tx = Transaction(
            tx_id=tx_id,
            operation=operation,
            payload=payload,
            status="pending",
            created_at=datetime.now().isoformat(),
        )
        self._write_tx(tx, self.pending_dir)
        log.debug(f"WAL: begin {tx_id} ({operation})")
        return tx

    def commit(self, tx: Transaction):
        """Mark transaction as committed — move from pending to committed."""
        tx.status = "committed"
        tx.committed_at = datetime.now().isoformat()

        # Remove from pending
        pending_path = os.path.join(self.pending_dir, f"{tx.tx_id}.json")
        if os.path.exists(pending_path):
            os.remove(pending_path)

        # Write to committed
        self._write_tx(tx, self.committed_dir)
        log.debug(f"WAL: commit {tx.tx_id}")

    def rollback(self, tx: Transaction, error: str = ""):
        """Mark transaction as rolled back."""
        tx.status = "rolled_back"
        tx.error = error

        pending_path = os.path.join(self.pending_dir, f"{tx.tx_id}.json")
        if os.path.exists(pending_path):
            os.remove(pending_path)

        self._write_tx(tx, self.failed_dir)
        log.warning(f"WAL: rollback {tx.tx_id}: {error}")

    def get_pending(self) -> List[Transaction]:
        """Get all uncommitted transactions, oldest first."""
        pending = []
        if not os.path.exists(self.pending_dir):
            return pending

        for fname in sorted(os.listdir(self.pending_dir)):
            if not fname.endswith(".json"):
                continue
            try:
                path = os.path.join(self.pending_dir, fname)
                data = json.load(open(path))
                tx = Transaction.from_dict(data)
                pending.append(tx)
            except Exception as e:
                log.warning(f"WAL: failed to load {fname}: {e}")

        return pending

    def replay_pending(self, executor=None) -> Dict:
        """
        Replay all pending transactions.
        
        Args:
            executor: callable(tx) -> bool that performs the actual write.
                     Returns True if successful, False otherwise.
        
        Returns:
            dict with counts: {"replayed": N, "failed": N, "skipped": N}
        """
        pending = self.get_pending()
        if not pending:
            return {"replayed": 0, "failed": 0, "skipped": 0}

        log.info(f"WAL: found {len(pending)} pending transactions to replay")
        stats = {"replayed": 0, "failed": 0, "skipped": 0}

        for tx in pending:
            tx.retries += 1

            if tx.retries > tx.max_retries:
                log.warning(f"WAL: {tx.tx_id} exceeded max retries ({tx.max_retries})")
                self.rollback(tx, error=f"Exceeded {tx.max_retries} retries")
                stats["failed"] += 1
                continue

            if executor:
                try:
                    success = executor(tx)
                    if success:
                        self.commit(tx)
                        stats["replayed"] += 1
                        log.info(f"WAL: replayed {tx.tx_id} successfully")
                    else:
                        # Update retry count
                        self._write_tx(tx, self.pending_dir)
                        stats["failed"] += 1
                except Exception as e:
                    log.error(f"WAL: replay failed for {tx.tx_id}: {e}")
                    self._write_tx(tx, self.pending_dir)
                    stats["failed"] += 1
            else:
                stats["skipped"] += 1
                log.debug(f"WAL: no executor provided, skipping {tx.tx_id}")

        return stats

    def cleanup_committed(self, max_age_days: int = 7):
        """Remove committed transactions older than max_age_days."""
        if not os.path.exists(self.committed_dir):
            return 0

        cutoff = time.time() - (max_age_days * 86400)
        removed = 0

        for fname in os.listdir(self.committed_dir):
            if not fname.endswith(".json"):
                continue
            path = os.path.join(self.committed_dir, fname)
            try:
                if os.path.getmtime(path) < cutoff:
                    os.remove(path)
                    removed += 1
            except Exception:
                pass

        if removed:
            log.info(f"WAL: cleaned up {removed} old committed transactions")
        return removed

    @property
    def stats(self) -> Dict:
        """Current WAL state."""
        def count_dir(d):
            if not os.path.exists(d):
                return 0
            return len([f for f in os.listdir(d) if f.endswith(".json")])

        return {
            "pending": count_dir(self.pending_dir),
            "committed": count_dir(self.committed_dir),
            "failed": count_dir(self.failed_dir),
        }

    # ── Internal ──────────────────────────────────────────────────────────

    def _generate_tx_id(self, operation: str) -> str:
        """Generate unique transaction ID."""
        ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        h = hashlib.md5(f"{operation}_{ts}_{os.getpid()}".encode()).hexdigest()[:8]
        return f"tx_{ts}_{h}"

    def _write_tx(self, tx: Transaction, directory: str):
        """Atomically write transaction to directory."""
        path = os.path.join(directory, f"{tx.tx_id}.json")
        tmp_path = path + ".tmp"
        try:
            with open(tmp_path, "w") as f:
                json.dump(tx.to_dict(), f, indent=2)
            os.replace(tmp_path, path)  # atomic on POSIX
        except Exception as e:
            log.error(f"WAL: failed to write {path}: {e}")
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

