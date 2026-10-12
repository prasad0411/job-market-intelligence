"""Test Write-Ahead Log — transactions, commit, rollback, replay."""
import pytest
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from aggregator.wal import WriteAheadLog, Transaction


class TestWALBasics:
    """Test core WAL operations."""

    @pytest.fixture
    def wal(self, tmp_path):
        return WriteAheadLog(wal_dir=str(tmp_path / "wal"))

    def test_begin_creates_pending(self, wal):
        tx = wal.begin("test_op", {"key": "value"})
        assert tx.status == "pending"
        assert tx.tx_id.startswith("tx_")
        assert wal.stats["pending"] == 1

    def test_commit_moves_to_committed(self, wal):
        tx = wal.begin("test_op", {"key": "value"})
        wal.commit(tx)
        assert wal.stats["pending"] == 0
        assert wal.stats["committed"] == 1

    def test_rollback_moves_to_failed(self, wal):
        tx = wal.begin("test_op", {"key": "value"})
        wal.rollback(tx, error="test error")
        assert wal.stats["pending"] == 0
        assert wal.stats["failed"] == 1

    def test_multiple_transactions(self, wal):
        tx1 = wal.begin("op1", {"a": 1})
        tx2 = wal.begin("op2", {"b": 2})
        tx3 = wal.begin("op3", {"c": 3})
        assert wal.stats["pending"] == 3
        wal.commit(tx1)
        wal.commit(tx2)
        assert wal.stats["pending"] == 1
        assert wal.stats["committed"] == 2

    def test_get_pending_returns_oldest_first(self, wal):
        import time
        tx1 = wal.begin("op1", {"order": 1})
        time.sleep(0.01)
        tx2 = wal.begin("op2", {"order": 2})
        pending = wal.get_pending()
        assert len(pending) == 2
        assert pending[0].tx_id == tx1.tx_id

    def test_empty_wal(self, wal):
        assert wal.stats == {"pending": 0, "committed": 0, "failed": 0}
        assert wal.get_pending() == []


class TestWALReplay:
    """Test transaction replay on startup."""

    @pytest.fixture
    def wal(self, tmp_path):
        return WriteAheadLog(wal_dir=str(tmp_path / "wal"))

    def test_replay_with_executor(self, wal):
        tx = wal.begin("test_op", {"data": "replay_me"})
        # Simulate crash — tx is pending
        result = wal.replay_pending(executor=lambda t: True)
        assert result["replayed"] == 1
        assert wal.stats["pending"] == 0
        assert wal.stats["committed"] == 1

    def test_replay_failed_executor(self, wal):
        tx = wal.begin("test_op", {"data": "fail_me"})
        result = wal.replay_pending(executor=lambda t: False)
        assert result["failed"] == 1
        assert wal.stats["pending"] == 1  # still pending for retry

    def test_replay_exceeds_max_retries(self, wal):
        tx = wal.begin("test_op", {"data": "exhaust_me"})
        # Manually set retries to max
        tx.retries = 3
        tx.max_retries = 3
        # Re-write with high retry count
        wal._write_tx(tx, wal.pending_dir)
        result = wal.replay_pending(executor=lambda t: False)
        assert result["failed"] == 1
        assert wal.stats["pending"] == 0
        assert wal.stats["failed"] == 1

    def test_replay_no_executor_skips(self, wal):
        tx = wal.begin("test_op", {"data": "skip_me"})
        result = wal.replay_pending(executor=None)
        assert result["skipped"] == 1

    def test_replay_exception_in_executor(self, wal):
        tx = wal.begin("test_op", {"data": "crash_me"})
        def bad_executor(t):
            raise RuntimeError("boom")
        result = wal.replay_pending(executor=bad_executor)
        assert result["failed"] == 1
        assert wal.stats["pending"] == 1  # still pending


class TestWALCleanup:
    """Test committed transaction cleanup."""

    @pytest.fixture
    def wal(self, tmp_path):
        return WriteAheadLog(wal_dir=str(tmp_path / "wal"))

    def test_cleanup_old_committed(self, wal):
        tx = wal.begin("old_op", {"old": True})
        wal.commit(tx)
        assert wal.stats["committed"] == 1
        # Set file mtime to 10 days ago
        path = os.path.join(wal.committed_dir, f"{tx.tx_id}.json")
        old_time = os.path.getmtime(path) - (10 * 86400)
        os.utime(path, (old_time, old_time))
        removed = wal.cleanup_committed(max_age_days=7)
        assert removed == 1
        assert wal.stats["committed"] == 0

    def test_cleanup_keeps_recent(self, wal):
        tx = wal.begin("new_op", {"new": True})
        wal.commit(tx)
        removed = wal.cleanup_committed(max_age_days=7)
        assert removed == 0
        assert wal.stats["committed"] == 1


class TestTransaction:
    """Test Transaction serialization."""

    def test_to_dict_and_back(self):
        tx = Transaction(
            tx_id="tx_test_123",
            operation="add_valid_jobs",
            payload={"jobs": [{"company": "Acme"}]},
            status="pending",
            created_at="2026-04-27T10:00:00",
        )
        d = tx.to_dict()
        assert d["tx_id"] == "tx_test_123"
        assert d["payload"]["jobs"][0]["company"] == "Acme"

        tx2 = Transaction.from_dict(d)
        assert tx2.tx_id == tx.tx_id
        assert tx2.operation == tx.operation
        assert tx2.payload == tx.payload

    def test_default_values(self):
        tx = Transaction(tx_id="x", operation="op", payload={})
        assert tx.status == "pending"
        assert tx.retries == 0
        assert tx.max_retries == 3

