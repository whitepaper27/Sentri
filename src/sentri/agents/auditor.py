"""Agent 2: The Auditor - Verification agent that validates alerts are real."""

from __future__ import annotations

import json
from typing import Optional

from sentri.core.constants import WorkflowStatus
from sentri.core.exceptions import OracleConnectionError
from sentri.core.models import Suggestion, VerificationReport, Workflow
from sentri.oracle.connection_pool import OracleConnectionPool
from sentri.oracle.query_runner import QueryRunner
from sentri.policy.alert_patterns import AlertPatterns

from .base import AgentContext, BaseAgent


class AuditorAgent(BaseAgent):
    """Verify alerts by connecting to the target Oracle database (read-only)."""

    def __init__(self, context: AgentContext, oracle_pool: Optional[OracleConnectionPool] = None):
        super().__init__("auditor", context)
        self._oracle_pool = oracle_pool or OracleConnectionPool()
        self._query_runner = QueryRunner(timeout_seconds=30)
        self._patterns = AlertPatterns(context.policy_loader)

    def process(self, workflow_id: str) -> dict:
        """Verify a workflow's alert against the live database.

        Returns {"status": "verified"|"failed", "report": VerificationReport}
        """
        workflow = self.context.workflow_repo.get(workflow_id)
        if not workflow:
            return {"status": "failure", "error": f"Workflow {workflow_id} not found"}

        try:
            report = self._verify(workflow)

            # Save verification result
            self.context.workflow_repo.update_status(
                workflow_id,
                WorkflowStatus.VERIFIED.value
                if report.is_valid
                else WorkflowStatus.VERIFICATION_FAILED.value,
                verification=report.to_json(),
            )

            self.logger.info(
                "Verification %s for workflow %s: confidence=%.2f",
                "PASSED" if report.is_valid else "FAILED",
                workflow_id,
                report.confidence,
            )

            return {
                "status": "verified" if report.is_valid else "failed",
                "report": report,
            }

        except Exception as e:
            self.logger.error("Verification error for %s: %s", workflow_id, e)
            self.context.workflow_repo.update_status(
                workflow_id,
                WorkflowStatus.VERIFICATION_FAILED.value,
                verification=json.dumps({"error": str(e)}),
            )
            return {"status": "failure", "error": str(e)}

    def _verify(self, workflow: Workflow) -> VerificationReport:
        """Run verification against the target database."""
        suggestion = Suggestion.from_json(workflow.suggestion)
        checks_passed = []
        checks_failed = []
        actual_metrics = {}
        reported_metrics = suggestion.extracted_data.copy()

        # 1. Check for duplicate active workflows
        duplicates = self.context.workflow_repo.find_duplicates(
            workflow.database_id, workflow.alert_type
        )
        # Exclude current workflow
        duplicates = [d for d in duplicates if d.id != workflow.id]
        is_duplicate = len(duplicates) > 0

        if is_duplicate:
            checks_failed.append(f"Duplicate workflow detected: {[d.id for d in duplicates]}")
        else:
            checks_passed.append("No duplicate workflows found")

        # 2. Connect to target database and run verification query
        verify_sql = self._patterns.get_verification_query(workflow.alert_type)
        self.logger.info(
            "[%s] Verification SQL for %s on %s:\n  %s",
            workflow.id[:8],
            workflow.alert_type,
            workflow.database_id,
            verify_sql or "(none)",
        )
        self.logger.info(
            "[%s] Bind params from email: %s",
            workflow.id[:8],
            reported_metrics,
        )

        try:
            actual_metrics = self._run_verification_query(workflow, suggestion)
            checks_passed.append("Successfully queried target database")
            self.logger.info(
                "[%s] Query returned: %s",
                workflow.id[:8],
                actual_metrics,
            )
        except OracleConnectionError as e:
            checks_failed.append(f"Cannot connect to database: {e}")
            self.logger.warning(
                "[%s] DB connection failed: %s",
                workflow.id[:8],
                e,
            )
            return VerificationReport(
                is_valid=False,
                confidence=0.0,
                actual_metrics=actual_metrics,
                reported_metrics=reported_metrics,
                duplicate_check=not is_duplicate,
                checks_passed=checks_passed,
                checks_failed=checks_failed,
            )

        # 3. Compare actual vs reported metrics
        tolerance = self._patterns.get_tolerance(workflow.alert_type)
        self.logger.info(
            "[%s] Comparing metrics — reported: %s, actual: %s, tolerance: %s",
            workflow.id[:8],
            reported_metrics,
            actual_metrics,
            tolerance,
        )
        metrics_match = self._compare_metrics(actual_metrics, reported_metrics, tolerance)
        if metrics_match:
            checks_passed.append("Actual metrics match reported values within tolerance")
        else:
            checks_failed.append("Actual metrics differ from reported values")

        # Calculate confidence
        total_checks = len(checks_passed) + len(checks_failed)
        confidence = len(checks_passed) / total_checks if total_checks > 0 else 0.0
        is_valid = len(checks_failed) == 0

        # Log detailed verdict
        self.logger.info(
            "[%s] Verification verdict: valid=%s, confidence=%.2f, passed=[%s], failed=[%s]",
            workflow.id[:8],
            is_valid,
            confidence,
            "; ".join(checks_passed),
            "; ".join(checks_failed) if checks_failed else "none",
        )

        return VerificationReport(
            is_valid=is_valid,
            confidence=confidence,
            actual_metrics=actual_metrics,
            reported_metrics=reported_metrics,
            duplicate_check=not is_duplicate,
            checks_passed=checks_passed,
            checks_failed=checks_failed,
        )

    def _run_verification_query(self, workflow: Workflow, suggestion: Suggestion) -> dict:
        """Execute the verification SQL from the alert policy."""
        verify_sql = self._patterns.get_verification_query(workflow.alert_type)
        if not verify_sql:
            self.logger.warning("No verification query for %s", workflow.alert_type)
            return {}

        # Find DB config from settings (credentials) and registry (metadata)
        db_cfg = self.context.settings.get_database(workflow.database_id)
        env_record = self.context.environment_repo.get(workflow.database_id)

        # Need at least one source for connection_string
        conn_string = ""
        if env_record:
            conn_string = env_record.connection_string
        elif db_cfg:
            conn_string = db_cfg.connection_string
        else:
            raise OracleConnectionError(f"No config for database {workflow.database_id}")

        conn = self._oracle_pool.get_connection(
            database_id=workflow.database_id,
            connection_string=conn_string,
            password=db_cfg.password if db_cfg else "",
            username=db_cfg.username if db_cfg and db_cfg.username else None,
            read_only=True,
        )

        try:
            results = self._query_runner.execute_read(conn, verify_sql, suggestion.extracted_data)
            return results[0] if results else {}
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def _compare_metrics(self, actual: dict, reported: dict, tolerance: dict) -> bool:
        """Verify the alert is still real by comparing actual vs reported metrics.

        Logic: alert is confirmed if actual is as bad or worse than reported.
        Only fails if actual is significantly BETTER (problem went away).
        e.g. email says 12% full, actual 48% → PASS (problem is worse)
             email says 92% full, actual 10% → FAIL (problem resolved)
        """
        if not actual:
            self.logger.warning("Metrics comparison: no actual data returned from DB")
            return False

        import re

        for key, tol_str in tolerance.items():
            if key not in actual or key not in reported:
                self.logger.info(
                    "Metrics comparison: skipping key '%s' (actual=%s, reported=%s)",
                    key,
                    key in actual,
                    key in reported,
                )
                continue

            try:
                actual_val = float(actual[key])
                reported_val = float(reported[key])
            except (ValueError, TypeError) as e:
                self.logger.warning(
                    "Metrics comparison: cannot convert '%s' — actual=%r, reported=%r: %s",
                    key,
                    actual.get(key),
                    reported.get(key),
                    e,
                )
                continue

            # Parse tolerance like "+/- 2%" or "± 2%"
            tol_val = 2.0  # default
            tol_match = re.search(r"(\d+(?:\.\d+)?)", tol_str)
            if tol_match:
                tol_val = float(tol_match.group(1))

            diff = reported_val - actual_val

            # One-directional check: fail only if actual is significantly
            # better than reported (the problem went away since the alert)
            if diff > tol_val:
                self.logger.info(
                    "Metrics MISMATCH on '%s': reported=%.2f, actual=%.2f, "
                    "diff=%.2f > tolerance=%.2f — problem appears resolved",
                    key,
                    reported_val,
                    actual_val,
                    diff,
                    tol_val,
                )
                return False

            self.logger.info(
                "Metrics OK on '%s': reported=%.2f, actual=%.2f, diff=%.2f within tolerance=%.2f",
                key,
                reported_val,
                actual_val,
                diff,
                tol_val,
            )

        return True
