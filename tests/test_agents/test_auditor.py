"""Comprehensive unit tests for the AuditorAgent (Agent 2).

Tests verification logic, confidence scoring, duplicate detection,
metric comparison, connection failure handling, and status transitions.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from sentri.agents.auditor import AuditorAgent
from sentri.core.constants import WorkflowStatus
from sentri.core.exceptions import OracleConnectionError, VerificationTimeoutError
from sentri.core.models import Suggestion, VerificationReport, Workflow

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_suggestion(
    alert_type: str = "tablespace_full",
    database_id: str = "DEV-DB-01",
    extracted: dict | None = None,
) -> Suggestion:
    """Build a Suggestion with sensible defaults for tablespace_full."""
    return Suggestion(
        alert_type=alert_type,
        database_id=database_id,
        raw_email_subject=f"ALERT: Tablespace USERS 92% full on {database_id}",
        raw_email_body="Tablespace usage exceeded threshold.",
        extracted_data=extracted or {"tablespace_name": "USERS", "used_percent": "92"},
    )


def _create_workflow(
    agent_context,
    alert_type: str = "tablespace_full",
    database_id: str = "DEV-DB-01",
    environment: str = "DEV",
    status: str = "DETECTED",
    extracted: dict | None = None,
) -> Workflow:
    """Create and persist a workflow, returning the Workflow object."""
    suggestion = _make_suggestion(alert_type, database_id, extracted)
    wf = Workflow(
        alert_type=alert_type,
        database_id=database_id,
        environment=environment,
        status=status,
        suggestion=suggestion.to_json(),
    )
    agent_context.workflow_repo.create(wf)
    return wf


def _build_auditor(agent_context, oracle_pool=None) -> AuditorAgent:
    """Build an AuditorAgent with a mocked OracleConnectionPool."""
    pool = oracle_pool or MagicMock()
    return AuditorAgent(context=agent_context, oracle_pool=pool)


# ---------------------------------------------------------------------------
# TestAuditorProcess — main entry point
# ---------------------------------------------------------------------------


class TestAuditorProcess:
    """Tests for AuditorAgent.process(workflow_id)."""

    def test_workflow_not_found(self, agent_context):
        """process() with a nonexistent workflow_id returns failure."""
        auditor = _build_auditor(agent_context)
        result = auditor.process("nonexistent-workflow-id")

        assert result["status"] == "failure"
        assert "not found" in result["error"]

    @patch("sentri.agents.auditor.QueryRunner")
    def test_verify_successful(self, MockQueryRunner, agent_context):
        """Verification passes when query returns matching metrics."""
        wf = _create_workflow(agent_context)

        mock_runner = MagicMock()
        # Return data matching the reported 92% — actual is 93% (within +/- 2 tolerance)
        mock_runner.execute_read.return_value = [{"tablespace_name": "USERS", "used_percent": 93}]
        MockQueryRunner.return_value = mock_runner

        mock_pool = MagicMock()
        mock_conn = MagicMock()
        mock_pool.get_connection.return_value = mock_conn

        auditor = _build_auditor(agent_context, oracle_pool=mock_pool)
        result = auditor.process(wf.id)

        assert result["status"] == "verified"
        report = result["report"]
        assert isinstance(report, VerificationReport)
        assert report.is_valid is True
        assert report.confidence > 0

    @patch("sentri.agents.auditor.QueryRunner")
    def test_verify_fails_below_tolerance(self, MockQueryRunner, agent_context):
        """Verification fails when actual metric is far better than reported.

        reported=92, actual=10 → diff = 92 - 10 = 82 > tolerance(2) → FAIL
        """
        wf = _create_workflow(agent_context)

        mock_runner = MagicMock()
        mock_runner.execute_read.return_value = [{"tablespace_name": "USERS", "used_percent": 10}]
        MockQueryRunner.return_value = mock_runner

        mock_pool = MagicMock()
        mock_conn = MagicMock()
        mock_pool.get_connection.return_value = mock_conn

        auditor = _build_auditor(agent_context, oracle_pool=mock_pool)
        result = auditor.process(wf.id)

        assert result["status"] == "failed"
        report = result["report"]
        assert report.is_valid is False

    @patch("sentri.agents.auditor.QueryRunner")
    def test_connection_failure(self, MockQueryRunner, agent_context):
        """OracleConnectionError during get_connection is handled gracefully."""
        wf = _create_workflow(agent_context)

        MockQueryRunner.return_value = MagicMock()

        mock_pool = MagicMock()
        mock_pool.get_connection.side_effect = OracleConnectionError("Cannot reach DEV-DB-01")

        auditor = _build_auditor(agent_context, oracle_pool=mock_pool)
        result = auditor.process(wf.id)

        assert result["status"] == "failed"
        report = result["report"]
        assert report.is_valid is False
        assert report.confidence == 0.0
        assert any("Cannot connect" in f for f in report.checks_failed)

    @patch("sentri.agents.auditor.QueryRunner")
    def test_query_timeout(self, MockQueryRunner, agent_context):
        """VerificationTimeoutError from QueryRunner is caught by top-level handler."""
        wf = _create_workflow(agent_context)

        mock_runner = MagicMock()
        mock_runner.execute_read.side_effect = VerificationTimeoutError("Query timed out after 30s")
        MockQueryRunner.return_value = mock_runner

        mock_pool = MagicMock()
        mock_conn = MagicMock()
        mock_pool.get_connection.return_value = mock_conn

        auditor = _build_auditor(agent_context, oracle_pool=mock_pool)
        result = auditor.process(wf.id)

        # VerificationTimeoutError is NOT OracleConnectionError, so it falls
        # through to the generic except in process(), which sets status=failure.
        assert result["status"] == "failure"
        assert "timed out" in result["error"]

    @patch("sentri.agents.auditor.QueryRunner")
    def test_empty_query_result(self, MockQueryRunner, agent_context):
        """When verification query returns no rows, verification fails."""
        wf = _create_workflow(agent_context)

        mock_runner = MagicMock()
        mock_runner.execute_read.return_value = []  # No rows
        MockQueryRunner.return_value = mock_runner

        mock_pool = MagicMock()
        mock_conn = MagicMock()
        mock_pool.get_connection.return_value = mock_conn

        auditor = _build_auditor(agent_context, oracle_pool=mock_pool)
        result = auditor.process(wf.id)

        # _run_verification_query returns {} when results empty → _compare_metrics
        # returns False (no actual data) → checks_failed includes metric mismatch
        assert result["status"] == "failed"
        report = result["report"]
        assert report.is_valid is False

    @patch("sentri.agents.auditor.QueryRunner")
    def test_process_returns_report_in_result(self, MockQueryRunner, agent_context):
        """The returned dict contains a VerificationReport under 'report' key."""
        wf = _create_workflow(agent_context)

        mock_runner = MagicMock()
        mock_runner.execute_read.return_value = [{"tablespace_name": "USERS", "used_percent": 94}]
        MockQueryRunner.return_value = mock_runner

        mock_pool = MagicMock()
        mock_pool.get_connection.return_value = MagicMock()

        auditor = _build_auditor(agent_context, oracle_pool=mock_pool)
        result = auditor.process(wf.id)

        assert "report" in result
        assert isinstance(result["report"], VerificationReport)


# ---------------------------------------------------------------------------
# TestAuditorStatus — workflow status transitions
# ---------------------------------------------------------------------------


class TestAuditorStatus:
    """Tests that workflow status is updated correctly after verification."""

    @patch("sentri.agents.auditor.QueryRunner")
    def test_verified_status_on_success(self, MockQueryRunner, agent_context):
        """On successful verification, workflow status becomes VERIFIED."""
        wf = _create_workflow(agent_context)

        mock_runner = MagicMock()
        mock_runner.execute_read.return_value = [{"tablespace_name": "USERS", "used_percent": 93}]
        MockQueryRunner.return_value = mock_runner

        mock_pool = MagicMock()
        mock_pool.get_connection.return_value = MagicMock()

        auditor = _build_auditor(agent_context, oracle_pool=mock_pool)
        auditor.process(wf.id)

        updated = agent_context.workflow_repo.get(wf.id)
        assert updated.status == WorkflowStatus.VERIFIED.value

    @patch("sentri.agents.auditor.QueryRunner")
    def test_verification_failed_status_on_mismatch(self, MockQueryRunner, agent_context):
        """When metrics don't match, workflow status becomes VERIFICATION_FAILED."""
        wf = _create_workflow(agent_context)

        mock_runner = MagicMock()
        # Actual 10% vs reported 92% — problem resolved → FAIL
        mock_runner.execute_read.return_value = [{"tablespace_name": "USERS", "used_percent": 10}]
        MockQueryRunner.return_value = mock_runner

        mock_pool = MagicMock()
        mock_pool.get_connection.return_value = MagicMock()

        auditor = _build_auditor(agent_context, oracle_pool=mock_pool)
        auditor.process(wf.id)

        updated = agent_context.workflow_repo.get(wf.id)
        assert updated.status == WorkflowStatus.VERIFICATION_FAILED.value

    @patch("sentri.agents.auditor.QueryRunner")
    def test_verification_failed_status_on_connection_error(self, MockQueryRunner, agent_context):
        """OracleConnectionError leads to VERIFICATION_FAILED status."""
        wf = _create_workflow(agent_context)
        MockQueryRunner.return_value = MagicMock()

        mock_pool = MagicMock()
        mock_pool.get_connection.side_effect = OracleConnectionError("Unreachable")

        auditor = _build_auditor(agent_context, oracle_pool=mock_pool)
        auditor.process(wf.id)

        updated = agent_context.workflow_repo.get(wf.id)
        assert updated.status == WorkflowStatus.VERIFICATION_FAILED.value

    @patch("sentri.agents.auditor.QueryRunner")
    def test_verification_failed_status_on_generic_exception(self, MockQueryRunner, agent_context):
        """A generic exception in process() also leads to VERIFICATION_FAILED status."""
        wf = _create_workflow(agent_context)

        mock_runner = MagicMock()
        mock_runner.execute_read.side_effect = RuntimeError("Unexpected DB error")
        MockQueryRunner.return_value = mock_runner

        mock_pool = MagicMock()
        mock_pool.get_connection.return_value = MagicMock()

        auditor = _build_auditor(agent_context, oracle_pool=mock_pool)
        auditor.process(wf.id)

        updated = agent_context.workflow_repo.get(wf.id)
        assert updated.status == WorkflowStatus.VERIFICATION_FAILED.value

    @patch("sentri.agents.auditor.QueryRunner")
    def test_verification_json_stored_in_workflow(self, MockQueryRunner, agent_context):
        """Verification JSON is stored in the workflow record after process()."""
        wf = _create_workflow(agent_context)

        mock_runner = MagicMock()
        mock_runner.execute_read.return_value = [{"tablespace_name": "USERS", "used_percent": 93}]
        MockQueryRunner.return_value = mock_runner

        mock_pool = MagicMock()
        mock_pool.get_connection.return_value = MagicMock()

        auditor = _build_auditor(agent_context, oracle_pool=mock_pool)
        auditor.process(wf.id)

        updated = agent_context.workflow_repo.get(wf.id)
        assert updated.verification is not None
        verification_data = json.loads(updated.verification)
        assert "is_valid" in verification_data
        assert "confidence" in verification_data
        assert "checks_passed" in verification_data

    @patch("sentri.agents.auditor.QueryRunner")
    def test_error_json_stored_on_exception(self, MockQueryRunner, agent_context):
        """When process() catches a generic exception, error JSON is stored."""
        wf = _create_workflow(agent_context)

        mock_runner = MagicMock()
        mock_runner.execute_read.side_effect = RuntimeError("kaboom")
        MockQueryRunner.return_value = mock_runner

        mock_pool = MagicMock()
        mock_pool.get_connection.return_value = MagicMock()

        auditor = _build_auditor(agent_context, oracle_pool=mock_pool)
        auditor.process(wf.id)

        updated = agent_context.workflow_repo.get(wf.id)
        assert updated.verification is not None
        verification_data = json.loads(updated.verification)
        assert "error" in verification_data
        assert "kaboom" in verification_data["error"]


