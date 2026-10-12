"""
Analytics Store — write and query interface for the SQLite analytics database.

Usage:
    store = AnalyticsStore()
    store.record_job(JobRecord(url="...", company="...", ...))
    stats = store.source_quality_report()
"""
import sqlite3
import os
import re
import logging
from datetime import datetime, timedelta
from typing import List, Optional, Dict

from analytics.schema import initialize_db
from analytics.models import JobRecord, RunRecord, SourceMetric

log = logging.getLogger(__name__)

DEFAULT_DB_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    ".local", "analytics.db"
)


class AnalyticsStore:
    """Read/write interface for the analytics database."""

    def __init__(self, db_path: str = None):
        self.db_path = db_path or DEFAULT_DB_PATH
        self.conn = initialize_db(self.db_path)

    def close(self):
        if self.conn:
            self.conn.close()

    # ── Write Methods ─────────────────────────────────────────────────────

    def record_job(self, job: JobRecord, run_id: str = ""):
        """Insert a single processed job into the fact table."""
        # Derive dimensions
        state = self._extract_state(job.location)
        is_remote = 1 if job.remote and job.remote.lower() in ("remote", "yes") else 0
        is_sponsored = 1 if job.sponsorship and job.sponsorship.lower() == "yes" else 0

        self.conn.execute("""
            INSERT INTO jobs (
                url, company, title, location, source, outcome,
                rejection_reason, resume_type, job_type, job_id,
                remote, sponsorship, salary_low, salary_high,
                page_age_days, processing_time_ms, validation_stage_reached,
                entry_date, processed_at, run_id,
                location_state, is_remote, is_sponsored
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            job.url, job.company, job.title, job.location, job.source,
            job.outcome, job.rejection_reason, job.resume_type, job.job_type,
            job.job_id, job.remote, job.sponsorship, job.salary_low,
            job.salary_high, job.page_age_days, job.processing_time_ms,
            job.validation_stage_reached, job.entry_date, job.processed_at,
            run_id, state, is_remote, is_sponsored
        ))
        self.conn.commit()

    def record_jobs_batch(self, jobs: List[JobRecord], run_id: str = ""):
        """Insert multiple jobs in a single transaction."""
        for job in jobs:
            state = self._extract_state(job.location)
            is_remote = 1 if job.remote and job.remote.lower() in ("remote", "yes") else 0
            is_sponsored = 1 if job.sponsorship and job.sponsorship.lower() == "yes" else 0
            self.conn.execute("""
                INSERT INTO jobs (
                    url, company, title, location, source, outcome,
                    rejection_reason, resume_type, job_type, job_id,
                    remote, sponsorship, salary_low, salary_high,
                    page_age_days, processing_time_ms, validation_stage_reached,
                    entry_date, processed_at, run_id,
                    location_state, is_remote, is_sponsored
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                job.url, job.company, job.title, job.location, job.source,
                job.outcome, job.rejection_reason, job.resume_type, job.job_type,
                job.job_id, job.remote, job.sponsorship, job.salary_low,
                job.salary_high, job.page_age_days, job.processing_time_ms,
                job.validation_stage_reached, job.entry_date, job.processed_at,
                run_id, state, is_remote, is_sponsored
            ))
        self.conn.commit()
        log.info(f"Recorded {len(jobs)} jobs to analytics store")

    def record_run(self, run: RunRecord):
        """Record an aggregator run."""
        self.conn.execute("""
            INSERT OR REPLACE INTO runs (
                run_id, started_at, finished_at, elapsed_seconds,
                valid_count, discarded_count, duplicate_count, source, error_count
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            run.run_id, run.started_at, run.finished_at, run.elapsed_seconds,
            run.valid_count, run.discarded_count, run.duplicate_count,
            run.source, run.error_count
        ))
        self.conn.commit()

    def record_source_metric(self, metric: SourceMetric):
        """Record daily source quality snapshot."""
        self.conn.execute("""
            INSERT OR REPLACE INTO source_quality (
                source, date, fetched, valid, rejected, valid_rate, avg_processing_ms
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            metric.source, metric.date, metric.fetched, metric.valid,
            metric.rejected, metric.valid_rate, metric.avg_processing_ms
        ))
        self.conn.commit()

    def record_rejection(self, date: str, stage: str, reason_category: str, count: int = 1):
        """Record rejection funnel entry."""
        self.conn.execute("""
            INSERT INTO rejection_funnel (date, stage, reason_category, count)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(date, stage, reason_category) 
            DO UPDATE SET count = count + ?
        """, (date, stage, reason_category, count, count))
        self.conn.commit()

    def update_company_outcome(self, company: str, outcome_type: str):
        """Update company outcome tracker. outcome_type: seen|valid|applied|rejected|interview"""
        col_map = {
            "seen": "total_seen", "valid": "total_valid",
            "applied": "total_applied", "rejected": "total_rejected",
            "interview": "total_interviews",
        }
        col = col_map.get(outcome_type)
        if not col:
            return
        self.conn.execute(f"""
            INSERT INTO company_outcomes (company, {col}, last_seen_at)
            VALUES (?, 1, ?)
            ON CONFLICT(company) DO UPDATE SET 
                {col} = {col} + 1,
                last_seen_at = ?
        """, (company, datetime.now().isoformat(), datetime.now().isoformat()))
        self.conn.commit()

    # ── Query Methods ─────────────────────────────────────────────────────

    def total_jobs(self, outcome: str = None) -> int:
        """Count total jobs, optionally filtered by outcome."""
        if outcome:
            row = self.conn.execute(
                "SELECT COUNT(*) FROM jobs WHERE outcome = ?", (outcome,)
            ).fetchone()
        else:
            row = self.conn.execute("SELECT COUNT(*) FROM jobs").fetchone()
        return row[0] if row else 0

    def source_quality_report(self, days: int = 30) -> List[Dict]:
        """Source quality over last N days."""
        cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        rows = self.conn.execute("""
            SELECT source,
                   COUNT(*) as total,
                   SUM(CASE WHEN outcome = 'valid' THEN 1 ELSE 0 END) as valid,
                   SUM(CASE WHEN outcome = 'discarded' THEN 1 ELSE 0 END) as discarded,
                   ROUND(AVG(processing_time_ms), 1) as avg_ms,
                   ROUND(100.0 * SUM(CASE WHEN outcome = 'valid' THEN 1 ELSE 0 END) / COUNT(*), 1) as valid_pct
            FROM jobs
            WHERE processed_at >= ?
            GROUP BY source
            ORDER BY valid_pct DESC
        """, (cutoff,)).fetchall()
        return [dict(r) for r in rows]

    def rejection_funnel(self, days: int = 7) -> List[Dict]:
        """Rejection reasons ranked by frequency."""
        cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        rows = self.conn.execute("""
            SELECT rejection_reason, COUNT(*) as count,
                   ROUND(100.0 * COUNT(*) / (SELECT COUNT(*) FROM jobs WHERE outcome = 'discarded' AND processed_at >= ?), 1) as pct
            FROM jobs
            WHERE outcome = 'discarded' AND rejection_reason != '' AND processed_at >= ?
            GROUP BY rejection_reason
            ORDER BY count DESC
            LIMIT 20
        """, (cutoff, cutoff)).fetchall()
        return [dict(r) for r in rows]

    def company_stats(self, min_seen: int = 2) -> List[Dict]:
        """Companies seen multiple times with outcome breakdown."""
        rows = self.conn.execute("""
            SELECT company,
                   COUNT(*) as total_seen,
                   SUM(CASE WHEN outcome = 'valid' THEN 1 ELSE 0 END) as valid,
                   SUM(CASE WHEN outcome = 'discarded' THEN 1 ELSE 0 END) as discarded
            FROM jobs
            GROUP BY company
            HAVING total_seen >= ?
            ORDER BY valid DESC
            LIMIT 50
        """, (min_seen,)).fetchall()
        return [dict(r) for r in rows]

    def location_distribution(self) -> List[Dict]:
        """Valid jobs by state."""
        rows = self.conn.execute("""
            SELECT location_state, COUNT(*) as count,
                   ROUND(100.0 * COUNT(*) / (SELECT COUNT(*) FROM jobs WHERE outcome = 'valid'), 1) as pct
            FROM jobs
            WHERE outcome = 'valid' AND location_state != ''
            GROUP BY location_state
            ORDER BY count DESC
            LIMIT 15
        """).fetchall()
        return [dict(r) for r in rows]

    def resume_type_distribution(self) -> List[Dict]:
        """Valid jobs by resume type."""
        rows = self.conn.execute("""
            SELECT resume_type, COUNT(*) as count,
                   ROUND(100.0 * COUNT(*) / (SELECT COUNT(*) FROM jobs WHERE outcome = 'valid'), 1) as pct
            FROM jobs
            WHERE outcome = 'valid'
            GROUP BY resume_type
            ORDER BY count DESC
        """).fetchall()
        return [dict(r) for r in rows]

    def daily_trend(self, days: int = 14) -> List[Dict]:
        """Daily valid/discarded counts for trend analysis."""
        cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        rows = self.conn.execute("""
            SELECT DATE(processed_at) as date,
                   SUM(CASE WHEN outcome = 'valid' THEN 1 ELSE 0 END) as valid,
                   SUM(CASE WHEN outcome = 'discarded' THEN 1 ELSE 0 END) as discarded,
                   COUNT(*) as total
            FROM jobs
            WHERE processed_at >= ?
            GROUP BY DATE(processed_at)
            ORDER BY date
        """, (cutoff,)).fetchall()
        return [dict(r) for r in rows]

    def processing_latency(self, days: int = 7) -> Dict:
        """Processing time percentiles."""
        cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        rows = self.conn.execute("""
            SELECT processing_time_ms FROM jobs
            WHERE processed_at >= ? AND processing_time_ms > 0
            ORDER BY processing_time_ms
        """, (cutoff,)).fetchall()
        if not rows:
            return {"p50": 0, "p90": 0, "p99": 0, "count": 0}
        times = [r[0] for r in rows]
        n = len(times)
        return {
            "p50": times[int(n * 0.5)],
            "p90": times[int(n * 0.9)],
            "p99": times[int(n * 0.99)] if n > 100 else times[-1],
            "count": n,
        }

    def feature_vector(self, company: str, title: str, source: str, location: str) -> Dict:
        """
        Generate ML feature vector for a job posting.
        Used for future response prediction model.
        """
        # Company history features
        co_row = self.conn.execute("""
            SELECT COUNT(*) as seen, 
                   SUM(CASE WHEN outcome='valid' THEN 1 ELSE 0 END) as valid_hist
            FROM jobs WHERE company = ?
        """, (company,)).fetchone()

        # Source reliability
        src_row = self.conn.execute("""
            SELECT COUNT(*) as total,
                   SUM(CASE WHEN outcome='valid' THEN 1 ELSE 0 END) as valid
            FROM jobs WHERE source = ?
        """, (source,)).fetchone()

        # Location density
        state = self._extract_state(location)
        loc_row = self.conn.execute("""
            SELECT COUNT(*) as count FROM jobs
            WHERE location_state = ? AND outcome = 'valid'
        """, (state,)).fetchone()

        return {
            "company_times_seen": co_row["seen"] if co_row else 0,
            "company_valid_rate": (co_row["valid_hist"] / co_row["seen"]) if co_row and co_row["seen"] > 0 else 0,
            "source_valid_rate": (src_row["valid"] / src_row["total"]) if src_row and src_row["total"] > 0 else 0,
            "location_state": state,
            "location_job_density": loc_row["count"] if loc_row else 0,
            "title_length": len(title),
            "has_ai_ml": 1 if any(kw in title.lower() for kw in ["ai", "ml", "machine learning", "data science"]) else 0,
            "has_sde": 1 if any(kw in title.lower() for kw in ["software", "sde", "backend", "frontend", "full stack"]) else 0,
        }

    # ── Helpers ────────────────────────────────────────────────────────────

    @staticmethod
    def _extract_state(location: str) -> str:
        """Extract US state code from location string."""
        if not location:
            return ""
        m = re.search(r',\s*([A-Z]{2})\b', location)
        if m:
            return m.group(1)
        return ""

