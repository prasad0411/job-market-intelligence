"""Test anomaly detection and SPC monitoring."""
import pytest
import sys, os
from datetime import datetime, timedelta
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analytics.anomaly import AnomalyDetector, AnomalyAlert, SourceStats
from analytics.store import AnalyticsStore
from analytics.models import JobRecord


class TestAnomalyDetector:
    """Test statistical anomaly detection."""

    @pytest.fixture
    def store_with_data(self, tmp_path):
        """Create a store with 14 days of synthetic source data."""
        db = str(tmp_path / "test_anomaly.db")
        store = AnalyticsStore(db_path=db)

        # Simulate 14 days of GoodSource: ~25% valid rate
        base = datetime.now() - timedelta(days=14)
        for day in range(14):
            dt = base + timedelta(days=day)
            date_str = dt.strftime("%Y-%m-%dT12:00:00")
            # 4 valid, 12 discarded per day (25% rate)
            for i in range(4):
                store.record_job(JobRecord(
                    url=f"https://ex.com/g/{day}/{i}",
                    company=f"Co{i}", title="Intern",
                    source="GoodSource", outcome="valid",
                    processed_at=date_str
                ))
            for i in range(12):
                store.record_job(JobRecord(
                    url=f"https://ex.com/g/{day}/d{i}",
                    company=f"CoD{i}", title="Intern",
                    source="GoodSource", outcome="discarded",
                    rejection_reason="Low salary",
                    processed_at=date_str
                ))

        # Simulate BadSource: drops to 0% on last day
        for day in range(14):
            dt = base + timedelta(days=day)
            date_str = dt.strftime("%Y-%m-%dT12:00:00")
            if day < 13:
                # Normal: 20% valid
                for i in range(2):
                    store.record_job(JobRecord(
                        url=f"https://ex.com/b/{day}/{i}",
                        company=f"CoB{i}", title="Intern",
                        source="BadSource", outcome="valid",
                        processed_at=date_str
                    ))
                for i in range(8):
                    store.record_job(JobRecord(
                        url=f"https://ex.com/b/{day}/d{i}",
                        company=f"CoBD{i}", title="Intern",
                        source="BadSource", outcome="discarded",
                        processed_at=date_str
                    ))
            else:
                # Last day: 0% valid (degradation)
                for i in range(10):
                    store.record_job(JobRecord(
                        url=f"https://ex.com/b/{day}/d{i}",
                        company=f"CoBD{i}", title="Intern",
                        source="BadSource", outcome="discarded",
                        processed_at=date_str
                    ))

        yield db
        store.close()

    def test_healthy_source_no_alerts(self, store_with_data):
        detector = AnomalyDetector(db_path=store_with_data)
        alerts = detector.check_source("GoodSource")
        # GoodSource is stable — should have no critical/warning alerts
        critical = [a for a in alerts if a.severity in ("critical", "warning")]
        assert len(critical) == 0
        detector.close()

    def test_degraded_source_detected(self, store_with_data):
        detector = AnomalyDetector(db_path=store_with_data)
        alerts = detector.check_source("BadSource")
        # BadSource dropped to 0% on last day — should flag degradation
        assert len(alerts) > 0
        assert any(a.alert_type in ("degradation", "anomaly") for a in alerts)
        detector.close()

    def test_check_all_sources(self, store_with_data):
        detector = AnomalyDetector(db_path=store_with_data)
        alerts = detector.check_all_sources()
        # was: assert isinstance(alerts, list) - true even when broken
        assert all(hasattr(a, "source") and hasattr(a, "severity") for a in alerts)
        detector.close()

    def test_spc_report(self, store_with_data):
        detector = AnomalyDetector(db_path=store_with_data)
        report = detector.spc_report()
        assert len(report) >= 2
        for r in report:
            assert "source" in r
            assert "mean_rate" in r
            assert "z_score" in r
            assert "status" in r
        detector.close()

    def test_trend_data(self, store_with_data):
        detector = AnomalyDetector(db_path=store_with_data)
        trend = detector.trend_data("GoodSource", days=14)
        assert len(trend) >= 10
        for t in trend:
            assert "date" in t
            assert "rate" in t
        detector.close()

    def test_source_stats_computation(self, store_with_data):
        detector = AnomalyDetector(db_path=store_with_data)
        stats = detector.compute_source_stats("GoodSource")
        assert stats is not None
        assert stats.mean > 0
        assert stats.std >= 0
        assert len(stats.daily_rates) >= 10
        assert stats.ucl >= stats.lcl  # equal when std=0 (perfectly consistent source)
        detector.close()

    def test_insufficient_data_returns_none(self, tmp_path):
        db = str(tmp_path / "empty.db")
        detector = AnomalyDetector(db_path=db)
        stats = detector.compute_source_stats("NonExistent")
        assert stats is None
        detector.close()


class TestAnomalyAlert:
    """Test alert formatting."""

    def test_str_representation(self):
        alert = AnomalyAlert(
            source="TestSource", alert_type="degradation",
            severity="warning", message="Rate dropped to 5%",
            current_rate=5.0, baseline_rate=25.0, z_score=-2.5
        )
        s = str(alert)
        assert "TestSource" in s
        assert "⚠️" in s

    def test_critical_icon(self):
        alert = AnomalyAlert(
            source="X", alert_type="outage", severity="critical",
            message="Down", current_rate=0, baseline_rate=20
        )
        assert "🚨" in str(alert)

