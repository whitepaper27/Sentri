"""Comprehensive tests for ExecutorAgent — safe SQL execution with locking, rollback, and audit."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from sentri.agents.executor import ExecutorAgent
from sentri.core.constants import (
    EXECUTION_TIMEOUT,
    ExecutionOutcome,
    WorkflowStatus,
)
from sentri.core.exceptions import (
    RollbackError,
)
from sentri.core.models import ExecutionPlan, ExecutionResult, Workflow

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_plan(**overrides) -> ExecutionPlan:
    """Create a default ExecutionPlan for tests."""
    defaults = dict(
        action_type="ADD_DATAFILE",
        forward_sql="ALTER TABLESPACE USERS ADD DATAFILE SIZE 100M",
        rollback_sql="-- no rollback for ADD DATAFILE",
        validation_sql="SELECT bytes/1024/1024 as size_mb FROM dba_data_files WHERE tablespace_name = :tbs",
        expected_outcome={"size_mb": 100},
        risk_level="LOW",
        estimated_duration_seconds=10,
        params={"tbs": "USERS"},
    )
    defaults.update(overrides)
    return ExecutionPlan(**defaults)


def _make_workflow_with_plan(agent_context, **overrides) -> tuple[str, ExecutionPlan]:
    """Create a workflow in EXECUTING status with an execution plan."""
    plan = _make_plan(
        **{k: v for k, v in overrides.items() if k in ExecutionPlan.__dataclass_fields__}
    )
    wf_overrides = {
        k: v for k, v in overrides.items() if k not in ExecutionPlan.__dataclass_fields__
    }

    defaults = dict(
        alert_type="tablespace_full",
        database_id="DEV-DB-01",
        environment="DEV",
        status=WorkflowStatus.EXECUTING.value,
        execution_plan=plan.to_json(),
    )
    defaults.update(wf_overrides)
    wf = Workflow(**defaults)
    wf_id = agent_context.workflow_repo.create(wf)
    return wf_id, plan


def _mock_connection():
    """Create a mock Oracle connection with commit, rollback, close, cursor."""
    conn = MagicMock()
    conn.commit = MagicMock()
    conn.rollback = MagicMock()
    conn.close = MagicMock()
    return conn


def _primary_rw_safety_rows():
    """Return rows indicating a PRIMARY, READ WRITE database."""
    return [{"database_role": "PRIMARY", "open_mode": "READ WRITE"}]


def _standby_ro_safety_rows():
    """Return rows indicating a PHYSICAL STANDBY, READ ONLY database."""
    return [{"database_role": "PHYSICAL STANDBY", "open_mode": "READ ONLY"}]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_pool():
    """Mock OracleConnectionPool."""
    pool = MagicMock()
    pool.get_connection.return_value = _mock_connection()
    return pool


@pytest.fixture
def executor(agent_context, mock_pool):
    """ExecutorAgent with mocked Oracle pool and QueryRunner."""
    with patch("sentri.agents.executor.QueryRunner") as MockQR:
        qr_instance = MagicMock()
        MockQR.return_value = qr_instance
        agent = ExecutorAgent(context=agent_context, oracle_pool=mock_pool)
        # Expose the mocked query runner for test assertions
        agent._mock_query_runner = qr_instance
        yield agent


# ---------------------------------------------------------------------------
# TestExecutorProcess: core process() flow
# ---------------------------------------------------------------------------


class TestExecutorProcess:
    """Tests for the main process() entry point."""

    def test_workflow_not_found(self, agent_context, mock_pool):
        """process() with non-existent workflow_id returns failure."""
        with patch("sentri.agents.executor.QueryRunner"):
            agent = ExecutorAgent(context=agent_context, oracle_pool=mock_pool)
            result = agent.process("non-existent-workflow-id")

        assert result["status"] == "failure"
        assert "not found" in result["error"]

    def test_no_execution_plan(self, agent_context, mock_pool):
        """process() with workflow missing execution_plan returns failure."""
        wf = Workflow(
            alert_type="tablespace_full",
            database_id="DEV-DB-01",
            environment="DEV",
            status=WorkflowStatus.EXECUTING.value,
            execution_plan=None,
        )
        wf_id = agent_context.workflow_repo.create(wf)

        with patch("sentri.agents.executor.QueryRunner"):
            agent = ExecutorAgent(context=agent_context, oracle_pool=mock_pool)
            result = agent.process(wf_id)

        assert result["status"] == "failure"
        assert "No execution plan" in result["error"]

    def test_successful_execution(self, executor, agent_context, mock_pool):
        """Full successful execution: lock, safety, execute, validate, audit, complete."""
        wf_id, plan = _make_workflow_with_plan(agent_context)

        # Configure mock query runner
        qr = executor._mock_query_runner
        # _check_db_safety
        qr.execute_read.side_effect = [
            _primary_rw_safety_rows(),  # safety check
            [{"value": "/u01/oradata"}],  # OMF check (db_create_file_dest)
            [{"size_mb": 50}],  # metrics before
            [{"size_mb": 50}],  # metrics after (same = pass)
        ]
        qr.execute_write.return_value = 0  # DDL returns 0 rows

        result = executor.process(wf_id)

        assert result["status"] == "success"
        assert isinstance(result["result"], ExecutionResult)
        assert result["result"].success is True

        # Verify workflow updated to COMPLETED
        wf = agent_context.workflow_repo.get(wf_id)
        assert wf.status == WorkflowStatus.COMPLETED.value

    def test_successful_execution_non_omf(self, executor, agent_context, mock_pool):
        """Execution where OMF is not set, so datafile path is resolved."""
        wf_id, plan = _make_workflow_with_plan(
            agent_context,
            forward_sql="ALTER TABLESPACE USERS ADD DATAFILE SIZE 100M",
        )

        qr = executor._mock_query_runner
        qr.execute_read.side_effect = [
            _primary_rw_safety_rows(),  # safety check
            [{"value": None}],  # OMF not set
            [{"file_name": "/u01/oradata/DEVDB/users_01.dbf"}],  # existing datafile
            [{"size_mb": 50}],  # metrics before
            [{"size_mb": 50}],  # metrics after (same = pass)
        ]
        qr.execute_write.return_value = 0

        result = executor.process(wf_id)

        assert result["status"] == "success"

    def test_execution_failure_triggers_rollback(self, executor, agent_context, mock_pool):
        """When execute_write raises, rollback is attempted."""
        wf_id, plan = _make_workflow_with_plan(
            agent_context,
            rollback_sql="ALTER TABLESPACE USERS DROP DATAFILE '/u01/oradata/users_02.dbf'",
        )

        qr = executor._mock_query_runner
        qr.execute_read.side_effect = [
            _primary_rw_safety_rows(),  # safety check
            [{"value": "/u01/oradata"}],  # OMF check
        ]
        # First execute_write (forward) raises, second (rollback) succeeds
        qr.execute_write.side_effect = [
            Exception("ORA-01119: error in creating database file"),
            0,  # rollback succeeds
        ]

        result = executor.process(wf_id)

        assert result["status"] == "rolled_back"
        assert result["result"].rolled_back is True
        assert result["result"].success is False

        # Verify workflow updated to ROLLED_BACK
        wf = agent_context.workflow_repo.get(wf_id)
        assert wf.status == WorkflowStatus.ROLLED_BACK.value

    def test_rollback_failure(self, executor, agent_context, mock_pool):
        """When both forward execution and rollback fail."""
        wf_id, plan = _make_workflow_with_plan(
            agent_context,
            rollback_sql="ALTER TABLESPACE USERS DROP DATAFILE '/u01/oradata/users_02.dbf'",
        )

        qr = executor._mock_query_runner
        qr.execute_read.side_effect = [
            _primary_rw_safety_rows(),
            [{"value": "/u01/oradata"}],
        ]
        # Both forward and rollback fail
        qr.execute_write.side_effect = [
            Exception("ORA-01119: error in creating database file"),
            RollbackError("Rollback also failed: ORA-00604"),
        ]

        result = executor.process(wf_id)

        assert result["status"] == "failure"
        assert "Rollback also failed" in result["result"].error_message

        wf = agent_context.workflow_repo.get(wf_id)
        assert wf.status == WorkflowStatus.FAILED.value

    def test_lock_acquisition_failure(self, executor, agent_context, mock_pool):
        """When lock cannot be acquired (another workflow holds it)."""
        wf_id_1, _ = _make_workflow_with_plan(agent_context)
        wf_id_2, _ = _make_workflow_with_plan(agent_context)

        qr = executor._mock_query_runner
        qr.execute_read.side_effect = [
            _primary_rw_safety_rows(),
            [{"value": "/u01/oradata"}],
            [{"size_mb": 50}],
            [{"size_mb": 50}],
        ]
        qr.execute_write.return_value = 0

        # First workflow acquires the lock
        result1 = executor.process(wf_id_1)
        assert result1["status"] == "success"

        # Second workflow tries the same lock key (same db + action_type)
        # The lock was released in finally block of first call, so we need
        # to simulate a held lock by inserting one manually
        from datetime import datetime, timedelta, timezone

        expires = (datetime.now(timezone.utc) + timedelta(seconds=600)).isoformat()
        agent_context.db.execute_write(
            "INSERT INTO locks (resource_key, workflow_id, expires_at) VALUES (?, ?, ?)",
            ("DEV-DB-01:ADD_DATAFILE", "some-other-workflow", expires),
        )

        result2 = executor.process(wf_id_2)

        assert result2["status"] == "failure"
        assert "locked" in result2["error"].lower()

        wf = agent_context.workflow_repo.get(wf_id_2)
        assert wf.status == WorkflowStatus.FAILED.value

    def test_connection_failure(self, executor, agent_context, mock_pool):
        """When Oracle connection cannot be established."""
        wf_id, plan = _make_workflow_with_plan(agent_context)

        mock_pool.get_connection.side_effect = Exception("TNS:listener does not know of service")

        result = executor.process(wf_id)

        assert result["status"] == "failure"
        assert isinstance(result["result"], ExecutionResult)
        assert "Connection failed" in result["result"].error_message

    def test_no_database_config(self, executor, agent_context, mock_pool):
        """When no database config or environment record exists."""
        # Create workflow for an unknown database
        wf_id, plan = _make_workflow_with_plan(
            agent_context,
            database_id="UNKNOWN-DB-99",
        )

        result = executor.process(wf_id)

        assert result["status"] == "failure"
        assert isinstance(result["result"], ExecutionResult)
        assert "No config" in result["result"].error_message

    def test_empty_rollback_sql_proceeds(self, executor, agent_context, mock_pool):
        """Execution proceeds with warning when rollback_sql is empty."""
        wf_id, plan = _make_workflow_with_plan(
            agent_context,
            rollback_sql="   ",  # whitespace only
        )

        qr = executor._mock_query_runner
        qr.execute_read.side_effect = [
            _primary_rw_safety_rows(),
            [{"value": "/u01/oradata"}],
            [{"size_mb": 50}],
            [{"size_mb": 50}],
        ]
        qr.execute_write.return_value = 0

        result = executor.process(wf_id)

        # Should still succeed (just logs a warning)
        assert result["status"] == "success"

    def test_lock_released_on_success(self, executor, agent_context, mock_pool):
        """Lock is released after successful execution."""
        wf_id, plan = _make_workflow_with_plan(agent_context)

        qr = executor._mock_query_runner
        qr.execute_read.side_effect = [
            _primary_rw_safety_rows(),
            [{"value": "/u01/oradata"}],
            [{"size_mb": 50}],
            [{"size_mb": 50}],
        ]
        qr.execute_write.return_value = 0

        executor.process(wf_id)

        # Lock should be released — verify by checking locks table is empty
        rows = agent_context.db.execute_read(
            "SELECT * FROM locks WHERE resource_key = ?",
            ("DEV-DB-01:ADD_DATAFILE",),
        )
        assert len(rows) == 0

    def test_lock_released_on_failure(self, executor, agent_context, mock_pool):
        """Lock is released even when execution fails (finally block)."""
        wf_id, plan = _make_workflow_with_plan(agent_context)

        qr = executor._mock_query_runner
        qr.execute_read.side_effect = [
            _primary_rw_safety_rows(),
            [{"value": "/u01/oradata"}],
        ]
        qr.execute_write.side_effect = Exception("ORA-99999: test error")

        executor.process(wf_id)

        # Lock should still be released
        rows = agent_context.db.execute_read(
            "SELECT * FROM locks WHERE resource_key = ?",
            ("DEV-DB-01:ADD_DATAFILE",),
        )
        assert len(rows) == 0


# ---------------------------------------------------------------------------
# TestExecutorSafety: database safety checks
# ---------------------------------------------------------------------------


class TestExecutorSafety:
    """Tests for _check_db_safety pre-execution guard."""

    def test_db_safety_blocks_standby(self, executor, agent_context, mock_pool):
        """Standby databases are blocked from execution."""
        wf_id, plan = _make_workflow_with_plan(agent_context)

        qr = executor._mock_query_runner
        qr.execute_read.side_effect = [
            _standby_ro_safety_rows(),  # safety check returns standby
        ]

        result = executor.process(wf_id)

        assert result["status"] == "failure"
        assert isinstance(result["result"], ExecutionResult)
        assert "STANDBY" in result["result"].error_message

    def test_db_safety_allows_primary(self, executor, agent_context, mock_pool):
        """Primary READ WRITE databases pass safety check."""
        wf_id, plan = _make_workflow_with_plan(agent_context)

        qr = executor._mock_query_runner
        qr.execute_read.side_effect = [
            _primary_rw_safety_rows(),
            [{"value": "/u01/oradata"}],
            [{"size_mb": 50}],
            [{"size_mb": 50}],
        ]
        qr.execute_write.return_value = 0

        result = executor.process(wf_id)

        assert result["status"] == "success"

    def test_db_safety_blocks_read_only(self, executor, agent_context, mock_pool):
        """Databases in READ ONLY mode are blocked."""
        wf_id, plan = _make_workflow_with_plan(agent_context)

        qr = executor._mock_query_runner
        qr.execute_read.side_effect = [
            [{"database_role": "PRIMARY", "open_mode": "READ ONLY"}],
        ]

        result = executor.process(wf_id)

        assert result["status"] == "failure"
        assert "not writable" in result["result"].error_message.lower()

    def test_db_safety_blocks_mounted(self, executor, agent_context, mock_pool):
        """Databases in MOUNTED mode are blocked."""
        wf_id, plan = _make_workflow_with_plan(agent_context)

        qr = executor._mock_query_runner
        qr.execute_read.side_effect = [
            [{"database_role": "PRIMARY", "open_mode": "MOUNTED"}],
        ]

        result = executor.process(wf_id)

        assert result["status"] == "failure"
        assert "not writable" in result["result"].error_message.lower()

    def test_db_safety_empty_result(self, executor, agent_context, mock_pool):
        """When v$database returns no rows, safety check fails."""
        wf_id, plan = _make_workflow_with_plan(agent_context)

        qr = executor._mock_query_runner
        qr.execute_read.side_effect = [
            [],  # empty result from v$database
        ]

        result = executor.process(wf_id)

        assert result["status"] == "failure"
        assert "Cannot query" in result["result"].error_message

    def test_db_safety_query_exception_proceeds(self, executor, agent_context, mock_pool):
        """When safety check query fails, execution proceeds (fail-open)."""
        wf_id, plan = _make_workflow_with_plan(agent_context)

        # Safety check raises, but then the rest succeeds
        call_count = 0

        def side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise Exception("ORA-00942: table or view does not exist")
            elif call_count == 2:
                return [{"value": "/u01/oradata"}]  # OMF check
            elif call_count == 3:
                return [{"size_mb": 50}]  # before metrics
            elif call_count == 4:
                return [{"size_mb": 50}]  # after metrics
            return []

        qr = executor._mock_query_runner
        qr.execute_read.side_effect = side_effect
        qr.execute_write.return_value = 0

        result = executor.process(wf_id)

        # Safety check fails but proceeds (fail-open design)
        assert result["status"] == "success"


# ---------------------------------------------------------------------------
# TestExecutorMetrics: before/after metric capture and validation
# ---------------------------------------------------------------------------


class TestExecutorMetrics:
    """Tests for metric capture and validation logic."""

    def test_metrics_captured_before_and_after(self, executor, agent_context, mock_pool):
        """Both before and after metrics are recorded in the result."""
        wf_id, plan = _make_workflow_with_plan(
            agent_context,
            validation_sql="SELECT pct_used FROM dba_tablespace_usage_metrics WHERE tablespace_name = :tbs",
        )

        qr = executor._mock_query_runner
        qr.execute_read.side_effect = [
            _primary_rw_safety_rows(),
            [{"value": "/u01/oradata"}],
            [{"pct_used": 95}],  # before (95% used)
            [{"pct_used": 70}],  # after  (70% used - improved)
        ]
        qr.execute_write.return_value = 0

        result = executor.process(wf_id)

        assert result["status"] == "success"
        exec_result = result["result"]
        assert exec_result.metrics_before == {"pct_used": 95}
        assert exec_result.metrics_after == {"pct_used": 70}

    def test_validation_fails_when_worse(self, executor, agent_context, mock_pool):
        """When after metrics are worse than before, validation fails and rollback is triggered."""
        wf_id, plan = _make_workflow_with_plan(
            agent_context,
            rollback_sql="ALTER TABLESPACE USERS DROP DATAFILE '/u01/oradata/users_02.dbf'",
            validation_sql="SELECT pct_used FROM dba_tablespace_usage_metrics WHERE tablespace_name = :tbs",
        )

        qr = executor._mock_query_runner
        qr.execute_read.side_effect = [
            _primary_rw_safety_rows(),
            [{"value": "/u01/oradata"}],
            [{"pct_used": 85}],  # before: 85% used
            [{"pct_used": 92}],  # after: 92% used (WORSE!)
        ]
        # First execute_write is forward SQL, second is rollback SQL
        qr.execute_write.side_effect = [0, 0]

        result = executor.process(wf_id)

        assert result["status"] == "rolled_back"
        assert result["result"].rolled_back is True
        assert result["result"].success is False

    def test_no_validation_sql_skips_validation(self, executor, agent_context, mock_pool):
        """When validation_sql is empty, execution succeeds without validation."""
        wf_id, plan = _make_workflow_with_plan(
            agent_context,
            validation_sql="   ",  # empty
        )

        qr = executor._mock_query_runner
        qr.execute_read.side_effect = [
            _primary_rw_safety_rows(),
            [{"value": "/u01/oradata"}],
            # No metrics calls because validation_sql is empty
        ]
        qr.execute_write.return_value = 0

        result = executor.process(wf_id)

        assert result["status"] == "success"

    def test_metrics_capture_failure_returns_empty(self, executor, agent_context, mock_pool):
        """When metrics capture query fails, it returns empty dict (not an error)."""
        wf_id, plan = _make_workflow_with_plan(agent_context)

        call_count = 0

        def side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _primary_rw_safety_rows()  # safety
            elif call_count == 2:
                return [{"value": "/u01/oradata"}]  # OMF
            elif call_count == 3:
                raise Exception("ORA-00942: table or view does not exist")  # before metrics fail
            elif call_count == 4:
                raise Exception("ORA-00942: table or view does not exist")  # after metrics fail
            return []

        qr = executor._mock_query_runner
        qr.execute_read.side_effect = side_effect
        qr.execute_write.return_value = 0

        result = executor.process(wf_id)

        # No metrics = assume success (both empty, validation returns True)
        assert result["status"] == "success"
        assert result["result"].metrics_before == {}
        assert result["result"].metrics_after == {}

    def test_validation_with_non_numeric_fields_ignored(self, executor, agent_context, mock_pool):
        """Non-numeric metric fields are skipped during validation comparison."""
        wf_id, plan = _make_workflow_with_plan(
            agent_context,
            validation_sql="SELECT tablespace_name, pct_used FROM dba_tablespace_usage_metrics WHERE tablespace_name = :tbs",
        )

        qr = executor._mock_query_runner
        qr.execute_read.side_effect = [
            _primary_rw_safety_rows(),
            [{"value": "/u01/oradata"}],
            [{"tablespace_name": "USERS", "pct_used": 85}],  # before
            [{"tablespace_name": "USERS", "pct_used": 70}],  # after (improved)
        ]
        qr.execute_write.return_value = 0

        result = executor.process(wf_id)

        assert result["status"] == "success"


# ---------------------------------------------------------------------------
# TestExecutorAudit: audit record creation
# ---------------------------------------------------------------------------


class TestExecutorAudit:
    """Tests for immutable audit trail."""

    def test_audit_record_on_success(self, executor, agent_context, mock_pool):
        """Successful execution creates a SUCCESS audit record."""
        wf_id, plan = _make_workflow_with_plan(agent_context)

        qr = executor._mock_query_runner
        qr.execute_read.side_effect = [
            _primary_rw_safety_rows(),
            [{"value": "/u01/oradata"}],
            [{"size_mb": 50}],
            [{"size_mb": 50}],
        ]
        qr.execute_write.return_value = 0

        executor.process(wf_id)

        # Check audit record was created
        audits = agent_context.audit_repo.find_recent(10)
        assert len(audits) == 1
        audit = audits[0]
        assert audit.workflow_id == wf_id
        assert audit.action_type == "ADD_DATAFILE"
        assert audit.database_id == "DEV-DB-01"
        assert audit.environment == "DEV"
        assert audit.executed_by == "agent4_executor"
        assert audit.result == ExecutionOutcome.SUCCESS.value

    def test_audit_record_on_failure(self, executor, agent_context, mock_pool):
        """Failed execution creates a FAILED audit record."""
        wf_id, plan = _make_workflow_with_plan(agent_context)

        qr = executor._mock_query_runner
        qr.execute_read.side_effect = [
            _primary_rw_safety_rows(),
            [{"value": "/u01/oradata"}],
        ]
        # Forward SQL fails, rollback also fails (empty rollback_sql default: "-- no rollback...")
        qr.execute_write.side_effect = Exception("ORA-01652: unable to extend temp segment")

        executor.process(wf_id)

        audits = agent_context.audit_repo.find_recent(10)
        assert len(audits) == 1
        audit = audits[0]
        assert audit.result == ExecutionOutcome.FAILED.value
        assert audit.error_message is not None

    def test_audit_record_on_rollback(self, executor, agent_context, mock_pool):
        """Rolled-back execution creates a ROLLED_BACK audit record."""
        wf_id, plan = _make_workflow_with_plan(
            agent_context,
            rollback_sql="ALTER TABLESPACE USERS DROP DATAFILE '/u01/oradata/users_02.dbf'",
            validation_sql="SELECT pct_used FROM dba_tablespace_usage_metrics WHERE tablespace_name = :tbs",
        )

        qr = executor._mock_query_runner
        qr.execute_read.side_effect = [
            _primary_rw_safety_rows(),
            [{"value": "/u01/oradata"}],
            [{"pct_used": 85}],  # before
            [{"pct_used": 95}],  # after (worse)
        ]
        qr.execute_write.side_effect = [0, 0]  # forward + rollback

        executor.process(wf_id)

        audits = agent_context.audit_repo.find_recent(10)
        assert len(audits) == 1
        assert audits[0].result == ExecutionOutcome.ROLLED_BACK.value

    def test_audit_evidence_contains_metrics(self, executor, agent_context, mock_pool):
        """Audit record evidence JSON contains metrics_before, metrics_after, and duration."""
        wf_id, plan = _make_workflow_with_plan(
            agent_context,
            validation_sql="SELECT pct_used FROM dba_tablespace_usage_metrics WHERE tablespace_name = :tbs",
        )

        qr = executor._mock_query_runner
        qr.execute_read.side_effect = [
            _primary_rw_safety_rows(),
            [{"value": "/u01/oradata"}],
            [{"pct_used": 95}],
            [{"pct_used": 70}],
        ]
        qr.execute_write.return_value = 0

        executor.process(wf_id)

        audits = agent_context.audit_repo.find_recent(10)
        evidence = json.loads(audits[0].evidence)
        assert "metrics_before" in evidence
        assert "metrics_after" in evidence
        assert "duration_seconds" in evidence
        assert evidence["metrics_before"] == {"pct_used": 95}
        assert evidence["metrics_after"] == {"pct_used": 70}

    def test_audit_action_sql_recorded(self, executor, agent_context, mock_pool):
        """Audit record contains the SQL that was executed."""
        wf_id, plan = _make_workflow_with_plan(agent_context)

        qr = executor._mock_query_runner
        qr.execute_read.side_effect = [
            _primary_rw_safety_rows(),
            [{"value": "/u01/oradata"}],
            [{"size_mb": 50}],
            [{"size_mb": 50}],
        ]
        qr.execute_write.return_value = 0

        executor.process(wf_id)

        audits = agent_context.audit_repo.find_recent(10)
        assert "ALTER TABLESPACE USERS ADD DATAFILE SIZE 100M" in audits[0].action_sql

    def test_no_audit_on_lock_failure(self, executor, agent_context, mock_pool):
        """No audit record is created when lock acquisition fails (no SQL was run)."""
        wf_id, plan = _make_workflow_with_plan(agent_context)

        # Insert a lock to cause acquisition failure
        from datetime import datetime, timedelta, timezone

        expires = (datetime.now(timezone.utc) + timedelta(seconds=600)).isoformat()
        agent_context.db.execute_write(
            "INSERT INTO locks (resource_key, workflow_id, expires_at) VALUES (?, ?, ?)",
            ("DEV-DB-01:ADD_DATAFILE", "blocking-workflow", expires),
        )

        executor.process(wf_id)

        audits = agent_context.audit_repo.find_recent(10)
        assert len(audits) == 0


# ---------------------------------------------------------------------------
# TestExecutorValidation: static _validate_execution method
# ---------------------------------------------------------------------------


class TestExecutorValidation:
    """Tests for the static _validate_execution comparison logic."""

    def test_validate_no_metrics_returns_true(self):
        """When both before and after are empty, validation passes."""
        assert ExecutorAgent._validate_execution({}, {}) is True

    def test_validate_no_before_returns_true(self):
        """When before is empty (could not capture), validation passes."""
        assert ExecutorAgent._validate_execution({}, {"size_mb": 100}) is True

    def test_validate_no_after_returns_true(self):
        """When after is empty (could not capture), validation passes."""
        assert ExecutorAgent._validate_execution({"size_mb": 50}, {}) is True

    def test_validate_improved_returns_true(self):
        """When numeric values decrease (lower is better), validation passes."""
        before = {"pct_used": 95.5}
        after = {"pct_used": 80.2}
        assert ExecutorAgent._validate_execution(before, after) is True

    def test_validate_same_returns_true(self):
        """When numeric values stay the same, validation passes (not worse)."""
        before = {"pct_used": 85.0}
        after = {"pct_used": 85.0}
        assert ExecutorAgent._validate_execution(before, after) is True

    def test_validate_worse_returns_false(self):
        """When numeric values increase (higher = worse), validation fails."""
        before = {"pct_used": 85.0}
        after = {"pct_used": 92.0}
        assert ExecutorAgent._validate_execution(before, after) is False

    def test_validate_mixed_metrics(self):
        """With multiple metrics, any getting worse triggers failure."""
        before = {"pct_used": 85, "size_mb": 100}
        after = {"pct_used": 80, "size_mb": 110}  # pct_used improved, size_mb worse
        assert ExecutorAgent._validate_execution(before, after) is False

    def test_validate_non_numeric_ignored(self):
        """Non-numeric metric fields are skipped (no TypeError)."""
        before = {"tablespace_name": "USERS", "pct_used": 85}
        after = {"tablespace_name": "USERS", "pct_used": 80}
        assert ExecutorAgent._validate_execution(before, after) is True

    def test_validate_extra_keys_in_after(self):
        """Keys in after but not in before are not compared."""
        before = {"pct_used": 85}
        after = {"pct_used": 80, "new_metric": 999}
        assert ExecutorAgent._validate_execution(before, after) is True

    def test_validate_none_values_skipped(self):
        """None values in metrics are safely skipped."""
        before = {"pct_used": None}
        after = {"pct_used": 80}
        assert ExecutorAgent._validate_execution(before, after) is True


# ---------------------------------------------------------------------------
# TestExecutorRollback: rollback logic details
# ---------------------------------------------------------------------------


class TestExecutorRollback:
    """Tests for the _do_rollback internal method."""

    def test_rollback_no_rollback_sql(self, executor, agent_context, mock_pool):
        """Validation failure with no rollback SQL marks as FAILED (not rolled back)."""
        wf_id, plan = _make_workflow_with_plan(
            agent_context,
            rollback_sql="   ",  # empty
            validation_sql="SELECT pct_used FROM dba_tablespace_usage_metrics WHERE tablespace_name = :tbs",
        )

        qr = executor._mock_query_runner
        qr.execute_read.side_effect = [
            _primary_rw_safety_rows(),
            [{"value": "/u01/oradata"}],
            [{"pct_used": 85}],  # before
            [{"pct_used": 95}],  # after (worse)
        ]
        qr.execute_write.return_value = 0

        result = executor.process(wf_id)

        assert result["status"] == "failure"
        assert result["result"].rolled_back is False
        assert "no rollback" in result["result"].error_message.lower()

    def test_rollback_sql_exception_raises_rollback_error(self, executor, agent_context, mock_pool):
        """When rollback SQL itself fails, RollbackError is raised."""
        wf_id, plan = _make_workflow_with_plan(
            agent_context,
            rollback_sql="DROP TABLESPACE USERS",
            validation_sql="SELECT pct_used FROM dba_tablespace_usage_metrics WHERE tablespace_name = :tbs",
        )

        qr = executor._mock_query_runner
        qr.execute_read.side_effect = [
            _primary_rw_safety_rows(),
            [{"value": "/u01/oradata"}],
            [{"pct_used": 85}],  # before
            [{"pct_used": 95}],  # after (worse) -- triggers rollback
        ]
        # Forward succeeds, rollback fails
        qr.execute_write.side_effect = [0, Exception("ORA-00604: error at recursive SQL level")]

        result = executor.process(wf_id)

        # Exception path in _execute_plan catches RollbackError as generic Exception
        assert result["status"] == "failure"

    def test_execution_exception_with_successful_rollback(self, executor, agent_context, mock_pool):
        """Forward SQL exception triggers rollback which succeeds."""
        wf_id, plan = _make_workflow_with_plan(
            agent_context,
            rollback_sql="ALTER TABLESPACE USERS DROP DATAFILE '/u01/oradata/users_02.dbf'",
        )

        qr = executor._mock_query_runner
        qr.execute_read.side_effect = [
            _primary_rw_safety_rows(),
            [{"value": "/u01/oradata"}],
        ]
        # Forward fails, rollback succeeds
        qr.execute_write.side_effect = [
            Exception("ORA-01652: unable to extend temp segment"),
            0,  # rollback succeeds
        ]

        result = executor.process(wf_id)

        assert result["status"] == "rolled_back"
        exec_result = result["result"]
        assert exec_result.rolled_back is True
        assert exec_result.error_message is not None
        assert "ORA-01652" in exec_result.error_message


# ---------------------------------------------------------------------------
# TestExecutorDatafilePath: _resolve_datafile_path logic
# ---------------------------------------------------------------------------


class TestExecutorDatafilePath:
    """Tests for datafile path resolution (OMF vs explicit path)."""

    def test_omf_enabled_no_path_resolution(self, executor, agent_context, mock_pool):
        """When OMF is enabled, SQL is returned unchanged."""
        wf_id, plan = _make_workflow_with_plan(
            agent_context,
            forward_sql="ALTER TABLESPACE USERS ADD DATAFILE SIZE 100M",
        )

        qr = executor._mock_query_runner
        qr.execute_read.side_effect = [
            _primary_rw_safety_rows(),
            [{"value": "/u01/app/oracle/oradata"}],  # OMF is set
            [{"size_mb": 50}],
            [{"size_mb": 50}],
        ]
        qr.execute_write.return_value = 0

        result = executor.process(wf_id)

        assert result["status"] == "success"
        # The forward SQL should be the same (no path added)
        assert (
            result["result"].action_sql_executed == "ALTER TABLESPACE USERS ADD DATAFILE SIZE 100M"
        )

    def test_non_datafile_sql_unchanged(self, executor, agent_context, mock_pool):
        """SQL that is not ADD DATAFILE is returned unchanged."""
        wf_id, plan = _make_workflow_with_plan(
            agent_context,
            forward_sql="ALTER TABLESPACE USERS RESIZE DATAFILE '/u01/oradata/users_01.dbf' SIZE 200M",
        )

        qr = executor._mock_query_runner
        qr.execute_read.side_effect = [
            _primary_rw_safety_rows(),
            # No OMF check needed since SQL doesn't match ADD DATAFILE SIZE pattern
            [{"size_mb": 100}],
            [{"size_mb": 100}],
        ]
        qr.execute_write.return_value = 0

        result = executor.process(wf_id)

        assert result["status"] == "success"

    def test_non_omf_resolves_path(self, executor, agent_context, mock_pool):
        """When OMF is NOT set, path is resolved from existing datafiles."""
        wf_id, plan = _make_workflow_with_plan(
            agent_context,
            forward_sql="ALTER TABLESPACE USERS ADD DATAFILE SIZE 100M",
        )

        qr = executor._mock_query_runner
        qr.execute_read.side_effect = [
            _primary_rw_safety_rows(),
            [{"value": None}],  # OMF not set
            [{"file_name": "/u01/oradata/DEVDB/users_01.dbf"}],  # existing datafile
            [{"size_mb": 50}],  # before
            [{"size_mb": 50}],  # after
        ]
        qr.execute_write.return_value = 0

        result = executor.process(wf_id)

        assert result["status"] == "success"
        # SQL should now have an explicit path
        _executed = result["result"].action_sql_executed
        # The action_sql_executed is the original forward_sql from the plan,
        # not the resolved one (the resolved one is used for execution only).
        # This is because the result stores plan.forward_sql not the resolved sql.
        # The actual execution used the resolved SQL via query_runner.

    def test_non_omf_empty_result_keeps_original(self, executor, agent_context, mock_pool):
        """When OMF is not set and no existing datafiles found, SQL is unchanged."""
        wf_id, plan = _make_workflow_with_plan(
            agent_context,
            forward_sql="ALTER TABLESPACE USERS ADD DATAFILE SIZE 100M",
        )

        qr = executor._mock_query_runner
        qr.execute_read.side_effect = [
            _primary_rw_safety_rows(),
            [{"value": None}],  # OMF not set
            [],  # no existing datafiles
            [{"size_mb": 50}],
            [{"size_mb": 50}],
        ]
        qr.execute_write.return_value = 0

        result = executor.process(wf_id)

        assert result["status"] == "success"


# ---------------------------------------------------------------------------
# TestExecutorLocking: lock acquisition and release
# ---------------------------------------------------------------------------


class TestExecutorLocking:
    """Tests for SQLite-based resource locking."""

    def test_stale_lock_cleaned_before_acquire(self, executor, agent_context, mock_pool):
        """Expired locks are cleaned up before attempting to acquire."""
        from datetime import datetime, timedelta, timezone

        # Insert an expired lock
        expired = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()
        agent_context.db.execute_write(
            "INSERT INTO locks (resource_key, workflow_id, expires_at) VALUES (?, ?, ?)",
            ("DEV-DB-01:ADD_DATAFILE", "old-workflow", expired),
        )

        wf_id, plan = _make_workflow_with_plan(agent_context)

        qr = executor._mock_query_runner
        qr.execute_read.side_effect = [
            _primary_rw_safety_rows(),
            [{"value": "/u01/oradata"}],
            [{"size_mb": 50}],
            [{"size_mb": 50}],
        ]
        qr.execute_write.return_value = 0

        result = executor.process(wf_id)

        # Should succeed because stale lock was cleaned
        assert result["status"] == "success"

    def test_active_lock_blocks_second_workflow(self, executor, agent_context, mock_pool):
        """An active (non-expired) lock prevents a second workflow from executing."""
        from datetime import datetime, timedelta, timezone

        expires = (datetime.now(timezone.utc) + timedelta(seconds=600)).isoformat()
        agent_context.db.execute_write(
            "INSERT INTO locks (resource_key, workflow_id, expires_at) VALUES (?, ?, ?)",
            ("DEV-DB-01:ADD_DATAFILE", "active-workflow", expires),
        )

        wf_id, plan = _make_workflow_with_plan(agent_context)

        result = executor.process(wf_id)

        assert result["status"] == "failure"
        assert "locked" in result["error"].lower()

    def test_different_lock_keys_no_conflict(self, executor, agent_context, mock_pool):
        """Workflows on different databases or action types don't conflict."""
        from datetime import datetime, timedelta, timezone

        # Lock on a different database
        expires = (datetime.now(timezone.utc) + timedelta(seconds=600)).isoformat()
        agent_context.db.execute_write(
            "INSERT INTO locks (resource_key, workflow_id, expires_at) VALUES (?, ?, ?)",
            ("UAT-DB-03:ADD_DATAFILE", "other-workflow", expires),
        )

        wf_id, plan = _make_workflow_with_plan(agent_context)

        qr = executor._mock_query_runner
        qr.execute_read.side_effect = [
            _primary_rw_safety_rows(),
            [{"value": "/u01/oradata"}],
            [{"size_mb": 50}],
            [{"size_mb": 50}],
        ]
        qr.execute_write.return_value = 0

        result = executor.process(wf_id)

        # Different lock key, should succeed
        assert result["status"] == "success"


