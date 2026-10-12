"""
Data Quality Scoring Framework.

Every job record gets a completeness score (0-100) based on 8 dimensions.
Tracks quality by source over time for pipeline optimization.

Usage:
    scorer = DataQualityScorer()
    score = scorer.score_job({"company": "Google", "title": "SDE Intern", ...})
    report = scorer.quality_report_by_source(store)
"""
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional
from datetime import datetime, timedelta

log = logging.getLogger(__name__)


@dataclass
class QualityDimension:
    """Single quality dimension with weight."""
    name: str
    weight: float = 1.0
    description: str = ""


# 8 completeness dimensions
DIMENSIONS = [
    QualityDimension("company", 2.0, "Company name present and not Unknown"),
    QualityDimension("title", 2.0, "Job title present and not Unknown"),
    QualityDimension("location", 1.5, "Location present and not Unknown"),
    QualityDimension("url", 2.0, "Valid URL present"),
    QualityDimension("job_id", 1.0, "Job ID present and not N/A"),
    QualityDimension("salary", 0.5, "Salary information available"),
    QualityDimension("sponsorship", 1.0, "Sponsorship status known"),
    QualityDimension("remote", 0.5, "Remote/onsite status known"),
]

TOTAL_WEIGHT = sum(d.weight for d in DIMENSIONS)


@dataclass
class QualityScore:
    """Quality score for a single job record."""
    total_score: float = 0.0            # 0-100
    dimension_scores: Dict[str, bool] = field(default_factory=dict)
    missing_fields: List[str] = field(default_factory=list)
    completeness_pct: float = 0.0       # percentage of dimensions satisfied

    @property
    def grade(self) -> str:
        if self.total_score >= 90:
            return "A"
        elif self.total_score >= 75:
            return "B"
        elif self.total_score >= 50:
            return "C"
        elif self.total_score >= 25:
            return "D"
        return "F"


class DataQualityScorer:
    """Scores job records on 8 completeness dimensions."""

    @staticmethod
    def score_job(job: dict) -> QualityScore:
        """Score a single job record. Returns QualityScore with 0-100 score."""
        result = QualityScore()
        weighted_sum = 0.0

        checks = {
            "company": bool(job.get("company") and job["company"] not in ("Unknown", "", None)),
            "title": bool(job.get("title") and job["title"] not in ("Unknown", "", None)),
            "location": bool(job.get("location") and job["location"] not in ("Unknown", "", None)),
            "url": bool(job.get("url") and str(job.get("url", "")).startswith("http")),
            "job_id": bool(job.get("job_id") and job["job_id"] not in ("N/A", "", None)),
            "salary": bool(job.get("salary_low") or job.get("salary_high")),
            "sponsorship": bool(job.get("sponsorship") and job["sponsorship"] not in ("Unknown", "", None)),
            "remote": bool(job.get("remote") and job["remote"] not in ("Unknown", "", None)),
        }

        for dim in DIMENSIONS:
            passed = checks.get(dim.name, False)
            result.dimension_scores[dim.name] = passed
            if passed:
                weighted_sum += dim.weight
            else:
                result.missing_fields.append(dim.name)

        result.total_score = round((weighted_sum / TOTAL_WEIGHT) * 100, 1)
        result.completeness_pct = round(
            sum(1 for v in checks.values() if v) / len(checks) * 100, 1
        )
        return result

    @staticmethod
    def score_batch(jobs: list) -> Dict:
        """Score a batch of jobs and return aggregate stats."""
        scores = [DataQualityScorer.score_job(j) for j in jobs]
        if not scores:
            return {"count": 0, "avg_score": 0, "grade_distribution": {}}

        avg = sum(s.total_score for s in scores) / len(scores)
        grades = {}
        for s in scores:
            grades[s.grade] = grades.get(s.grade, 0) + 1

        # Missing field frequency
        missing_freq = {}
        for s in scores:
            for f in s.missing_fields:
                missing_freq[f] = missing_freq.get(f, 0) + 1

        return {
            "count": len(scores),
            "avg_score": round(avg, 1),
            "min_score": min(s.total_score for s in scores),
            "max_score": max(s.total_score for s in scores),
            "grade_distribution": grades,
            "missing_field_frequency": dict(
                sorted(missing_freq.items(), key=lambda x: -x[1])
            ),
        }

    @staticmethod
    def quality_by_source(store) -> List[Dict]:
        """Quality breakdown by source — identifies low-quality data sources."""
        rows = store.conn.execute("""
            SELECT source,
                   COUNT(*) as total,
                   SUM(CASE WHEN location != 'Unknown' AND location != '' THEN 1 ELSE 0 END) as has_location,
                   SUM(CASE WHEN job_id != 'N/A' AND job_id != '' THEN 1 ELSE 0 END) as has_job_id,
                   SUM(CASE WHEN sponsorship != 'Unknown' AND sponsorship != '' THEN 1 ELSE 0 END) as has_sponsorship,
                   SUM(CASE WHEN remote != 'Unknown' AND remote != '' THEN 1 ELSE 0 END) as has_remote,
                   SUM(CASE WHEN salary_low IS NOT NULL THEN 1 ELSE 0 END) as has_salary
            FROM jobs
            WHERE source NOT IN ('Unknown', '')
            GROUP BY source
            HAVING total >= 5
            ORDER BY total DESC
        """).fetchall()

        report = []
        for r in rows:
            total = r["total"]
            report.append({
                "source": r["source"],
                "total": total,
                "location_pct": round(100 * r["has_location"] / total, 1),
                "job_id_pct": round(100 * r["has_job_id"] / total, 1),
                "sponsorship_pct": round(100 * r["has_sponsorship"] / total, 1),
                "remote_pct": round(100 * r["has_remote"] / total, 1),
                "salary_pct": round(100 * r["has_salary"] / total, 1),
                "avg_completeness": round(
                    (r["has_location"] + r["has_job_id"] + r["has_sponsorship"] +
                     r["has_remote"] + r["has_salary"]) / (5 * total) * 100, 1
                ),
            })
        return sorted(report, key=lambda x: -x["avg_completeness"])

    @staticmethod
    def quality_report_text(store) -> str:
        """Text report for nightly digest."""
        by_source = DataQualityScorer.quality_by_source(store)
        if not by_source:
            return "No quality data available."
        lines = ["Data Quality by Source:"]
        lines.append(f"  {'Source':20s} {'Total':>6s} {'Loc%':>6s} {'ID%':>6s} {'Spon%':>6s} {'Rmt%':>6s} {'Sal%':>6s}")
        for s in by_source[:10]:
            lines.append(
                f"  {s['source']:20s} {s['total']:6d} "
                f"{s['location_pct']:5.1f}% {s['job_id_pct']:5.1f}% "
                f"{s['sponsorship_pct']:5.1f}% {s['remote_pct']:5.1f}% "
                f"{s['salary_pct']:5.1f}%"
            )
        return "\n".join(lines)

