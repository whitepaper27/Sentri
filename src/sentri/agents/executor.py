"""Agent 4: The Executor - Safe execution of database fixes."""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

from sentri.core.constants import (
    EXECUTION_TIMEOUT,
    LOCK_EXPIRY,
    ExecutionOutcome,
    WorkflowStatus,
)
from sentri.core.exceptions import (
    LockAcquisitionError,
    RollbackError,
)
from sentri.core.models import AuditRecord, ExecutionPlan, ExecutionResult, Workflow
from sentri.oracle.connection_pool import OracleConnectionPool
from sentri.oracle.query_runner import QueryRunner

from .base import AgentContext, BaseAgent

logger = logging.getLogger("sentri.agents.executor")


class ExecutorAgent(BaseAgent):
    """Execute fixes on databases with safety checks, locking, and rollback."""

    def __init__(self, context: AgentContext, oracle_pool: Optional[OracleConnectionPool] = None):
        super().__init__("executor", context)
        self._oracle_pool = oracle_pool or OracleConnectionPool()
        self._query_runner = QueryRunner(timeout_seconds=EXECUTION_TIMEOUT)

    def process(self, workflow_id: str) -> dict:
        """Execute the fix for a workflow.

        Returns {"status": "success"|"failure"|"rolled_back", "result": ExecutionResult}
        """
        workflow = self.context.workflow_repo.get(workflow_id)
        if not workflow:
            return {"status": "failure", "error": f"Workflow {workflow_id} not found"}

        if not workflow.execution_plan:
            return {"status": "failure", "error": "No execution plan"}

        plan = ExecutionPlan.from_json(workflow.execution_plan)
        lock_key = f"{workflow.database_id}:{plan.action_type}"

        try:
            # 1. Acquire lock
            self._acquire_lock(lock_key, workflow_id)

            # 2. Validate rollback exists
            if not plan.rollback_sql.strip():
                self.logger.warning("No rollback SQL for %s - proceeding with caution", workflow_id)

            # 3. Execute
            result = self._execute_plan(workflow, plan)

            # 4. Write audit record
            self._write_audit(workflow, plan, result)

            # 5. Update workflow status
            if result.success:
                self.context.workflow_repo.update_status(
                    workflow_id,
                    WorkflowStatus.COMPLETED.value,
                    execution_result=result.to_json(),
                )
                return {"status": "success", "result": result}
            elif result.rolled_back:
                self.context.workflow_repo.update_status(
                    workflow_id,
                    WorkflowStatus.ROLLED_BACK.value,
                    execution_result=result.to_json(),
                )
                return {"status": "rolled_back", "result": result}
            else:
                self.context.workflow_repo.update_status(
                    workflow_id,
                    WorkflowStatus.FAILED.value,
                    execution_result=result.to_json(),
                )
                return {"status": "failure", "result": result}

        except LockAcquisitionError as e:
            self.logger.error("Lock acquisition failed for %s: %s", workflow_id, e)
            self.context.workflow_repo.update_status(workflow_id, WorkflowStatus.FAILED.value)
            return {"status": "failure", "error": str(e)}

        except Exception as e:
            self.logger.error("Execution error for %s: %s", workflow_id, e)
            self.context.workflow_repo.update_status(workflow_id, WorkflowStatus.FAILED.value)
            return {"status": "failure", "error": str(e)}

        finally:
            # Always release lock
            self._release_lock(lock_key)

    def _execute_plan(self, workflow: Workflow, plan: ExecutionPlan) -> ExecutionResult:
        """Execute the forward SQL, validate, and rollback on failure."""
        start_time = time.time()

        # Get Oracle connection from settings (credentials) and registry (metadata)
        db_cfg = self.context.settings.get_database(workflow.database_id)
        env_record = self.context.environment_repo.get(workflow.database_id)

        conn_string = ""
        if env_record:
            conn_string = env_record.connection_string
        elif db_cfg:
            conn_string = db_cfg.connection_string
        else:
            return ExecutionResult(
                success=False,
                action_sql_executed=plan.forward_sql,
                output="",
                error_message=f"No config for database {workflow.database_id}",
            )

        try:
            conn = self._oracle_pool.get_connection(
                database_id=workflow.database_id,
                connection_string=conn_string,
                password=db_cfg.password if db_cfg else "",
                username=db_cfg.username if db_cfg and db_cfg.username else None,
                read_only=False,
            )
        except Exception as e:
            return ExecutionResult(
                success=False,
                action_sql_executed=plan.forward_sql,
                output="",
                error_message=f"Connection failed: {e}",
                duration_seconds=time.time() - start_time,
            )

        try:
            # Pre-execution safety: verify DB is writable (catches switchovers)
            safety = self._check_db_safety(conn)
            if not safety["safe"]:
                conn.close()
                return ExecutionResult(
                    success=False,
                    action_sql_executed=plan.forward_sql,
                    output="",
                    error_message=f"Safety check failed: {safety['reason']}",
                    duration_seconds=time.time() - start_time,
                )

            # Resolve datafile path if needed (OMF off → Oracle needs explicit path)
            forward_sql = self._resolve_datafile_path(conn, plan)

            # Capture metrics before
            metrics_before = self._capture_metrics(conn, plan.validation_sql, plan.params)

            # Execute forward SQL
            self.logger.info("Executing: %s", forward_sql[:200])
            self._query_runner.execute_write(conn, forward_sql)
            conn.commit()

            # Validate
            metrics_after = self._capture_metrics(conn, plan.validation_sql, plan.params)
            validation_passed = self._validate_execution(metrics_before, metrics_after)

            if validation_passed:
                return ExecutionResult(
                    success=True,
                    action_sql_executed=plan.forward_sql,
                    output="Execution and validation successful",
                    metrics_before=metrics_before,
                    metrics_after=metrics_after,
                    duration_seconds=time.time() - start_time,
                )

            # Validation failed - rollback
            self.logger.warning("Validation failed for %s, rolling back", workflow.id)
            return self._do_rollback(conn, plan, metrics_before, metrics_after, start_time)

        except Exception as e:
            # Execution failed - attempt rollback
            self.logger.error("Execution failed: %s, attempting rollback", e)
            try:
                conn.rollback()
                rollback_result = self._do_rollback(conn, plan, {}, {}, start_time)
                rollback_result.error_message = str(e)
                return rollback_result
            except Exception as re:
                return ExecutionResult(
                    success=False,
                    action_sql_executed=plan.forward_sql,
                    output="",
                    error_message=f"Execution failed: {e}. Rollback also failed: {re}",
                    rolled_back=False,
                    duration_seconds=time.time() - start_time,
                )
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def _do_rollback(
        self,
        conn,
        plan: ExecutionPlan,
        metrics_before: dict,
        metrics_after: dict,
        start_time: float,
    ) -> ExecutionResult:
        """Execute the rollback SQL."""
        if not plan.rollback_sql.strip():
            return ExecutionResult(
                success=False,
                action_sql_executed=plan.forward_sql,
                output="No rollback SQL available",
                error_message="Validation failed but no rollback plan",
                rolled_back=False,
                metrics_before=metrics_before,
                metrics_after=metrics_after,
                duration_seconds=time.time() - start_time,
            )

        try:
            self._query_runner.execute_write(conn, plan.rollback_sql)
            conn.commit()
            return ExecutionResult(
                success=False,
                action_sql_executed=plan.forward_sql,
                output="Rolled back successfully",
                rolled_back=True,
                rollback_output="Rollback SQL executed",
                metrics_before=metrics_before,
                metrics_after=metrics_after,
                duration_seconds=time.time() - start_time,
            )
        except Exception as e:
            raise RollbackError(f"Rollback failed: {e}") from e

    def _check_db_safety(self, conn) -> dict:
        """Quick pre-execution check: is the database writable?

        Catches switchovers (now STANDBY) or read-only mode changes
        that happened after profiling.
        """
        try:
            rows = self._query_runner.execute_read(
                conn,
                "SELECT database_role, open_mode FROM v$database",
                timeout=10,
            )
            if not rows:
                return {"safe": False, "reason": "Cannot query v$database"}
            info = rows[0]
            role = str(info.get("database_role", "")).upper()
            mode = str(info.get("open_mode", "")).upper()
            if "STANDBY" in role:
                return {"safe": False, "reason": f"Database is {role} — cannot write"}
            if mode != "READ WRITE":
                return {"safe": False, "reason": f"Database is {mode} — not writable"}
            return {"safe": True, "reason": ""}
        except Exception as e:
            self.logger.warning("Safety check query failed: %s — proceeding", e)
            return {"safe": True, "reason": ""}

    def _resolve_datafile_path(self, conn, plan: ExecutionPlan) -> str:
        """If forward SQL uses ADD DATAFILE without a path, resolve one from the DB.

        Oracle requires an explicit file path when OMF (db_create_file_dest) is not set.
        We look up an existing datafile's directory for the target tablespace and
        generate a new unique filename in the same location.
        """
        import re as _re

        sql = plan.forward_sql

        # Only applies to ADD DATAFILE without a quoted path
        if not _re.search(r"ADD\s+DATAFILE\s+SIZE", sql, _re.IGNORECASE):
            return sql  # Already has a path or not a datafile operation

        # First check: does Oracle have OMF configured?
        try:
            rows = self._query_runner.execute_read(
                conn,
                "SELECT value FROM v$parameter WHERE name = 'db_create_file_dest'",
                timeout=10,
            )
            if rows and rows[0].get("value"):
                self.logger.info("OMF enabled (db_create_file_dest set) — no path needed")
                return sql
        except Exception:
            pass

        # OMF not set — look up existing datafile directory for this tablespace
        tablespace_name = plan.params.get("tablespace_name", "")
        if not tablespace_name:
            # Try to extract from the SQL itself: ALTER TABLESPACE <name>
            tbs_match = _re.search(r"ALTER\s+TABLESPACE\s+(\S+)", sql, _re.IGNORECASE)
            if tbs_match:
                tablespace_name = tbs_match.group(1)

        if tablespace_name:
            try:
                rows = self._query_runner.execute_read(
                    conn,
                    "SELECT file_name FROM dba_data_files WHERE tablespace_name = :tbs ORDER BY file_id",
                    {"tbs": tablespace_name},
                    timeout=10,
                )
                if rows:
                    existing_path = rows[0]["file_name"]
                    # Extract directory from existing path
                    import os

                    directory = os.path.dirname(existing_path)
                    # Use forward slashes for Oracle (works on all platforms)
                    directory = directory.replace("\\", "/")
                    new_count = len(rows) + 1
                    new_filename = f"{tablespace_name.lower()}_{new_count:02d}.dbf"
                    new_path = f"{directory}/{new_filename}"

                    # Replace ADD DATAFILE SIZE with ADD DATAFILE 'path' SIZE
                    sql = _re.sub(
                        r"ADD\s+DATAFILE\s+SIZE",
                        f"ADD DATAFILE '{new_path}' SIZE",
                        sql,
                        flags=_re.IGNORECASE,
                    )
                    self.logger.info("Resolved datafile path: %s", new_path)
                    return sql
            except Exception as e:
                self.logger.warning("Could not resolve datafile path: %s", e)

        return sql

    def _capture_metrics(self, conn, validation_sql: str, params: dict | None = None) -> dict:
        """Run validation query and return metrics."""
        if not validation_sql.strip():
            return {}
        try:
            results = self._query_runner.execute_read(conn, validation_sql, params)
            return results[0] if results else {}
        except Exception as e:
            self.logger.warning("Metrics capture failed: %s", e)
            return {}

    @staticmethod
    def _validate_execution(before: dict, after: dict) -> bool:
        """Compare before/after metrics to determine if fix worked."""
        if not before or not after:
            return True  # No metrics to compare, assume success

        # For numeric metrics, check they improved (lower is better for usage%)
        for key in after:
            if key in before:
                try:
                    val_before = float(before[key])
                    val_after = float(after[key])
                    if val_after > val_before:
                        return False  # Got worse
                except (ValueError, TypeError):
                    continue
        return True

    def _write_audit(
        self, workflow: Workflow, plan: ExecutionPlan, result: ExecutionResult
    ) -> None:
        """Write an immutable audit record."""
        outcome = ExecutionOutcome.SUCCESS.value
        if result.rolled_back:
            outcome = ExecutionOutcome.ROLLED_BACK.value
        elif not result.success:
            outcome = ExecutionOutcome.FAILED.value

        evidence = json.dumps(
            {
                "metrics_before": result.metrics_before,
                "metrics_after": result.metrics_after,
                "duration_seconds": result.duration_seconds,
            }
        )

        record = AuditRecord(
            workflow_id=workflow.id,
            action_type=plan.action_type,
            action_sql=result.action_sql_executed,
            database_id=workflow.database_id,
            environment=workflow.environment,
            executed_by="agent4_executor",
            approved_by=workflow.approved_by,
            result=outcome,
            error_message=result.error_message,
            evidence=evidence,
        )
        self.context.audit_repo.create(record)

    def _acquire_lock(self, resource_key: str, workflow_id: str) -> None:
        """Acquire a resource lock in SQLite."""
        expires = (datetime.now(timezone.utc) + timedelta(seconds=LOCK_EXPIRY)).isoformat()

        # Clean stale locks first
        self.context.db.execute_write(
            "DELETE FROM locks WHERE expires_at < ?",
            (datetime.now(timezone.utc).isoformat(),),
        )

        try:
            self.context.db.execute_write(
                "INSERT INTO locks (resource_key, workflow_id, expires_at) VALUES (?, ?, ?)",
                (resource_key, workflow_id, expires),
            )
            self.logger.debug("Acquired lock: %s", resource_key)
        except Exception:
            raise LockAcquisitionError(f"Resource {resource_key} is locked by another workflow")

    def _release_lock(self, resource_key: str) -> None:
        """Release a resource lock."""
        try:
            self.context.db.execute_write(
                "DELETE FROM locks WHERE resource_key = ?",
                (resource_key,),
            )
            self.logger.debug("Released lock: %s", resource_key)
        except Exception as e:
            self.logger.warning("Failed to release lock %s: %s", resource_key, e)
