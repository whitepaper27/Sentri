"""Tests for `sentri approve` CLI command."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from click.testing import CliRunner

from sentri.cli.approve_cmd import approve_cmd
from sentri.core.constants import WorkflowStatus
from sentri.core.models import Workflow
from sentri.db.audit_repo import AuditRepository
from sentri.db.connection import Database
from sentri.db.workflow_repo import WorkflowRepository


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def cli_db(tmp_path):
    """Create a temp DB for CLI tests and patch paths."""
    db_path = tmp_path / "test.db"
    db = Database(db_path)
    db.initialize_schema()
    yield db, db_path, tmp_path
    db.close()


class TestApproveCmd:
    """Test the approve CLI command."""

    def test_approve_workflow(self, runner, cli_db):
        """Should transition AWAITING_APPROVAL -> APPROVED."""
        db, db_path, home = cli_db
        repo = WorkflowRepository(db)

        wf = Workflow(
            alert_type="cpu_high",
            database_id="DEV-DB-01",
            environment="DEV",
            status=WorkflowStatus.AWAITING_APPROVAL.value,
        )
        wf_id = repo.create(wf)

        with patch("sentri.config.paths.SENTRI_HOME", home), patch(
            "sentri.config.paths.DB_PATH", db_path
        ):
            result = runner.invoke(approve_cmd, [wf_id])

        assert result.exit_code == 0
        assert "APPROVED" in result.output

        updated = repo.get(wf_id)
        assert updated.status == WorkflowStatus.APPROVED.value

    def test_deny_workflow(self, runner, cli_db):
        """Should transition AWAITING_APPROVAL -> DENIED -> COMPLETED."""
        db, db_path, home = cli_db
        repo = WorkflowRepository(db)

        wf = Workflow(
            alert_type="tablespace_full",
            database_id="PROD-DB-07",
            environment="PROD",
            status=WorkflowStatus.AWAITING_APPROVAL.value,
        )
        wf_id = repo.create(wf)

        with patch("sentri.config.paths.SENTRI_HOME", home), patch(
            "sentri.config.paths.DB_PATH", db_path
        ):
            result = runner.invoke(approve_cmd, [wf_id, "--deny"])

        assert result.exit_code == 0
        assert "DENIED" in result.output

        updated = repo.get(wf_id)
        assert updated.status == WorkflowStatus.COMPLETED.value

    def test_partial_id_match(self, runner, cli_db):
        """Should work with first 8 chars of workflow ID."""
        db, db_path, home = cli_db
        repo = WorkflowRepository(db)

        wf = Workflow(
            alert_type="cpu_high",
            database_id="DEV-DB-01",
            environment="DEV",
            status=WorkflowStatus.AWAITING_APPROVAL.value,
        )
        wf_id = repo.create(wf)
        short_id = wf_id[:8]

        with patch("sentri.config.paths.SENTRI_HOME", home), patch(
            "sentri.config.paths.DB_PATH", db_path
        ):
            result = runner.invoke(approve_cmd, [short_id])

        assert result.exit_code == 0
        assert "APPROVED" in result.output

    def test_wrong_status_rejected(self, runner, cli_db):
        """Should reject workflows not in AWAITING_APPROVAL."""
        db, db_path, home = cli_db
        repo = WorkflowRepository(db)

        wf = Workflow(
            alert_type="cpu_high",
            database_id="DEV-DB-01",
            environment="DEV",
            status=WorkflowStatus.COMPLETED.value,
        )
        wf_id = repo.create(wf)

        with patch("sentri.config.paths.SENTRI_HOME", home), patch(
            "sentri.config.paths.DB_PATH", db_path
        ):
            result = runner.invoke(approve_cmd, [wf_id])

        assert result.exit_code != 0

    def test_not_found_error(self, runner, cli_db):
        """Should error when workflow doesn't exist."""
        db, db_path, home = cli_db

        with patch("sentri.config.paths.SENTRI_HOME", home), patch(
            "sentri.config.paths.DB_PATH", db_path
        ):
            result = runner.invoke(approve_cmd, ["nonexistent-id-12345"])

        assert result.exit_code != 0

    def test_approve_creates_audit_record(self, runner, cli_db):
        """Approve should create an audit record with WHO and channel."""
        db, db_path, home = cli_db
        repo = WorkflowRepository(db)
        audit_repo = AuditRepository(db)

        wf = Workflow(
            alert_type="cpu_high",
            database_id="DEV-DB-01",
            environment="DEV",
            status=WorkflowStatus.AWAITING_APPROVAL.value,
            execution_plan='{"forward_sql":"SELECT 1"}',
        )
        wf_id = repo.create(wf)

        with patch("sentri.config.paths.SENTRI_HOME", home), patch(
            "sentri.config.paths.DB_PATH", db_path
        ):
            result = runner.invoke(approve_cmd, [wf_id, "--by", "John DBA"])

        assert result.exit_code == 0

        records = audit_repo.find_by_workflow(wf_id)
        assert len(records) == 1
        assert records[0].action_type == "APPROVAL_DECISION"
        assert records[0].result == "APPROVED"
        assert records[0].approved_by == "John DBA"
        assert "channel=cli" in records[0].evidence

        # Check approved_by populated on workflow
        updated = repo.get(wf_id)
        assert updated.approved_by == "John DBA"
        assert updated.approved_at is not None

    def test_deny_with_reason(self, runner, cli_db):
        """Deny with --reason should store reason in audit evidence."""
        db, db_path, home = cli_db
        repo = WorkflowRepository(db)
        audit_repo = AuditRepository(db)

        wf = Workflow(
            alert_type="tablespace_full",
            database_id="PROD-DB-07",
            environment="PROD",
            status=WorkflowStatus.AWAITING_APPROVAL.value,
        )
        wf_id = repo.create(wf)

        with patch("sentri.config.paths.SENTRI_HOME", home), patch(
            "sentri.config.paths.DB_PATH", db_path
        ):
            result = runner.invoke(approve_cmd, [wf_id, "--deny", "--reason", "too risky"])

        assert result.exit_code == 0
        assert "too risky" in result.output

        records = audit_repo.find_by_workflow(wf_id)
        assert len(records) == 1
        assert records[0].result == "DENIED"
        assert "denied_reason=too risky" in records[0].evidence

    def test_deny_with_escalate(self, runner, cli_db):
        """Deny with --escalate should transition to ESCALATED instead of COMPLETED."""
        db, db_path, home = cli_db
        repo = WorkflowRepository(db)

        wf = Workflow(
            alert_type="cpu_high",
            database_id="DEV-DB-01",
            environment="DEV",
            status=WorkflowStatus.AWAITING_APPROVAL.value,
        )
        wf_id = repo.create(wf)

        with patch("sentri.config.paths.SENTRI_HOME", home), patch(
            "sentri.config.paths.DB_PATH", db_path
        ):
            result = runner.invoke(approve_cmd, [wf_id, "--deny", "--escalate"])

        assert result.exit_code == 0
        assert "ESCALATED" in result.output

        updated = repo.get(wf_id)
        assert updated.status == WorkflowStatus.ESCALATED.value
