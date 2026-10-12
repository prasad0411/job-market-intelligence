"""
Source Health Monitor — detect silent parser breakages.

Tracks historical job counts per source and flags anomalies
when a source returns 0 jobs that normally returns 20+.

Usage:
    from aggregator.source_health import SourceHealthMonitor
    monitor = SourceHealthMonitor()
    monitor.record_run(source_stats)  # after aggregator run
    alerts = monitor.check_health()
"""
import json
import os
import logging
from datetime import datetime

log = logging.getLogger(__name__)

_HEALTH_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    ".local", "source_health.json"
)


class SourceHealthMonitor:
    def __init__(self):
        self.history = self._load()

    def _load(self):
        try:
            if os.path.exists(_HEALTH_FILE):
                return json.load(open(_HEALTH_FILE))
        except Exception:
            pass
        return {"sources": {}, "alerts": []}

    def _save(self):
        try:
            os.makedirs(os.path.dirname(_HEALTH_FILE), exist_ok=True)
            with open(_HEALTH_FILE, "w") as f:
                json.dump(self.history, f, indent=2)
        except Exception:
            pass

    def record_run(self, source_stats):
        """Record job counts from this run. source_stats: dict of source_name -> count."""
        ts = datetime.now().isoformat()
        for source, count in source_stats.items():
            if source not in self.history["sources"]:
                self.history["sources"][source] = []
            self.history["sources"][source].append({"ts": ts, "count": count})
            # Keep last 30 runs
            self.history["sources"][source] = self.history["sources"][source][-30:]
        self._save()

    def check_health(self):
        """Check for anomalies. Returns list of alert strings."""
        alerts = []
        for source, runs in self.history["sources"].items():
            if len(runs) < 3:
                continue
            counts = [r["count"] for r in runs]
            avg = sum(counts[:-1]) / len(counts[:-1]) if len(counts) > 1 else 0
            latest = counts[-1]

            # Alert if source dropped to 0 but usually has 10+
            if latest == 0 and avg >= 10:
                alert = f"SOURCE DOWN: {source} returned 0 jobs (avg: {avg:.0f})"
                alerts.append(alert)
                log.warning(alert)

            # Alert if source dropped by 80%+
            elif avg > 0 and latest < avg * 0.2:
                alert = f"SOURCE DEGRADED: {source} returned {latest} jobs (avg: {avg:.0f}, -{ (1 - latest/avg) * 100:.0f}%)"
                alerts.append(alert)
                log.warning(alert)

        self.history["alerts"] = alerts
        self._save()
        return alerts
