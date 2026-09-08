"""
Statistical Anomaly Detection for source quality monitoring.

Uses rolling Z-scores and Statistical Process Control (SPC) bounds
to detect source degradation, outages, and recovery.

Never throttles or reduces volume — alert-only.

Usage:
    detector = AnomalyDetector()
    alerts = detector.check_all_sources()
    for alert in alerts:
        print(f"{alert['source']}: {alert['message']}")
"""
import logging
import math
from datetime import datetime, timedelta
from typing import List, Dict, Optional
from dataclasses import dataclass, field

from analytics.store import AnalyticsStore

log = logging.getLogger(__name__)


@dataclass
class AnomalyAlert:
    """Single anomaly detection result."""
    source: str
    alert_type: str          # degradation | outage | recovery | anomaly
    severity: str            # info | warning | critical
    message: str
    current_rate: float
    baseline_rate: float
    z_score: float = 0.0
    details: Dict = field(default_factory=dict)

    def __str__(self):
        icon = {"info": "ℹ️", "warning": "⚠️", "critical": "🚨"}.get(self.severity, "")
        return f"{icon} {self.source}: {self.message}"


@dataclass
class SourceStats:
    """Rolling statistics for a single source."""
    source: str
    window_days: int
    daily_rates: List[float] = field(default_factory=list)
    daily_volumes: List[int] = field(default_factory=list)
    mean: float = 0.0
    std: float = 0.0
    current_rate: float = 0.0
    current_volume: int = 0
    z_score: float = 0.0
    # SPC bounds
    ucl: float = 0.0     # upper control limit
    lcl: float = 0.0     # lower control limit
    center: float = 0.0  # center line


