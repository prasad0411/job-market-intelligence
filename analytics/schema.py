"""Star schema DDL for the analytics SQLite database."""
import sqlite3
import os
import logging

log = logging.getLogger(__name__)

SCHEMA_VERSION = 1

DDL = """
-- ============================================================================
-- FACT TABLE: Every processed job (valid, discarded, or reviewed)
-- ============================================================================
CREATE TABLE IF NOT EXISTS jobs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    url             TEXT NOT NULL,
    company         TEXT NOT NULL,
    title           TEXT NOT NULL,
    location        TEXT DEFAULT 'Unknown',
    source          TEXT DEFAULT 'Unknown',
    outcome         TEXT DEFAULT 'valid',          -- valid | discarded | reviewed
    rejection_reason TEXT DEFAULT '',
    resume_type     TEXT DEFAULT 'SDE',             -- SDE | ML | DA
    job_type        TEXT DEFAULT 'Internship',
    job_id          TEXT DEFAULT 'N/A',
    remote          TEXT DEFAULT 'Unknown',
    sponsorship     TEXT DEFAULT 'Unknown',
    salary_low      REAL,
    salary_high     REAL,
    page_age_days   INTEGER,
    processing_time_ms REAL DEFAULT 0.0,
    validation_stage_reached TEXT DEFAULT '',
    entry_date      TEXT DEFAULT '',
    processed_at    TEXT NOT NULL,
    run_id          TEXT DEFAULT '',
    
    -- Derived dimensions (denormalized for query speed)
    company_sector  TEXT DEFAULT '',
    location_state  TEXT DEFAULT '',
    is_remote       INTEGER DEFAULT 0,
    is_sponsored    INTEGER DEFAULT 0
);

-- ============================================================================
-- DIMENSION: Aggregator runs
-- ============================================================================
CREATE TABLE IF NOT EXISTS runs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id          TEXT UNIQUE NOT NULL,
    started_at      TEXT NOT NULL,
    finished_at     TEXT DEFAULT '',
    elapsed_seconds REAL DEFAULT 0.0,
    valid_count     INTEGER DEFAULT 0,
    discarded_count INTEGER DEFAULT 0,
    duplicate_count INTEGER DEFAULT 0,
    source          TEXT DEFAULT '',
    error_count     INTEGER DEFAULT 0
);

-- ============================================================================
-- DIMENSION: Source quality over time (one row per source per day)
-- ============================================================================
CREATE TABLE IF NOT EXISTS source_quality (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    source          TEXT NOT NULL,
    date            TEXT NOT NULL,
    fetched         INTEGER DEFAULT 0,
    valid           INTEGER DEFAULT 0,
    rejected        INTEGER DEFAULT 0,
    valid_rate      REAL DEFAULT 0.0,
    avg_processing_ms REAL DEFAULT 0.0,
    UNIQUE(source, date)
);

-- ============================================================================
-- DIMENSION: Rejection reasons (for funnel analysis)
-- ============================================================================
CREATE TABLE IF NOT EXISTS rejection_funnel (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    date            TEXT NOT NULL,
    stage           TEXT NOT NULL,
    reason_category TEXT NOT NULL,
    count           INTEGER DEFAULT 0,
    UNIQUE(date, stage, reason_category)
);

-- ============================================================================
-- DIMENSION: Company outcomes (for response prediction)
-- ============================================================================
CREATE TABLE IF NOT EXISTS company_outcomes (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    company         TEXT NOT NULL,
    total_seen      INTEGER DEFAULT 0,
    total_valid     INTEGER DEFAULT 0,
    total_applied   INTEGER DEFAULT 0,
    total_rejected  INTEGER DEFAULT 0,
    total_interviews INTEGER DEFAULT 0,
    last_seen_at    TEXT DEFAULT '',
    avg_salary_low  REAL,
    UNIQUE(company)
);

-- ============================================================================
-- METADATA: Schema version tracking
-- ============================================================================
CREATE TABLE IF NOT EXISTS schema_meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

-- ============================================================================
-- INDEXES for common query patterns
-- ============================================================================
CREATE INDEX IF NOT EXISTS idx_jobs_company ON jobs(company);
CREATE INDEX IF NOT EXISTS idx_jobs_source ON jobs(source);
CREATE INDEX IF NOT EXISTS idx_jobs_outcome ON jobs(outcome);
CREATE INDEX IF NOT EXISTS idx_jobs_processed_at ON jobs(processed_at);
CREATE INDEX IF NOT EXISTS idx_jobs_location_state ON jobs(location_state);
CREATE INDEX IF NOT EXISTS idx_jobs_resume_type ON jobs(resume_type);
CREATE INDEX IF NOT EXISTS idx_source_quality_date ON source_quality(date);
CREATE INDEX IF NOT EXISTS idx_rejection_date ON rejection_funnel(date);
"""


def initialize_db(db_path: str) -> sqlite3.Connection:
    """Create database and apply schema. Idempotent."""
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(DDL)
    conn.execute(
        "INSERT OR REPLACE INTO schema_meta (key, value) VALUES (?, ?)",
        ("version", str(SCHEMA_VERSION))
    )
    conn.commit()
    log.info(f"Analytics DB initialized at {db_path} (v{SCHEMA_VERSION})")
    return conn

