"""Test Brain intelligence — pattern learning, contacts, source quality."""
import pytest
import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestBrainPatterns:
    """Test pattern learning and retrieval."""

    @pytest.fixture
    def brain(self, tmp_path):
        from outreach.brain import Brain
        Brain.reset()
        b = Brain.__new__(Brain)
        b._path = str(tmp_path / "brain.json")
        b._data = b._default()
        return b

    def test_record_pattern_success(self, brain):
        brain.record_pattern_success("example.com", "{first}.{last}", "john.doe@example.com")
        assert brain.best_pattern_for("example.com") == "{first}.{last}"

    def test_pattern_confidence_increases(self, brain):
        for _ in range(5):
            brain.record_pattern_success("test.com", "{first}.{last}", "a@test.com")
        entry = brain._data["domains"]["test.com"]
        assert entry["pattern_confidence"] >= 0.9

    def test_failed_pattern_recorded(self, brain):
        brain.record_pattern_failure("fail.com", "{first}.{last}")
        assert brain.is_failed_pattern("fail.com", "{first}.{last}")

    def test_unknown_domain_returns_none(self, brain):
        assert brain.best_pattern_for("nonexistent.com") is None


class TestBrainContacts:
    """Test verified contact storage and retrieval."""

    @pytest.fixture
    def brain(self, tmp_path):
        from outreach.brain import Brain
        Brain.reset()
        b = Brain.__new__(Brain)
        b._path = str(tmp_path / "brain.json")
        b._data = b._default()
        return b

    def test_store_and_retrieve_contact(self, brain):
        brain.store_verified_contact("Acme Corp", "hm", "John Doe", "john@acme.com", confidence=0.9)
        contact = brain.get_verified_contact("Acme Corp", "hm")
        assert contact is not None
        assert contact["email"] == "john@acme.com"
        assert contact["name"] == "John Doe"

    def test_unknown_contact_returns_none(self, brain):
        assert brain.get_verified_contact("Unknown Corp", "hm") is None

    def test_bounced_contact_skipped(self, brain):
        brain.store_verified_contact("Bounce Inc", "hm", "Jane", "jane@bounce.com")
        brain.mark_contact_bounced("Bounce Inc", "hm", "jane@bounce.com")
        brain.mark_contact_bounced("Bounce Inc", "hm", "jane@bounce.com")
        contact = brain.get_verified_contact("Bounce Inc", "hm")
        assert contact is None  # bounced 2x = skipped


class TestBrainSourceQuality:
    """Test source quality tracking."""

    @pytest.fixture
    def brain(self, tmp_path):
        from outreach.brain import Brain
        Brain.reset()
        b = Brain.__new__(Brain)
        b._path = str(tmp_path / "brain.json")
        b._data = b._default()
        return b

    def test_record_source_quality(self, brain):
        brain.record_source_quality("TestSource", valid=10, rejected=90)
        report = brain.get_source_quality_report()
        assert "TestSource" in report
