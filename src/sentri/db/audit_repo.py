"""Repository for the audit_log table. Append-only - no update or delete."""

from __future__ import annotations

import logging

from sentri.core.models import AuditRecord

from .connection import Database

logger = logging.getLogger("sentri.db.audit")


class AuditRepository:
    """Append-only audit log operations."""

    def __init__(self, db: Database):
        self._db = db

    def create(self, record: AuditRecord) -> int:
        """Insert an audit record. Returns the auto-generated id."""
        row_id = self._db.execute_write_returning_id(
            """INSERT INTO audit_log
               (workflow_id, action_type, action_sql, database_id,
                environment, executed_by, approved_by, result,
                error_message, evidence, change_ticket)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                record.workflow_id,
                record.action_type,
                record.action_sql,
                record.database_id,
                record.environment,
                record.executed_by,
                record.approved_by,
                record.result,
                record.error_message,
                record.evidence,
                record.change_ticket,
            ),
        )
        logger.info(
            "Audit record %d: workflow=%s action=%s result=%s",
            row_id,
            record.workflow_id,
            record.action_type,
            record.result,
        )
        return row_id

    def find_by_workflow(self, workflow_id: str) -> list[AuditRecord]:
        """Get all audit records for a workflow."""
        rows = self._db.execute_read(
            "SELECT * FROM audit_log WHERE workflow_id = ? ORDER BY timestamp",
            (workflow_id,),
        )
        return [self._row_to_record(r) for r in rows]

    def find_recent(self, limit: int = 50) -> list[AuditRecord]:
        """Get most recent audit records."""
        rows = self._db.execute_read(
            "SELECT * FROM audit_log ORDER BY timestamp DESC LIMIT ?",
            (limit,),
        )
        return [self._row_to_record(r) for r in rows]

    def find_by_database(self, database_id: str, limit: int = 50) -> list[AuditRecord]:
        """Get audit records for a specific database."""
        rows = self._db.execute_read(
            "SELECT * FROM audit_log WHERE database_id = ? ORDER BY timestamp DESC LIMIT ?",
            (database_id, limit),
        )
        return [self._row_to_record(r) for r in rows]

    def count_by_result(self) -> dict[str, int]:
        """Return counts grouped by result (SUCCESS, FAILED, ROLLED_BACK)."""
        rows = self._db.execute_read(
            "SELECT result, COUNT(*) as cnt FROM audit_log GROUP BY result"
        )
        return {row["result"]: row["cnt"] for row in rows}

    @staticmethod
    def _row_to_record(row) -> AuditRecord:
        return AuditRecord(
            id=row["id"],
            workflow_id=row["workflow_id"],
            timestamp=row["timestamp"],
            action_type=row["action_type"],
            action_sql=row["action_sql"],
            database_id=row["database_id"],
            environment=row["environment"],
            executed_by=row["executed_by"],
            approved_by=row["approved_by"],
            result=row["result"],
            error_message=row["error_message"],
            evidence=row["evidence"],
            change_ticket=row["change_ticket"],
        )
