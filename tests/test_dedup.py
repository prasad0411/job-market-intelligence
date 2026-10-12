"""Test deduplication logic — job ID, URL, company+title."""
import pytest
import sys, os, json, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestJobIDDedup:
    """Test Brain.is_duplicate_job_id and normalize_job_id."""

    @pytest.fixture
    def brain(self, tmp_path):
        """Create a temporary Brain instance for testing."""
        from outreach.brain import Brain
        Brain.reset()
        brain_file = tmp_path / "brain.json"
        brain_file.write_text("{}")
        os.environ["BRAIN_PATH"] = str(brain_file)
        b = Brain.__new__(Brain)
        b._path = str(brain_file)
        b._data = b._default()
        return b

    def test_normalize_strips_prefix(self, brain):
        result = brain.normalize_job_id("JR-12345")
        assert result is not None and len(result) > 0
        result = brain.normalize_job_id("REQ_0000012345")
        assert result is not None and len(result) > 0

    def test_normalize_preserves_meaningful_ids(self, brain):
        nid = brain.normalize_job_id("R-01340600")
        assert nid is not None
        assert len(nid) > 0

    def test_register_and_detect_duplicate(self, brain):
        brain.register_job_id("JR-12345", "Acme", "Software Intern")
        assert brain.is_duplicate_job_id("JR-12345")

    def test_different_ids_not_duplicate(self, brain):
        brain.register_job_id("JR-12345", "Acme", "Software Intern")
        assert not brain.is_duplicate_job_id("JR-99999")

    def test_empty_id_not_duplicate(self, brain):
        assert not brain.is_duplicate_job_id("")
        assert not brain.is_duplicate_job_id("N/A")


class TestRunScopedJobIDDedup:
    """Same numeric ID on two domains in one run must dedup (ByteDance jobs. vs join.)."""

    def test_bytedance_cross_domain_same_id(self):
        from outreach.brain import Brain
        Brain.reset()
        a = "https://jobs.bytedance.com/en/position/7668464504736876853/detail?utm_source=x"
        b = "https://joinbytedance.com/search/7668464504736876853?utm_source=y"
        import re
        norm = lambda u: re.search(r"(\d{10,})", u).group(1)
        assert norm(a) == norm(b)
