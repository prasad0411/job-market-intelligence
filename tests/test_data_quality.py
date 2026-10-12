"""Test data quality scoring and TF-IDF similarity."""
import pytest
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analytics.data_quality import DataQualityScorer, QualityScore
from analytics.similarity import TitleSimilarity, SimilarityMatch


class TestDataQuality:
    """Test quality scoring framework."""

    def test_perfect_job_scores_high(self):
        job = {
            "company": "Google", "title": "SDE Intern",
            "location": "Mountain View, CA", "url": "https://google.com/jobs/1",
            "job_id": "JR-12345", "salary_low": 45.0, "salary_high": 55.0,
            "sponsorship": "Yes", "remote": "Hybrid",
        }
        score = DataQualityScorer.score_job(job)
        assert score.total_score >= 90
        assert score.grade == "A"
        assert len(score.missing_fields) == 0

    def test_minimal_job_scores_low(self):
        job = {"company": "Unknown", "title": "Unknown", "url": ""}
        score = DataQualityScorer.score_job(job)
        assert score.total_score < 30
        assert score.grade in ("D", "F")

    def test_partial_job(self):
        job = {
            "company": "Acme", "title": "Intern",
            "url": "https://acme.com/jobs/1",
            "location": "Unknown", "job_id": "N/A",
        }
        score = DataQualityScorer.score_job(job)
        assert 30 < score.total_score < 80
        assert "location" in score.missing_fields
        assert "job_id" in score.missing_fields

    def test_batch_scoring(self):
        jobs = [
            {"company": "A", "title": "X", "url": "https://a.com", "location": "Boston, MA"},
            {"company": "B", "title": "Y", "url": "https://b.com"},
            {"company": "Unknown", "title": "Unknown", "url": ""},
        ]
        stats = DataQualityScorer.score_batch(jobs)
        assert stats["count"] == 3
        assert stats["avg_score"] > 0
        assert "grade_distribution" in stats
        assert "missing_field_frequency" in stats

    def test_empty_batch(self):
        stats = DataQualityScorer.score_batch([])
        assert stats["count"] == 0

    def test_quality_by_source(self, tmp_path):
        from analytics.store import AnalyticsStore
        from analytics.models import JobRecord
        store = AnalyticsStore(db_path=str(tmp_path / "q.db"))
        for i in range(10):
            store.record_job(JobRecord(
                url=f"https://x.com/{i}", company=f"Co{i}",
                title="Intern", source="GoodSource",
                location="Boston, MA" if i < 8 else "Unknown",
                job_id=f"JR-{i}" if i < 6 else "N/A",
            ))
        report = DataQualityScorer.quality_by_source(store)
        assert len(report) >= 1
        assert report[0]["source"] == "GoodSource"
        assert report[0]["location_pct"] == 80.0
        store.close()


class TestTitleSimilarity:
    """Test TF-IDF fuzzy dedup."""

    @pytest.fixture
    def engine(self):
        e = TitleSimilarity()
        e.add("Software Engineering Intern", job_id="1", company="Google")
        e.add("Machine Learning Research Intern", job_id="2", company="Meta")
        e.add("Data Science Intern", job_id="3", company="Amazon")
        e.add("Backend Engineer Intern", job_id="4", company="Stripe")
        e.add("Software Engineer - Intern", job_id="5", company="Apple")
        return e

    def test_exact_match(self, engine):
        matches = engine.find_similar("Software Engineering Intern", threshold=0.9)
        assert len(matches) >= 1
        assert matches[0].score >= 0.9

    def test_near_match(self, engine):
        matches = engine.find_similar("Software Engineer Intern", threshold=0.6)
        assert len(matches) >= 1
        # Should match "Software Engineering Intern" and "Software Engineer - Intern"

    def test_no_match(self, engine):
        matches = engine.find_similar("Accounting Manager", threshold=0.7)
        assert len(matches) == 0

    def test_ml_title_matches_ml(self, engine):
        matches = engine.find_similar("ML Research Intern", threshold=0.5)
        assert any("Machine Learning" in m.title for m in matches)

    def test_is_near_duplicate(self, engine):
        result = engine.is_near_duplicate("Software Engineering Intern", threshold=0.85)
        assert result is not None
        assert result.score >= 0.85

    def test_not_near_duplicate(self, engine):
        result = engine.is_near_duplicate("Marketing Coordinator", threshold=0.85)
        assert result is None

    def test_same_company_filter(self, engine):
        matches = engine.find_similar(
            "Software Engineering Intern",
            threshold=0.5, same_company="Google"
        )
        assert all(m.company == "Google" for m in matches)

    def test_add_batch(self):
        e = TitleSimilarity()
        e.add_batch([
            {"title": "SDE Intern", "company": "A"},
            {"title": "ML Intern", "company": "B"},
        ])
        assert e.size == 2

    def test_stats(self, engine):
        s = engine.stats()
        assert s["documents"] == 5
        assert s["vocabulary"] > 0

    def test_empty_engine(self):
        e = TitleSimilarity()
        matches = e.find_similar("anything")
        assert matches == []

    def test_tokenization(self):
        tokens = TitleSimilarity._tokenize("Software Engineering Intern - Summer 2026")
        assert "software" in tokens
        assert "engineering" in tokens
        assert "intern" in tokens
        # Stopwords removed
        assert "the" not in tokens

