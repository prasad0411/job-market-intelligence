"""Test analytics store — schema, writes, queries."""
import pytest
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analytics.store import AnalyticsStore
from analytics.models import JobRecord, RunRecord, SourceMetric
from analytics.queries import AnalyticsQueries


class TestAnalyticsStore:
    """Test core store operations."""

    @pytest.fixture
    def store(self, tmp_path):
        db = str(tmp_path / "test_analytics.db")
        s = AnalyticsStore(db_path=db)
        yield s
        s.close()

    def test_empty_db(self, store):
        assert store.total_jobs() == 0

    def test_record_single_job(self, store):
        job = JobRecord(
            url="https://example.com/job/1",
            company="Acme Corp",
            title="Software Intern",
            location="Boston, MA",
            source="SimplifyJobs",
            outcome="valid",
        )
        store.record_job(job)
        assert store.total_jobs() == 1
        assert store.total_jobs("valid") == 1

    def test_record_batch(self, store):
        jobs = [
            JobRecord(url=f"https://example.com/{i}", company=f"Co{i}",
                      title="Intern", source="Test", outcome="valid")
            for i in range(10)
        ]
        store.record_jobs_batch(jobs)
        assert store.total_jobs() == 10

    def test_source_quality_report(self, store):
        for i in range(5):
            store.record_job(JobRecord(
                url=f"https://ex.com/{i}", company="A",
                title="Intern", source="GoodSource", outcome="valid"
            ))
        for i in range(15):
            store.record_job(JobRecord(
                url=f"https://ex.com/d{i}", company="B",
                title="Intern", source="BadSource", outcome="discarded",
                rejection_reason="Low salary"
            ))
        report = store.source_quality_report()
        assert len(report) == 2
        good = next(r for r in report if r["source"] == "GoodSource")
        assert good["valid_pct"] == 100.0

    def test_rejection_funnel(self, store):
        for reason in ["Low salary", "Low salary", "International", "Clearance"]:
            store.record_job(JobRecord(
                url=f"https://ex.com/{reason}", company="X",
                title="Intern", source="T", outcome="discarded",
                rejection_reason=reason
            ))
        funnel = store.rejection_funnel(days=30)
        assert len(funnel) >= 2
        assert funnel[0]["rejection_reason"] == "Low salary"

    def test_location_distribution(self, store):
        for loc in ["Boston, MA", "Boston, MA", "NYC, NY", "Austin, TX"]:
            store.record_job(JobRecord(
                url=f"https://ex.com/{loc}", company="X",
                title="Intern", location=loc, source="T", outcome="valid"
            ))
        dist = store.location_distribution()
        assert len(dist) >= 2

    def test_feature_vector(self, store):
        store.record_job(JobRecord(
            url="https://ex.com/1", company="Google",
            title="ML Intern", location="MTV, CA",
            source="SimplifyJobs", outcome="valid"
        ))
        fv = store.feature_vector("Google", "ML Intern", "SimplifyJobs", "MTV, CA")
        assert fv["company_times_seen"] == 1
        assert fv["has_ai_ml"] == 1

    def test_daily_trend(self, store):
        store.record_job(JobRecord(
            url="https://ex.com/1", company="A",
            title="Intern", source="T", outcome="valid"
        ))
        trend = store.daily_trend(days=1)
        assert len(trend) >= 1

    def test_extract_state(self, store):
        assert store._extract_state("Boston, MA") == "MA"
        assert store._extract_state("Remote") == ""
        assert store._extract_state("") == ""


class TestAnalyticsQueries:
    """Test high-level query interface."""

    @pytest.fixture
    def queries(self, tmp_path):
        db = str(tmp_path / "test_q.db")
        q = AnalyticsQueries(db_path=db)
        yield q
        q.close()

    def test_empty_summary(self, queries):
        s = queries.summary()
        assert s["total_processed"] == 0

    def test_source_report_text(self, queries):
        txt = queries.source_report_text()
        # was: assert isinstance(txt, str) - passes for any string, incl. ""
        assert txt and ("Source" in txt or "No source data" in txt), txt[:80]

    def test_rejection_report_text(self, queries):
        txt = queries.rejection_report_text()
        assert txt and ("Rejection" in txt or "No rejections" in txt), txt[:80]


class TestModels:
    """Test data model construction."""

    def test_job_record_defaults(self):
        j = JobRecord(url="https://x.com", company="A", title="B")
        assert j.outcome == "valid"
        assert j.resume_type == "SDE"
        assert j.processed_at != ""

    def test_to_dict(self):
        j = JobRecord(url="https://x.com", company="A", title="B")
        d = j.to_dict()
        assert isinstance(d, dict)
        assert d["company"] == "A"

    def test_run_record(self):
        r = RunRecord(run_id="run_001", started_at="2026-04-26T08:00:00")
        assert r.valid_count == 0

    def test_source_metric(self):
        m = SourceMetric(source="Test", date="2026-04-26", fetched=100, valid=20)
        assert m.valid_rate == 0.0  # not auto-calculated