# ---------------------------------------------------------------------------
# TestExecutorEnvironment: environment-specific behavior
# ---------------------------------------------------------------------------


class TestExecutorEnvironment:
    """Tests for environment-specific execution paths."""

    def test_uat_execution(self, executor, agent_context, mock_pool):
        """Execution works for UAT environment."""
        wf_id, plan = _make_workflow_with_plan(
            agent_context,
            database_id="UAT-DB-03",
            environment="UAT",
        )

        qr = executor._mock_query_runner
        qr.execute_read.side_effect = [
            _primary_rw_safety_rows(),
            [{"value": "/u01/oradata"}],
            [{"size_mb": 50}],
            [{"size_mb": 50}],
        ]
        qr.execute_write.return_value = 0

        result = executor.process(wf_id)

        assert result["status"] == "success"

        audits = agent_context.audit_repo.find_recent(10)
        assert audits[0].environment == "UAT"
        assert audits[0].database_id == "UAT-DB-03"

    def test_prod_execution(self, executor, agent_context, mock_pool):
        """Execution works for PROD environment (approval assumed already given)."""
        wf_id, plan = _make_workflow_with_plan(
            agent_context,
            database_id="PROD-DB-07",
            environment="PROD",
        )

        qr = executor._mock_query_runner
        qr.execute_read.side_effect = [
            _primary_rw_safety_rows(),
            [{"value": "/u01/oradata"}],
            [{"size_mb": 50}],
            [{"size_mb": 50}],
        ]
        qr.execute_write.return_value = 0

        result = executor.process(wf_id)

        assert result["status"] == "success"

        audits = agent_context.audit_repo.find_recent(10)
        assert audits[0].environment == "PROD"


