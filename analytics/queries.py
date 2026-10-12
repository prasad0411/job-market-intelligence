"""
Pre-built analytics queries for the nightly digest and dashboard.

Usage:
    from analytics.queries import AnalyticsQueries
    aq = AnalyticsQueries()
    print(aq.summary())
"""
import os
from analytics.store import AnalyticsStore


class AnalyticsQueries:
    """High-level analytics interface."""

    def __init__(self, db_path: str = None):
        self.store = AnalyticsStore(db_path=db_path)

    def summary(self) -> dict:
        """Full pipeline summary for digest/dashboard."""
        return {
            "total_processed": self.store.total_jobs(),
            "total_valid": self.store.total_jobs("valid"),
            "total_discarded": self.store.total_jobs("discarded"),
            "total_reviewed": self.store.total_jobs("reviewed"),
            "source_quality": self.store.source_quality_report(days=30),
            "rejection_funnel": self.store.rejection_funnel(days=7),
            "location_distribution": self.store.location_distribution(),
            "resume_types": self.store.resume_type_distribution(),
            "daily_trend": self.store.daily_trend(days=14),
            "latency": self.store.processing_latency(days=7),
        }

    def source_report_text(self) -> str:
        """Text report for nightly digest email."""
        sources = self.store.source_quality_report(days=30)
        if not sources:
            return "No source data available."
        lines = ["Source Quality (30d):"]
        for s in sources:
            lines.append(
                f"  {s['source']:20s} {s['valid_pct']:5.1f}% valid "
                f"({s['valid']}/{s['total']}) avg {s['avg_ms']:.0f}ms"
            )
        return "\n".join(lines)

    def rejection_report_text(self) -> str:
        """Text report of top rejection reasons."""
        funnel = self.store.rejection_funnel(days=7)
        if not funnel:
            return "No rejections this week."
        lines = ["Top Rejection Reasons (7d):"]
        for r in funnel[:10]:
            lines.append(f"  {r['rejection_reason'][:50]:50s} {r['count']:4d} ({r['pct']:.1f}%)")
        return "\n".join(lines)

    def close(self):
        self.store.close()