class AnomalyDetector:
    """
    Monitors source quality using statistical methods.
    
    Methods:
    - Rolling Z-score: detects sudden drops relative to historical baseline
    - SPC bounds: UCL/LCL at ±2σ from mean, flags out-of-control points
    - Volume tracking: detects source outages (zero volume)
    """

    def __init__(self, db_path: str = None, window_days: int = 14,
                 lookback_days: int = 30):
        self.store = AnalyticsStore(db_path=db_path)
        self.window_days = window_days
        self.lookback_days = lookback_days

    def compute_source_stats(self, source: str) -> Optional[SourceStats]:
        """Compute rolling statistics for a single source."""
        cutoff = (datetime.now() - timedelta(days=self.lookback_days)).strftime("%Y-%m-%d")

        rows = self.store.conn.execute("""
            SELECT DATE(processed_at) as date,
                   COUNT(*) as total,
                   SUM(CASE WHEN outcome = 'valid' THEN 1 ELSE 0 END) as valid
            FROM jobs
            WHERE source = ? AND processed_at >= ?
            GROUP BY DATE(processed_at)
            ORDER BY date
        """, (source, cutoff)).fetchall()

        if len(rows) < 3:
            return None  # not enough data for statistics

        stats = SourceStats(source=source, window_days=self.window_days)

        for row in rows:
            total = row["total"]
            valid = row["valid"]
            rate = (valid / total * 100) if total > 0 else 0.0
            stats.daily_rates.append(rate)
            stats.daily_volumes.append(total)

        # Compute rolling mean and std
        n = len(stats.daily_rates)
        stats.mean = sum(stats.daily_rates) / n
        variance = sum((r - stats.mean) ** 2 for r in stats.daily_rates) / max(n - 1, 1)
        stats.std = math.sqrt(variance)

        # Current = last day
        stats.current_rate = stats.daily_rates[-1]
        stats.current_volume = stats.daily_volumes[-1]

        # Z-score for current day
        if stats.std > 0:
            stats.z_score = (stats.current_rate - stats.mean) / stats.std
        else:
            stats.z_score = 0.0

        # SPC bounds (2-sigma)
        stats.ucl = stats.mean + 2 * stats.std
        stats.lcl = max(0, stats.mean - 2 * stats.std)
        stats.center = stats.mean

        return stats

    def check_source(self, source: str) -> List[AnomalyAlert]:
        """Check a single source for anomalies."""
        alerts = []
        stats = self.compute_source_stats(source)

        if stats is None:
            return alerts

        # Check 1: Source outage (zero volume for last day)
        if stats.current_volume == 0:
            alerts.append(AnomalyAlert(
                source=source,
                alert_type="outage",
                severity="critical",
                message=f"No jobs processed today (baseline: {stats.mean:.0f}% valid rate)",
                current_rate=0,
                baseline_rate=stats.mean,
                details={"last_volume": stats.daily_volumes[-2] if len(stats.daily_volumes) > 1 else 0}
            ))

        # Check 2: Quality degradation (Z-score < -2)
        elif stats.z_score < -2.0:
            alerts.append(AnomalyAlert(
                source=source,
                alert_type="degradation",
                severity="warning",
                message=(f"Valid rate dropped to {stats.current_rate:.1f}% "
                         f"(baseline: {stats.mean:.1f}%, Z={stats.z_score:.1f})"),
                current_rate=stats.current_rate,
                baseline_rate=stats.mean,
                z_score=stats.z_score,
                details={"std": stats.std, "ucl": stats.ucl, "lcl": stats.lcl}
            ))

        # Check 3: Below lower control limit (SPC)
        elif stats.current_rate < stats.lcl and stats.std > 0:
            alerts.append(AnomalyAlert(
                source=source,
                alert_type="anomaly",
                severity="info",
                message=(f"Valid rate {stats.current_rate:.1f}% below LCL "
                         f"({stats.lcl:.1f}%) — may be normal variation"),
                current_rate=stats.current_rate,
                baseline_rate=stats.mean,
                z_score=stats.z_score,
                details={"lcl": stats.lcl, "ucl": stats.ucl}
            ))

        # Check 4: Quality recovery (was degraded, now above mean)
        if len(stats.daily_rates) >= 3:
            prev_rate = stats.daily_rates[-2]
            prev_prev_rate = stats.daily_rates[-3]
            if (prev_rate < stats.lcl and prev_prev_rate < stats.lcl
                    and stats.current_rate >= stats.mean):
                alerts.append(AnomalyAlert(
                    source=source,
                    alert_type="recovery",
                    severity="info",
                    message=(f"Valid rate recovered to {stats.current_rate:.1f}% "
                             f"from {prev_rate:.1f}% (baseline: {stats.mean:.1f}%)"),
                    current_rate=stats.current_rate,
                    baseline_rate=stats.mean,
                    z_score=stats.z_score,
                ))

        return alerts

    def check_all_sources(self) -> List[AnomalyAlert]:
        """Check all known sources for anomalies."""
        sources = self.store.conn.execute("""
            SELECT DISTINCT source FROM jobs 
            WHERE source NOT IN ('Unknown', '') 
            AND processed_at >= ?
        """, ((datetime.now() - timedelta(days=self.lookback_days)).strftime("%Y-%m-%d"),)).fetchall()

        all_alerts = []
        for row in sources:
            source = row["source"]
            alerts = self.check_source(source)
            all_alerts.extend(alerts)

        # Sort by severity
        severity_order = {"critical": 0, "warning": 1, "info": 2}
        all_alerts.sort(key=lambda a: severity_order.get(a.severity, 3))

        return all_alerts

    def spc_report(self) -> List[Dict]:
        """Generate SPC report for all sources — for dashboard/digest."""
        sources = self.store.conn.execute("""
            SELECT DISTINCT source FROM jobs 
            WHERE source NOT IN ('Unknown', '')
            AND processed_at >= ?
        """, ((datetime.now() - timedelta(days=self.lookback_days)).strftime("%Y-%m-%d"),)).fetchall()

        report = []
        for row in sources:
            stats = self.compute_source_stats(row["source"])
            if stats and len(stats.daily_rates) >= 3:
                report.append({
                    "source": stats.source,
                    "mean_rate": round(stats.mean, 1),
                    "std": round(stats.std, 1),
                    "current_rate": round(stats.current_rate, 1),
                    "z_score": round(stats.z_score, 2),
                    "ucl": round(stats.ucl, 1),
                    "lcl": round(stats.lcl, 1),
                    "days_tracked": len(stats.daily_rates),
                    "total_volume": sum(stats.daily_volumes),
                    "status": (
                        "critical" if stats.z_score < -2.5 else
                        "warning" if stats.z_score < -2.0 else
                        "degraded" if stats.current_rate < stats.lcl else
                        "healthy"
                    ),
                })

        report.sort(key=lambda r: r["mean_rate"], reverse=True)
        return report

    def trend_data(self, source: str, days: int = 14) -> List[Dict]:
        """Daily valid rate for a single source — for charting."""
        cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        rows = self.store.conn.execute("""
            SELECT DATE(processed_at) as date,
                   COUNT(*) as total,
                   SUM(CASE WHEN outcome = 'valid' THEN 1 ELSE 0 END) as valid,
                   ROUND(100.0 * SUM(CASE WHEN outcome = 'valid' THEN 1 ELSE 0 END) / COUNT(*), 1) as rate
            FROM jobs
            WHERE source = ? AND processed_at >= ?
            GROUP BY DATE(processed_at)
            ORDER BY date
        """, (source, cutoff)).fetchall()
        return [dict(r) for r in rows]

    def close(self):
        self.store.close()