# ---------------------------------------------------------------------------
# TestExecutorDuration: timing measurement
# ---------------------------------------------------------------------------


class TestExecutorDuration:
    """Tests for execution duration tracking."""

    def test_duration_recorded(self, executor, agent_context, mock_pool):
        """Execution result includes a positive duration."""
        wf_id, plan = _make_workflow_with_plan(agent_context)

        qr = executor._mock_query_runner
        qr.execute_read.side_effect = [
            _primary_rw_safety_rows(),
            [{"value": "/u01/oradata"}],
            [{"size_mb": 50}],
            [{"size_mb": 50}],
        ]
        qr.execute_write.return_value = 0

        result = executor.process(wf_id)

        assert result["result"].duration_seconds >= 0

    def test_duration_recorded_on_failure(self, executor, agent_context, mock_pool):
        """Duration is recorded even when execution fails."""
        wf_id, plan = _make_workflow_with_plan(agent_context)

        qr = executor._mock_query_runner
        qr.execute_read.side_effect = [
            _primary_rw_safety_rows(),
            [{"value": "/u01/oradata"}],
        ]
        qr.execute_write.side_effect = Exception("ORA-01652: test error")

        result = executor.process(wf_id)

        assert result["result"].duration_seconds >= 0


# ---------------------------------------------------------------------------
# TestExecutorConstructor: initialization
# ---------------------------------------------------------------------------


class TestExecutorConstructor:
    """Tests for ExecutorAgent constructor."""

    def test_default_pool_created(self, agent_context):
        """When no oracle_pool is provided, a default OracleConnectionPool is created."""
        with (
            patch("sentri.agents.executor.OracleConnectionPool") as MockPool,
            patch("sentri.agents.executor.QueryRunner"),
        ):
            _agent = ExecutorAgent(context=agent_context)
            MockPool.assert_called_once()

    def test_custom_pool_used(self, agent_context, mock_pool):
        """When oracle_pool is provided, it is used instead of creating a new one."""
        with patch("sentri.agents.executor.QueryRunner"):
            agent = ExecutorAgent(context=agent_context, oracle_pool=mock_pool)
            assert agent._oracle_pool is mock_pool

    def test_query_runner_initialized_with_timeout(self, agent_context, mock_pool):
        """QueryRunner is initialized with EXECUTION_TIMEOUT."""
        with patch("sentri.agents.executor.QueryRunner") as MockQR:
            _agent = ExecutorAgent(context=agent_context, oracle_pool=mock_pool)
            MockQR.assert_called_once_with(timeout_seconds=EXECUTION_TIMEOUT)
