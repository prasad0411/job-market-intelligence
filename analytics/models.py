"""Type-safe data models for the analytics store."""
from dataclasses import dataclass, field, asdict
from typing import Optional
from datetime import datetime


@dataclass
class JobRecord:
    """Single processed job — fact table row."""
    url: str
    company: str
    title: str
    location: str = "Unknown"
    source: str = "Unknown"
    outcome: str = "valid"          # valid | discarded | reviewed
    rejection_reason: str = ""
    resume_type: str = "SDE"        # SDE | ML | DA
    job_type: str = "Internship"
    job_id: str = "N/A"
    remote: str = "Unknown"
    sponsorship: str = "Unknown"
    salary_low: Optional[float] = None
    salary_high: Optional[float] = None
    page_age_days: Optional[int] = None
    processing_time_ms: float = 0.0
    validation_stage_reached: str = ""
    entry_date: str = ""
    processed_at: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class RunRecord:
    """Single aggregator run — run dimension."""
    run_id: str = ""
    started_at: str = ""
    finished_at: str = ""
    elapsed_seconds: float = 0.0
    valid_count: int = 0
    discarded_count: int = 0
    duplicate_count: int = 0
    source: str = ""
    error_count: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class SourceMetric:
    """Source quality snapshot — for time-series tracking."""
    source: str
    date: str
    fetched: int = 0
    valid: int = 0
    rejected: int = 0
    valid_rate: float = 0.0
    avg_processing_ms: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)

