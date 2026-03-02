"""Repository for the workflows table."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from sentri.core.models import Workflow

from .connection import Database

logger = logging.getLogger("sentri.db.workflow")


class WorkflowRepository:
    """CRUD operations for workflows."""

    def __init__(self, db: Database):
        self._db = db

    def create(self, wf: Workflow) -> str:
        """Insert a new workflow. Returns the workflow id."""
        now = datetime.now(timezone.utc).isoformat()
        self._db.execute_write(
            """INSERT INTO workflows
               (id, alert_type, database_id, environment, status,
                created_at, updated_at, suggestion, verification,
                execution_plan, execution_result, approved_by,
                approved_at, approval_timeout, metadata)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                wf.id,
                wf.alert_type,
                wf.database_id,
                wf.environment,
                wf.status,
                now,
                now,
                wf.suggestion,
                wf.verification,
                wf.execution_plan,
                wf.execution_result,
                wf.approved_by,
                wf.approved_at,
                wf.approval_timeout,
                wf.metadata,
            ),
        )
        logger.debug("Created workflow %s", wf.id)
        return wf.id

    def get(self, workflow_id: str) -> Optional[Workflow]:
        """Fetch a single workflow by id."""
        row = self._db.execute_read_one("SELECT * FROM workflows WHERE id = ?", (workflow_id,))
        if row is None:
            return None
        return self._row_to_workflow(row)

    def update_status(self, workflow_id: str, status: str, **kwargs) -> int:
        """Update workflow status and optional extra fields."""
        now = datetime.now(timezone.utc).isoformat()
        sets = ["status = ?", "updated_at = ?"]
        params: list = [status, now]

        for key, value in kwargs.items():
            sets.append(f"{key} = ?")
            params.append(value)

        params.append(workflow_id)
        sql = f"UPDATE workflows SET {', '.join(sets)} WHERE id = ?"
        count = self._db.execute_write(sql, params)
        logger.debug("Updated workflow %s -> %s", workflow_id, status)
        return count

    def find_by_status(self, *statuses: str) -> list[Workflow]:
        """Find all workflows matching any of the given statuses."""
        placeholders = ", ".join("?" for _ in statuses)
        rows = self._db.execute_read(
            f"SELECT * FROM workflows WHERE status IN ({placeholders}) ORDER BY created_at",
            list(statuses),
        )
        return [self._row_to_workflow(r) for r in rows]

    def find_actionable(self) -> list[Workflow]:
        """Find workflows that need processing by the orchestrator."""
        return self.find_by_status(
            "DETECTED", "VERIFIED", "APPROVED", "AWAITING_APPROVAL", "DENIED"
        )

    def find_recent(self, limit: int = 10) -> list[Workflow]:
        """Fetch most recent workflows."""
        rows = self._db.execute_read(
            "SELECT * FROM workflows ORDER BY created_at DESC LIMIT ?",
            (limit,),
        )
        return [self._row_to_workflow(r) for r in rows]

    def count_by_status(self) -> dict[str, int]:
        """Return counts grouped by status."""
        rows = self._db.execute_read(
            "SELECT status, COUNT(*) as cnt FROM workflows GROUP BY status"
        )
        return {row["status"]: row["cnt"] for row in rows}

    def count_total(self) -> int:
        """Total number of workflows."""
        row = self._db.execute_read_one("SELECT COUNT(*) as cnt FROM workflows")
        return row["cnt"] if row else 0

    def find_duplicates(self, database_id: str, alert_type: str) -> list[Workflow]:
        """Check for active workflows on the same database and alert type."""
        return self.find_by_status_and_db(
            database_id,
            alert_type,
            "DETECTED",
            "VERIFYING",
            "VERIFIED",
            "AWAITING_APPROVAL",
            "APPROVED",
            "EXECUTING",
        )

    def count_recent_same(
        self,
        database_id: str,
        alert_type: str,
        hours: int = 24,
        exclude_id: str = "",
    ) -> tuple[int, float]:
        """Count COMPLETED workflows for same DB + alert in the last N hours.

        Only counts workflows that actually executed (COMPLETED, FAILED,
        ROLLED_BACK) — not ones that were just detected or escalated.
        This prevents old test runs from blocking new alerts.

        Returns (count, hours_since_most_recent_completed).
        hours_since_most_recent is 999.0 if no prior completed workflows found.
        """
        from datetime import timedelta

        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        rows = self._db.execute_read(
            """SELECT created_at FROM workflows
               WHERE database_id = ? AND alert_type = ?
               AND created_at > ?
               AND status IN ('COMPLETED', 'FAILED', 'ROLLED_BACK')
               ORDER BY created_at DESC""",
            [database_id, alert_type, cutoff],
        )
        count = len(rows)
        if count == 0:
            return 0, 999.0

        # Calculate hours since most recent (first row = newest)
        try:
            newest = datetime.fromisoformat(rows[0]["created_at"])
            if newest.tzinfo is None:
                newest = newest.replace(tzinfo=timezone.utc)
            delta = datetime.now(timezone.utc) - newest
            hours_since = delta.total_seconds() / 3600
        except (ValueError, TypeError):
            hours_since = 999.0

        return count, hours_since

    def find_by_status_and_db(
        self, database_id: str, alert_type: str, *statuses: str
    ) -> list[Workflow]:
        """Find workflows for a specific database/alert combo in given statuses."""
        placeholders = ", ".join("?" for _ in statuses)
        rows = self._db.execute_read(
            f"""SELECT * FROM workflows
                WHERE database_id = ? AND alert_type = ?
                AND status IN ({placeholders})
                ORDER BY created_at""",
            [database_id, alert_type, *statuses],
        )
        return [self._row_to_workflow(r) for r in rows]

    @staticmethod
    def _row_to_workflow(row) -> Workflow:
        keys = row.keys()
        return Workflow(
            id=row["id"],
            alert_type=row["alert_type"],
            database_id=row["database_id"],
            environment=row["environment"],
            status=row["status"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            suggestion=row["suggestion"],
            verification=row["verification"],
            execution_plan=row["execution_plan"],
            execution_result=row["execution_result"],
            approved_by=row["approved_by"],
            approved_at=row["approved_at"],
            approval_timeout=row["approval_timeout"],
            metadata=row["metadata"],
            severity=row["severity"] if "severity" in keys else None,
        )
