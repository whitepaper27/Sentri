"""Monitor: tracks the impact of applied improvements over a monitoring window.

After a policy change is applied, monitors subsequent workflow outcomes
for the affected alert type over a configurable period (default 30 days).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from sentri.db.learning_repo import LearningRepository

logger = logging.getLogger("sentri.learning.monitor")


class Monitor:
    """Tracks post-improvement performance for policy changes."""

    def __init__(
        self,
        learning_repo: LearningRepository,
        monitoring_days: int = 30,
    ):
        self._repo = learning_repo
        self._monitoring_days = monitoring_days

    def get_impact_summary(self, alert_type: str) -> dict:
        """Get the current performance summary for an alert type.

        Returns success rate, failure rate, and trend info.
        """
        observations = self._repo.find_by_alert_type(alert_type)
        if not observations:
            return {
                "alert_type": alert_type,
                "total_observations": 0,
                "status": "no_data",
            }

        total = len(observations)
        successes = sum(1 for o in observations if o.observation_type == "EXECUTION_SUCCESS")
        failures = sum(1 for o in observations if o.observation_type == "EXECUTION_FAILURE")
        rollbacks = sum(1 for o in observations if o.observation_type == "ROLLBACK")
        false_positives = sum(1 for o in observations if o.observation_type == "FALSE_POSITIVE")

        # Recent window (last N days)
        cutoff = (datetime.now(timezone.utc) - timedelta(days=self._monitoring_days)).isoformat()

        recent = [o for o in observations if o.created_at and o.created_at >= cutoff]
        recent_total = len(recent)
        recent_successes = sum(1 for o in recent if o.observation_type == "EXECUTION_SUCCESS")

        return {
            "alert_type": alert_type,
            "total_observations": total,
            "successes": successes,
            "failures": failures,
            "rollbacks": rollbacks,
            "false_positives": false_positives,
            "success_rate": round(successes / max(total, 1), 3),
            "recent_period_days": self._monitoring_days,
            "recent_observations": recent_total,
            "recent_success_rate": round(recent_successes / max(recent_total, 1), 3),
            "status": "monitoring",
        }

    def get_all_summaries(self) -> list[dict]:
        """Get impact summaries for all alert types with observations."""
        counts = self._repo.count_by_alert_type()
        return [self.get_impact_summary(alert_type) for alert_type in counts]

    def is_improvement_effective(
        self,
        alert_type: str,
        baseline_success_rate: float,
    ) -> Optional[bool]:
        """Check if a recent improvement has been effective.

        Returns True if success rate improved, False if degraded, None if
        not enough data yet.
        """
        summary = self.get_impact_summary(alert_type)

        if summary["recent_observations"] < 3:
            return None  # Not enough data

        current_rate = summary["recent_success_rate"]
        if current_rate > baseline_success_rate + 0.05:
            return True
        elif current_rate < baseline_success_rate - 0.05:
            return False

        return None  # Within noise margin
