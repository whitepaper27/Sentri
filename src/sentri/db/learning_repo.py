"""Repository for the learning_observations table (v2.0)."""

from __future__ import annotations

import logging
from typing import Optional

from sentri.core.models import LearningObservation

from .connection import Database

logger = logging.getLogger("sentri.db.learning")


class LearningRepository:
    """CRUD for learning observations captured by Agent 5."""

    def __init__(self, db: Database):
        self._db = db

    def create(self, obs: LearningObservation) -> int:
        """Insert a new observation. Returns the auto-generated id."""
        return self._db.execute_write_returning_id(
            """INSERT INTO learning_observations
               (workflow_id, alert_type, database_id,
                observation_type, data, status)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                obs.workflow_id,
                obs.alert_type,
                obs.database_id,
                obs.observation_type,
                obs.data,
                obs.status,
            ),
        )

    def get(self, obs_id: int) -> Optional[LearningObservation]:
        """Fetch a single observation by id."""
        row = self._db.execute_read_one(
            "SELECT * FROM learning_observations WHERE id = ?",
            (obs_id,),
        )
        if row is None:
            return None
        return self._row_to_obs(row)

    def find_by_alert_type(self, alert_type: str) -> list[LearningObservation]:
        """Find all observations for a given alert type."""
        rows = self._db.execute_read(
            "SELECT * FROM learning_observations WHERE alert_type = ? ORDER BY created_at",
            (alert_type,),
        )
        return [self._row_to_obs(r) for r in rows]

    def find_by_status(self, status: str) -> list[LearningObservation]:
        """Find observations by processing status."""
        rows = self._db.execute_read(
            "SELECT * FROM learning_observations WHERE status = ? ORDER BY created_at",
            (status,),
        )
        return [self._row_to_obs(r) for r in rows]

    def update_status(self, obs_id: int, status: str) -> int:
        """Update the status of an observation."""
        return self._db.execute_write(
            """UPDATE learning_observations
               SET status = ?, processed_at = CURRENT_TIMESTAMP
               WHERE id = ?""",
            (status, obs_id),
        )

    def count_by_alert_type(self) -> dict[str, int]:
        """Return observation counts grouped by alert type."""
        rows = self._db.execute_read(
            "SELECT alert_type, COUNT(*) as cnt FROM learning_observations GROUP BY alert_type"
        )
        return {row["alert_type"]: row["cnt"] for row in rows}

    def count_total(self) -> int:
        """Total number of observations."""
        row = self._db.execute_read_one("SELECT COUNT(*) as cnt FROM learning_observations")
        return row["cnt"] if row else 0

    @staticmethod
    def _row_to_obs(row) -> LearningObservation:
        return LearningObservation(
            id=row["id"],
            workflow_id=row["workflow_id"],
            alert_type=row["alert_type"],
            database_id=row["database_id"],
            observation_type=row["observation_type"],
            data=row["data"],
            status=row["status"],
            created_at=row["created_at"],
            processed_at=row["processed_at"],
        )