# ---------------------------------------------------------------------------
# TestAuditorConfidence — confidence score computation
# ---------------------------------------------------------------------------


class TestAuditorConfidence:
    """Tests for confidence score calculation."""

    @patch("sentri.agents.auditor.QueryRunner")
    def test_high_confidence_when_match_no_duplicates(self, MockQueryRunner, agent_context):
        """Full match + no duplicates → confidence = 1.0 (3 passed / 3 total).

        Checks: no duplicates (pass), DB query (pass), metrics match (pass).
        """
        wf = _create_workflow(agent_context)

        mock_runner = MagicMock()
        mock_runner.execute_read.return_value = [{"tablespace_name": "USERS", "used_percent": 93}]
        MockQueryRunner.return_value = mock_runner

        mock_pool = MagicMock()
        mock_pool.get_connection.return_value = MagicMock()

        auditor = _build_auditor(agent_context, oracle_pool=mock_pool)
        result = auditor.process(wf.id)

        report = result["report"]
        assert report.confidence == 1.0
        assert len(report.checks_passed) == 3
        assert len(report.checks_failed) == 0

    @patch("sentri.agents.auditor.QueryRunner")
    def test_lower_confidence_when_partial_match(self, MockQueryRunner, agent_context):
        """Metrics mismatch with no duplicates → confidence = 2/3.

        Checks: no duplicates (pass), DB query (pass), metrics match (fail).
        """
        wf = _create_workflow(agent_context)

        mock_runner = MagicMock()
        # Actual 10% vs reported 92% — problem resolved → metric check fails
        mock_runner.execute_read.return_value = [{"tablespace_name": "USERS", "used_percent": 10}]
        MockQueryRunner.return_value = mock_runner

        mock_pool = MagicMock()
        mock_pool.get_connection.return_value = MagicMock()

        auditor = _build_auditor(agent_context, oracle_pool=mock_pool)
        result = auditor.process(wf.id)

        report = result["report"]
        # 2 passed (no dup + queried DB), 1 failed (metrics mismatch) = 2/3
        assert report.confidence == pytest.approx(2 / 3, abs=0.01)
        assert len(report.checks_passed) == 2
        assert len(report.checks_failed) == 1

    @patch("sentri.agents.auditor.QueryRunner")
    def test_zero_confidence_on_connection_failure(self, MockQueryRunner, agent_context):
        """Connection failure → confidence = 0.0 (early return)."""
        wf = _create_workflow(agent_context)
        MockQueryRunner.return_value = MagicMock()

        mock_pool = MagicMock()
        mock_pool.get_connection.side_effect = OracleConnectionError("Unreachable")

        auditor = _build_auditor(agent_context, oracle_pool=mock_pool)
        result = auditor.process(wf.id)

        report = result["report"]
        assert report.confidence == 0.0

    @patch("sentri.agents.auditor.QueryRunner")
    def test_confidence_with_duplicate_and_metric_match(self, MockQueryRunner, agent_context):
        """Duplicate detected but metrics match → confidence = 2/3.

        Checks: no duplicates (fail), DB query (pass), metrics match (pass).
        """
        # Create a duplicate workflow in active state
        _create_workflow(
            agent_context,
            alert_type="tablespace_full",
            database_id="DEV-DB-01",
            status="VERIFIED",
        )
        # Create the workflow under test
        wf = _create_workflow(agent_context)

        mock_runner = MagicMock()
        mock_runner.execute_read.return_value = [{"tablespace_name": "USERS", "used_percent": 93}]
        MockQueryRunner.return_value = mock_runner

        mock_pool = MagicMock()
        mock_pool.get_connection.return_value = MagicMock()

        auditor = _build_auditor(agent_context, oracle_pool=mock_pool)
        result = auditor.process(wf.id)

        report = result["report"]
        # 2 passed (DB query + metrics), 1 failed (duplicate) = 2/3
        assert report.confidence == pytest.approx(2 / 3, abs=0.01)
        assert report.is_valid is False  # Any failed check makes is_valid False
        assert report.duplicate_check is False  # duplicate_check = not is_duplicate


