"""
Cumulative pipeline metrics tracker.

Records stats after every aggregator run for resume-ready numbers
and long-term reliability tracking.

Usage:
    from aggregator.metrics import PipelineMetrics
    metrics = PipelineMetrics()
    metrics.record_run(valid=15, discarded=42, corrected=3, time_sec=180)
    print(metrics.summary())  # "6,120 jobs processed | 95% accuracy | 92 consecutive days"
"""
import json
import os
import logging
from datetime import datetime, timedelta

log = logging.getLogger(__name__)

_METRICS_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    ".local", "metrics.json"
)


class PipelineMetrics:
    def __init__(self):
        self.data = self._load()

    def _load(self):
        try:
            if os.path.exists(_METRICS_PATH):
                return json.load(open(_METRICS_PATH))
        except Exception:
            pass
        return {
            "first_run": None,
            "total_runs": 0,
            "total_valid": 0,
            "total_discarded": 0,
            "total_url_corrections": 0,
            "total_emails_drafted": 0,
            "total_emails_verified": 0,
            "consecutive_success_days": 0,
            "last_run_date": None,
            "last_run_time_sec": 0,
            "avg_run_time_sec": 0,
            "sources_active": 0,
            "accuracy_rate": 0.0,
            "runs": [],
        }

    def _save(self):
        try:
            os.makedirs(os.path.dirname(_METRICS_PATH), exist_ok=True)
            with open(_METRICS_PATH, "w") as f:
                json.dump(self.data, f, indent=2)
        except Exception as e:
            log.warning(f"Metrics save failed: {e}")

    def record_run(self, valid=0, discarded=0, url_corrections=0,
                   emails_drafted=0, emails_verified=0,
                   sources_active=0, time_sec=0):
        """Record a single aggregator run."""
        now = datetime.now()
        today = now.strftime("%Y-%m-%d")

        if not self.data["first_run"]:
            self.data["first_run"] = today

        self.data["total_runs"] += 1
        self.data["total_valid"] += valid
        self.data["total_discarded"] += discarded
        self.data["total_url_corrections"] += url_corrections
        self.data["total_emails_drafted"] += emails_drafted
        self.data["total_emails_verified"] += emails_verified
        self.data["sources_active"] = max(self.data["sources_active"], sources_active)
        self.data["last_run_time_sec"] = time_sec

        # Consecutive days tracking
        last = self.data.get("last_run_date")
        if last:
            last_date = datetime.strptime(last, "%Y-%m-%d").date()
            delta = (now.date() - last_date).days
            if delta <= 1:
                self.data["consecutive_success_days"] += (1 if delta == 1 else 0)
            else:
                self.data["consecutive_success_days"] = 1
        else:
            self.data["consecutive_success_days"] = 1

        self.data["last_run_date"] = today

        # Accuracy rate
        total = self.data["total_valid"] + self.data["total_discarded"]
        if total > 0:
            self.data["accuracy_rate"] = round(
                self.data["total_valid"] / total, 4
            )

        # Average run time
        run_times = [r.get("time_sec", 0) for r in self.data["runs"][-29:]] + [time_sec]
        self.data["avg_run_time_sec"] = round(sum(run_times) / len(run_times), 1)

        # Keep last 90 runs
        self.data["runs"].append({
            "date": today,
            "time": now.strftime("%H:%M"),
            "valid": valid,
            "discarded": discarded,
            "url_corrections": url_corrections,
            "time_sec": time_sec,
        })
        self.data["runs"] = self.data["runs"][-90:]

        self._save()

    def summary(self):
        """One-line summary for terminal output."""
        d = self.data
        total = d["total_valid"] + d["total_discarded"]
        return (
            f"Pipeline: {total:,} jobs processed | "
            f"{d['total_valid']:,} valid | "
            f"{d['consecutive_success_days']} consecutive days | "
            f"{d['total_url_corrections']} auto-corrections | "
            f"avg {d['avg_run_time_sec']:.0f}s/run"
        )

    def get_resume_stats(self):
        """Key numbers for resume bullets."""
        d = self.data
        return {
            "total_processed": d["total_valid"] + d["total_discarded"],
            "total_valid": d["total_valid"],
            "accuracy_pct": round(d["accuracy_rate"] * 100, 1),
            "uptime_days": d["consecutive_success_days"],
            "total_runs": d["total_runs"],
            "auto_corrections": d["total_url_corrections"],
            "sources": d["sources_active"],
        }