# ---------------------------------------------------------------------------
# TestAuditorDuplicates — duplicate workflow detection
# ---------------------------------------------------------------------------


class TestAuditorDuplicates:
    """Tests for duplicate workflow detection."""

    @patch("sentri.agents.auditor.QueryRunner")
    def test_duplicate_detection(self, MockQueryRunner, agent_context):
        """A recent active workflow with same alert+db is flagged as duplicate."""
        # Create a prior active workflow (same alert_type, same database_id)
        _create_workflow(
            agent_context,
            alert_type="tablespace_full",
            database_id="DEV-DB-01",
            status="EXECUTING",
        )

        # Create workflow under test
        wf = _create_workflow(agent_context)

        mock_runner = MagicMock()
        mock_runner.execute_read.return_value = [{"tablespace_name": "USERS", "used_percent": 93}]
        MockQueryRunner.return_value = mock_runner

        mock_pool = MagicMock()
        mock_pool.get_connection.return_value = MagicMock()

        auditor = _build_auditor(agent_context, oracle_pool=mock_pool)
        result = auditor.process(wf.id)

        report = result["report"]
        assert report.duplicate_check is False  # True = no duplicates, False = dup found
        assert any("Duplicate" in f for f in report.checks_failed)
        assert report.is_valid is False

    @patch("sentri.agents.auditor.QueryRunner")
    def test_no_duplicate_when_different_database(self, MockQueryRunner, agent_context):
        """Workflows on different databases are NOT considered duplicates."""
        _create_workflow(
            agent_context,
            alert_type="tablespace_full",
            database_id="PROD-DB-07",
            environment="PROD",
            status="EXECUTING",
        )

        wf = _create_workflow(
            agent_context,
            alert_type="tablespace_full",
            database_id="DEV-DB-01",
        )

        mock_runner = MagicMock()
        mock_runner.execute_read.return_value = [{"tablespace_name": "USERS", "used_percent": 93}]
        MockQueryRunner.return_value = mock_runner

        mock_pool = MagicMock()
        mock_pool.get_connection.return_value = MagicMock()

        auditor = _build_auditor(agent_context, oracle_pool=mock_pool)
        result = auditor.process(wf.id)

        report = result["report"]
        assert report.duplicate_check is True  # No duplicates found

    @patch("sentri.agents.auditor.QueryRunner")
    def test_no_duplicate_when_different_alert_type(self, MockQueryRunner, agent_context):
        """Workflows with different alert_type on the same DB are NOT duplicates."""
        _create_workflow(
            agent_context,
            alert_type="temp_full",
            database_id="DEV-DB-01",
            status="EXECUTING",
            extracted={"tablespace_name": "TEMP", "used_percent": "88"},
        )

        wf = _create_workflow(
            agent_context,
            alert_type="tablespace_full",
            database_id="DEV-DB-01",
        )

        mock_runner = MagicMock()
        mock_runner.execute_read.return_value = [{"tablespace_name": "USERS", "used_percent": 93}]
        MockQueryRunner.return_value = mock_runner

        mock_pool = MagicMock()
        mock_pool.get_connection.return_value = MagicMock()

        auditor = _build_auditor(agent_context, oracle_pool=mock_pool)
        result = auditor.process(wf.id)

        report = result["report"]
        assert report.duplicate_check is True  # Different alert type → not a duplicate

    @patch("sentri.agents.auditor.QueryRunner")
    def test_completed_workflow_not_duplicate(self, MockQueryRunner, agent_context):
        """Completed workflows are NOT included in the duplicate check.

        find_duplicates only looks at active statuses (DETECTED, VERIFYING, etc.).
        """
        _create_workflow(
            agent_context,
            alert_type="tablespace_full",
            database_id="DEV-DB-01",
            status="COMPLETED",
        )

        wf = _create_workflow(agent_context)

        mock_runner = MagicMock()
        mock_runner.execute_read.return_value = [{"tablespace_name": "USERS", "used_percent": 93}]
        MockQueryRunner.return_value = mock_runner

        mock_pool = MagicMock()
        mock_pool.get_connection.return_value = MagicMock()

        auditor = _build_auditor(agent_context, oracle_pool=mock_pool)
        result = auditor.process(wf.id)

        report = result["report"]
        assert report.duplicate_check is True  # COMPLETED is not an active status


# ---------------------------------------------------------------------------
# TestVerificationReport — report dataclass behavior
# ---------------------------------------------------------------------------


class TestVerificationReport:
    """Tests for the VerificationReport dataclass itself."""

    def test_report_fields_populated(self, agent_context):
        """VerificationReport has all expected fields after construction."""
        report = VerificationReport(
            is_valid=True,
            confidence=0.95,
            actual_metrics={"used_percent": 93},
            reported_metrics={"used_percent": "92"},
            duplicate_check=True,
            checks_passed=["No duplicates", "Query OK", "Metrics match"],
            checks_failed=[],
        )

        assert report.is_valid is True
        assert report.confidence == 0.95
        assert report.actual_metrics == {"used_percent": 93}
        assert report.reported_metrics == {"used_percent": "92"}
        assert report.duplicate_check is True
        assert len(report.checks_passed) == 3
        assert len(report.checks_failed) == 0
        assert report.verified_at is not None  # Auto-populated

    def test_report_to_json_roundtrip(self, agent_context):
        """VerificationReport serializes to JSON and deserializes back."""
        report = VerificationReport(
            is_valid=False,
            confidence=0.67,
            actual_metrics={"used_percent": 10},
            reported_metrics={"used_percent": "92"},
            duplicate_check=True,
            checks_passed=["No duplicates", "Query OK"],
            checks_failed=["Metrics mismatch"],
        )

        json_str = report.to_json()
        restored = VerificationReport.from_json(json_str)

        assert restored.is_valid == report.is_valid
        assert restored.confidence == report.confidence
        assert restored.actual_metrics == report.actual_metrics
        assert restored.reported_metrics == report.reported_metrics
        assert restored.duplicate_check == report.duplicate_check
        assert restored.checks_passed == report.checks_passed
        assert restored.checks_failed == report.checks_failed

    def test_report_json_is_valid_json(self, agent_context):
        """to_json() produces valid JSON parseable by json.loads()."""
        report = VerificationReport(
            is_valid=True,
            confidence=1.0,
            actual_metrics={},
            reported_metrics={},
            duplicate_check=True,
            checks_passed=["OK"],
            checks_failed=[],
        )

        parsed = json.loads(report.to_json())
        assert parsed["is_valid"] is True
        assert parsed["confidence"] == 1.0


# ---------------------------------------------------------------------------
# TestCompareMetrics — _compare_metrics logic
# ---------------------------------------------------------------------------


class TestCompareMetrics:
    """Tests for the _compare_metrics private method.

    Metric comparison is one-directional: it only fails when actual is
    significantly BETTER than reported (the problem went away).
    """

    def _get_auditor(self, agent_context):
        """Build auditor for testing _compare_metrics directly."""
        return _build_auditor(agent_context)

    def test_match_within_tolerance(self, agent_context):
        """actual close to reported → True (problem still exists)."""
        auditor = self._get_auditor(agent_context)
        result = auditor._compare_metrics(
            actual={"used_percent": 93},
            reported={"used_percent": 92},
            tolerance={"used_percent": "+/- 2%"},
        )
        assert result is True

    def test_actual_worse_than_reported(self, agent_context):
        """actual > reported → True (problem is worse, still valid alert)."""
        auditor = self._get_auditor(agent_context)
        result = auditor._compare_metrics(
            actual={"used_percent": 97},
            reported={"used_percent": 92},
            tolerance={"used_percent": "+/- 2%"},
        )
        # diff = 92 - 97 = -5, not > 2 → passes
        assert result is True

    def test_actual_much_better_than_reported(self, agent_context):
        """actual much better than reported → False (problem resolved)."""
        auditor = self._get_auditor(agent_context)
        result = auditor._compare_metrics(
            actual={"used_percent": 10},
            reported={"used_percent": 92},
            tolerance={"used_percent": "+/- 2%"},
        )
        # diff = 92 - 10 = 82, 82 > 2 → fails
        assert result is False

    def test_no_actual_data(self, agent_context):
        """Empty actual dict → False."""
        auditor = self._get_auditor(agent_context)
        result = auditor._compare_metrics(
            actual={},
            reported={"used_percent": 92},
            tolerance={"used_percent": "+/- 2%"},
        )
        assert result is False

    def test_tolerance_boundary_exact(self, agent_context):
        """diff exactly equal to tolerance → passes (not strictly greater)."""
        auditor = self._get_auditor(agent_context)
        # diff = 92 - 90 = 2, tolerance = 2, 2 > 2 is False → passes
        result = auditor._compare_metrics(
            actual={"used_percent": 90},
            reported={"used_percent": 92},
            tolerance={"used_percent": "+/- 2%"},
        )
        assert result is True

    def test_tolerance_boundary_just_over(self, agent_context):
        """diff just over tolerance → fails."""
        auditor = self._get_auditor(agent_context)
        # diff = 92 - 89.9 = 2.1, tolerance = 2, 2.1 > 2 → fails
        result = auditor._compare_metrics(
            actual={"used_percent": 89.9},
            reported={"used_percent": 92},
            tolerance={"used_percent": "+/- 2%"},
        )
        assert result is False

    def test_missing_key_in_actual_skipped(self, agent_context):
        """Key in tolerance but not in actual is skipped (returns True)."""
        auditor = self._get_auditor(agent_context)
        result = auditor._compare_metrics(
            actual={"other_metric": 50},
            reported={"used_percent": 92},
            tolerance={"used_percent": "+/- 2%"},
        )
        # Key used_percent not in actual → skipped. No failures → True
        assert result is True

    def test_missing_key_in_reported_skipped(self, agent_context):
        """Key in tolerance but not in reported is skipped (returns True)."""
        auditor = self._get_auditor(agent_context)
        result = auditor._compare_metrics(
            actual={"used_percent": 93},
            reported={"other_key": "foo"},
            tolerance={"used_percent": "+/- 2%"},
        )
        # Key used_percent not in reported → skipped. No failures → True
        assert result is True

    def test_non_numeric_values_skipped(self, agent_context):
        """Non-numeric metric values are skipped (logged as warning)."""
        auditor = self._get_auditor(agent_context)
        result = auditor._compare_metrics(
            actual={"used_percent": "not_a_number"},
            reported={"used_percent": "92"},
            tolerance={"used_percent": "+/- 2%"},
        )
        # Cannot convert → skipped. No failures → True
        assert result is True

    def test_tolerance_with_unicode_plus_minus(self, agent_context):
        """Tolerance string using unicode +/- sign is parsed correctly."""
        auditor = self._get_auditor(agent_context)
        # Tolerance uses the +/- 5 format (with digits)
        result = auditor._compare_metrics(
            actual={"used_percent": 85},
            reported={"used_percent": 92},
            tolerance={"used_percent": "\u00b1 5%"},  # ± 5%
        )
        # diff = 92 - 85 = 7, tolerance = 5, 7 > 5 → fails
        assert result is False

    def test_tolerance_default_when_no_number(self, agent_context):
        """If tolerance string has no number, default 2.0 is used."""
        auditor = self._get_auditor(agent_context)
        result = auditor._compare_metrics(
            actual={"used_percent": 88},
            reported={"used_percent": 92},
            tolerance={"used_percent": "within tolerance"},
        )
        # diff = 92 - 88 = 4, default tolerance = 2.0, 4 > 2 → fails
        assert result is False

    def test_multiple_metrics_all_pass(self, agent_context):
        """All metrics within tolerance → True."""
        auditor = self._get_auditor(agent_context)
        result = auditor._compare_metrics(
            actual={"used_percent": 93, "free_mb": 500},
            reported={"used_percent": 92, "free_mb": 510},
            tolerance={"used_percent": "+/- 2%", "free_mb": "+/- 20"},
        )
        assert result is True

    def test_multiple_metrics_one_fails(self, agent_context):
        """One metric out of tolerance → False even if others pass."""
        auditor = self._get_auditor(agent_context)
        result = auditor._compare_metrics(
            actual={"used_percent": 10, "free_mb": 500},
            reported={"used_percent": 92, "free_mb": 510},
            tolerance={"used_percent": "+/- 2%", "free_mb": "+/- 20"},
        )
        assert result is False


# ---------------------------------------------------------------------------
# TestRunVerificationQuery — _run_verification_query internal logic
# ---------------------------------------------------------------------------


class TestRunVerificationQuery:
    """Tests for _run_verification_query: DB config lookup, connection, query execution."""

    @patch("sentri.agents.auditor.QueryRunner")
    def test_no_verification_query_returns_empty(self, MockQueryRunner, agent_context):
        """Alert type with no verification_query → returns empty dict."""
        # Use a workflow with an alert type that has no verification query defined
        wf = _create_workflow(
            agent_context,
            alert_type="nonexistent_alert_type",
            extracted={"field": "value"},
        )

        mock_runner = MagicMock()
        MockQueryRunner.return_value = mock_runner

        mock_pool = MagicMock()
        auditor = _build_auditor(agent_context, oracle_pool=mock_pool)

        suggestion = Suggestion.from_json(wf.suggestion)
        result = auditor._run_verification_query(wf, suggestion)

        assert result == {}
        # QueryRunner.execute_read should NOT be called when no verify_sql
        mock_runner.execute_read.assert_not_called()

    @patch("sentri.agents.auditor.QueryRunner")
    def test_no_db_config_raises_oracle_connection_error(self, MockQueryRunner, agent_context):
        """Unknown database_id with no config or env record raises OracleConnectionError."""
        wf = _create_workflow(
            agent_context,
            database_id="UNKNOWN-DB-99",
        )
        # Remove any env record for this database
        # (it was never registered in the conftest environment_repo)

        mock_runner = MagicMock()
        MockQueryRunner.return_value = mock_runner

        mock_pool = MagicMock()
        auditor = _build_auditor(agent_context, oracle_pool=mock_pool)

        suggestion = Suggestion.from_json(wf.suggestion)
        with pytest.raises(OracleConnectionError, match="No config"):
            auditor._run_verification_query(wf, suggestion)

    @patch("sentri.agents.auditor.QueryRunner")
    def test_connection_uses_env_record_connection_string(self, MockQueryRunner, agent_context):
        """get_connection is called with connection_string from environment_repo."""
        wf = _create_workflow(agent_context, database_id="DEV-DB-01")

        mock_runner = MagicMock()
        mock_runner.execute_read.return_value = [{"tablespace_name": "USERS", "used_percent": 93}]
        MockQueryRunner.return_value = mock_runner

        mock_pool = MagicMock()
        mock_conn = MagicMock()
        mock_pool.get_connection.return_value = mock_conn

        auditor = _build_auditor(agent_context, oracle_pool=mock_pool)
        suggestion = Suggestion.from_json(wf.suggestion)
        auditor._run_verification_query(wf, suggestion)

        mock_pool.get_connection.assert_called_once()
        call_kwargs = mock_pool.get_connection.call_args
        # Should use env record's connection_string
        assert (
            call_kwargs.kwargs.get("connection_string")
            or call_kwargs[1].get("connection_string")
            or ("oracle://sentri_agent@dev-db-01:1521/DEVDB" in str(call_kwargs))
        )

    @patch("sentri.agents.auditor.QueryRunner")
    def test_connection_closed_after_query(self, MockQueryRunner, agent_context):
        """Connection.close() is called after query execution."""
        wf = _create_workflow(agent_context)

        mock_runner = MagicMock()
        mock_runner.execute_read.return_value = [{"tablespace_name": "USERS", "used_percent": 93}]
        MockQueryRunner.return_value = mock_runner

        mock_pool = MagicMock()
        mock_conn = MagicMock()
        mock_pool.get_connection.return_value = mock_conn

        auditor = _build_auditor(agent_context, oracle_pool=mock_pool)
        suggestion = Suggestion.from_json(wf.suggestion)
        auditor._run_verification_query(wf, suggestion)

        mock_conn.close.assert_called_once()

    @patch("sentri.agents.auditor.QueryRunner")
    def test_connection_closed_even_on_query_error(self, MockQueryRunner, agent_context):
        """Connection.close() is called even if the query raises an exception."""
        wf = _create_workflow(agent_context)

        mock_runner = MagicMock()
        mock_runner.execute_read.side_effect = RuntimeError("Query exploded")
        MockQueryRunner.return_value = mock_runner

        mock_pool = MagicMock()
        mock_conn = MagicMock()
        mock_pool.get_connection.return_value = mock_conn

        auditor = _build_auditor(agent_context, oracle_pool=mock_pool)
        suggestion = Suggestion.from_json(wf.suggestion)

        with pytest.raises(RuntimeError):
            auditor._run_verification_query(wf, suggestion)

        mock_conn.close.assert_called_once()

    @patch("sentri.agents.auditor.QueryRunner")
    def test_query_returns_first_row(self, MockQueryRunner, agent_context):
        """When query returns multiple rows, only the first is used."""
        wf = _create_workflow(agent_context)

        mock_runner = MagicMock()
        mock_runner.execute_read.return_value = [
            {"tablespace_name": "USERS", "used_percent": 93},
            {"tablespace_name": "SYSTEM", "used_percent": 40},
        ]
        MockQueryRunner.return_value = mock_runner

        mock_pool = MagicMock()
        mock_pool.get_connection.return_value = MagicMock()

        auditor = _build_auditor(agent_context, oracle_pool=mock_pool)
        suggestion = Suggestion.from_json(wf.suggestion)
        result = auditor._run_verification_query(wf, suggestion)

        assert result == {"tablespace_name": "USERS", "used_percent": 93}


# ---------------------------------------------------------------------------
# TestAuditorConstructor — constructor behavior
# ---------------------------------------------------------------------------


class TestAuditorConstructor:
    """Tests for AuditorAgent initialization."""

    def test_default_oracle_pool(self, agent_context):
        """Without explicit pool, constructor creates OracleConnectionPool."""
        with patch("sentri.agents.auditor.OracleConnectionPool") as MockPool:
            MockPool.return_value = MagicMock()
            _auditor = AuditorAgent(context=agent_context)
            MockPool.assert_called_once()

    def test_custom_oracle_pool(self, agent_context):
        """Explicit pool is used instead of creating a new one."""
        custom_pool = MagicMock()
        auditor = AuditorAgent(context=agent_context, oracle_pool=custom_pool)
        assert auditor._oracle_pool is custom_pool

    def test_name_is_auditor(self, agent_context):
        """Agent name is 'auditor'."""
        auditor = _build_auditor(agent_context)
        assert auditor.name == "auditor"

    def test_query_runner_timeout(self, agent_context):
        """QueryRunner is initialized with 30s timeout."""
        with patch("sentri.agents.auditor.QueryRunner") as MockQR:
            MockQR.return_value = MagicMock()
            _build_auditor(agent_context)
            MockQR.assert_called_once_with(timeout_seconds=30)


# ---------------------------------------------------------------------------
# TestAuditorEdgeCases — edge cases and error handling
# ---------------------------------------------------------------------------


class TestAuditorEdgeCases:
    """Edge cases and unusual situations."""

    @patch("sentri.agents.auditor.QueryRunner")
    def test_actual_equals_reported(self, MockQueryRunner, agent_context):
        """Exact match (diff=0) passes verification."""
        wf = _create_workflow(
            agent_context,
            extracted={"tablespace_name": "USERS", "used_percent": "92"},
        )

        mock_runner = MagicMock()
        mock_runner.execute_read.return_value = [{"tablespace_name": "USERS", "used_percent": 92}]
        MockQueryRunner.return_value = mock_runner

        mock_pool = MagicMock()
        mock_pool.get_connection.return_value = MagicMock()

        auditor = _build_auditor(agent_context, oracle_pool=mock_pool)
        result = auditor.process(wf.id)

        assert result["status"] == "verified"
        assert result["report"].confidence == 1.0

    @patch("sentri.agents.auditor.QueryRunner")
    def test_actual_slightly_better_within_tolerance(self, MockQueryRunner, agent_context):
        """Actual slightly better but within tolerance → passes.

        reported=92, actual=91 → diff = 92 - 91 = 1, tolerance=2 → pass
        """
        wf = _create_workflow(agent_context)

        mock_runner = MagicMock()
        mock_runner.execute_read.return_value = [{"tablespace_name": "USERS", "used_percent": 91}]
        MockQueryRunner.return_value = mock_runner

        mock_pool = MagicMock()
        mock_pool.get_connection.return_value = MagicMock()

        auditor = _build_auditor(agent_context, oracle_pool=mock_pool)
        result = auditor.process(wf.id)

        assert result["status"] == "verified"

    @patch("sentri.agents.auditor.QueryRunner")
    def test_conn_close_exception_does_not_propagate(self, MockQueryRunner, agent_context):
        """If conn.close() raises, the exception is silently suppressed."""
        wf = _create_workflow(agent_context)

        mock_runner = MagicMock()
        mock_runner.execute_read.return_value = [{"tablespace_name": "USERS", "used_percent": 93}]
        MockQueryRunner.return_value = mock_runner

        mock_pool = MagicMock()
        mock_conn = MagicMock()
        mock_conn.close.side_effect = RuntimeError("Close failed")
        mock_pool.get_connection.return_value = mock_conn

        auditor = _build_auditor(agent_context, oracle_pool=mock_pool)
        # Should not raise even though close() fails
        result = auditor.process(wf.id)
        assert result["status"] == "verified"

    @patch("sentri.agents.auditor.QueryRunner")
    def test_process_with_prod_database(self, MockQueryRunner, agent_context):
        """Verification works the same for PROD databases (same flow, different env)."""
        wf = _create_workflow(
            agent_context,
            database_id="PROD-DB-07",
            environment="PROD",
        )

        mock_runner = MagicMock()
        mock_runner.execute_read.return_value = [{"tablespace_name": "USERS", "used_percent": 95}]
        MockQueryRunner.return_value = mock_runner

        mock_pool = MagicMock()
        mock_pool.get_connection.return_value = MagicMock()

        auditor = _build_auditor(agent_context, oracle_pool=mock_pool)
        result = auditor.process(wf.id)

        assert result["status"] == "verified"

    @patch("sentri.agents.auditor.QueryRunner")
    def test_uses_read_only_connection(self, MockQueryRunner, agent_context):
        """get_connection is called with read_only=True."""
        wf = _create_workflow(agent_context)

        mock_runner = MagicMock()
        mock_runner.execute_read.return_value = [{"tablespace_name": "USERS", "used_percent": 93}]
        MockQueryRunner.return_value = mock_runner

        mock_pool = MagicMock()
        mock_pool.get_connection.return_value = MagicMock()

        auditor = _build_auditor(agent_context, oracle_pool=mock_pool)
        auditor.process(wf.id)

        call_kwargs = mock_pool.get_connection.call_args
        assert call_kwargs.kwargs.get("read_only") is True

    @patch("sentri.agents.auditor.QueryRunner")
    def test_extracted_data_passed_as_query_params(self, MockQueryRunner, agent_context):
        """The extracted_data from suggestion is passed to execute_read as params."""
        wf = _create_workflow(
            agent_context,
            extracted={"tablespace_name": "SYSAUX", "used_percent": "85"},
        )

        mock_runner = MagicMock()
        mock_runner.execute_read.return_value = [{"tablespace_name": "SYSAUX", "used_percent": 87}]
        MockQueryRunner.return_value = mock_runner

        mock_pool = MagicMock()
        mock_pool.get_connection.return_value = MagicMock()

        auditor = _build_auditor(agent_context, oracle_pool=mock_pool)
        auditor.process(wf.id)

        # execute_read should receive the extracted_data as third arg
        call_args = mock_runner.execute_read.call_args
        params = call_args[0][2] if len(call_args[0]) > 2 else call_args.kwargs.get("params")
        assert params == {"tablespace_name": "SYSAUX", "used_percent": "85"}
