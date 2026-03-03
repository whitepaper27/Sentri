"""Tests for `sentri resolve` CLI command."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from click.testing import CliRunner

from sentri.cli.resolve_cmd import resolve_cmd
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


class TestResolveCmd:
    """Test the resolve CLI command."""

    def test_resolve_awaiting_approval(self, runner, cli_db):
        """Should resolve AWAITING_APPROVAL -> COMPLETED."""
        db, db_path, home = cli_db
        repo = WorkflowRepository(db)

        wf = Workflow(
            alert_type="cpu_high",
            database_id="DEV-DB-01",
            environment="DEV",
            status=WorkflowStatus.AWAITING_APPROVAL.value,
        )
        wf_id = repo.create(wf)

        with (
            patch("sentri.config.paths.SENTRI_HOME", home),
            patch("sentri.config.paths.DB_PATH", db_path),
        ):
            result = runner.invoke(resolve_cmd, [wf_id, "--reason", "Fixed manually"])

        assert result.exit_code == 0
        assert "resolved" in result.output
        assert "Fixed manually" in result.output

        updated = repo.get(wf_id)
        assert updated.status == WorkflowStatus.COMPLETED.value

    def test_resolve_detected_workflow(self, runner, cli_db):
        """Should resolve DETECTED -> COMPLETED."""
        db, db_path, home = cli_db
        repo = WorkflowRepository(db)

        wf = Workflow(
            alert_type="tablespace_full",
            database_id="PROD-DB-07",
            environment="PROD",
            status=WorkflowStatus.DETECTED.value,
        )
        wf_id = repo.create(wf)

        with (
            patch("sentri.config.paths.SENTRI_HOME", home),
            patch("sentri.config.paths.DB_PATH", db_path),
        ):
            result = runner.invoke(resolve_cmd, [wf_id])

        assert result.exit_code == 0

        updated = repo.get(wf_id)
        assert updated.status == WorkflowStatus.COMPLETED.value

    def test_resolve_with_escalate(self, runner, cli_db):
        """Should escalate instead of completing when --escalate used."""
        db, db_path, home = cli_db
        repo = WorkflowRepository(db)

        wf = Workflow(
            alert_type="cpu_high",
            database_id="DEV-DB-01",
            environment="DEV",
            status=WorkflowStatus.AWAITING_APPROVAL.value,
        )
        wf_id = repo.create(wf)

        with (
            patch("sentri.config.paths.SENTRI_HOME", home),
            patch("sentri.config.paths.DB_PATH", db_path),
        ):
            result = runner.invoke(resolve_cmd, [wf_id, "--escalate"])

        assert result.exit_code == 0
        assert "ESCALATED" in result.output

        updated = repo.get(wf_id)
        assert updated.status == WorkflowStatus.ESCALATED.value

    def test_resolve_creates_audit_record(self, runner, cli_db):
        """Should create MANUAL_RESOLUTION audit record."""
        db, db_path, home = cli_db
        repo = WorkflowRepository(db)
        audit_repo = AuditRepository(db)

        wf = Workflow(
            alert_type="cpu_high",
            database_id="DEV-DB-01",
            environment="DEV",
            status=WorkflowStatus.AWAITING_APPROVAL.value,
        )
        wf_id = repo.create(wf)

        with (
            patch("sentri.config.paths.SENTRI_HOME", home),
            patch("sentri.config.paths.DB_PATH", db_path),
        ):
            result = runner.invoke(
                resolve_cmd,
                [
                    wf_id,
                    "--by",
                    "Senior DBA",
                    "--reason",
                    "Applied fix manually",
                ],
            )

        assert result.exit_code == 0

        records = audit_repo.find_by_workflow(wf_id)
        assert len(records) == 1
        assert records[0].action_type == "MANUAL_RESOLUTION"
        assert records[0].approved_by == "Senior DBA"
        assert "reason=Applied fix manually" in records[0].evidence

    def test_resolve_terminal_rejected(self, runner, cli_db):
        """Should reject workflows in terminal state."""
        db, db_path, home = cli_db
        repo = WorkflowRepository(db)

        wf = Workflow(
            alert_type="cpu_high",
            database_id="DEV-DB-01",
            environment="DEV",
            status=WorkflowStatus.COMPLETED.value,
        )
        wf_id = repo.create(wf)

        with (
            patch("sentri.config.paths.SENTRI_HOME", home),
            patch("sentri.config.paths.DB_PATH", db_path),
        ):
            result = runner.invoke(resolve_cmd, [wf_id])

        assert result.exit_code != 0

    def test_resolve_not_found(self, runner, cli_db):
        """Should error when workflow doesn't exist."""
        db, db_path, home = cli_db

        with (
            patch("sentri.config.paths.SENTRI_HOME", home),
            patch("sentri.config.paths.DB_PATH", db_path),
        ):
            result = runner.invoke(resolve_cmd, ["nonexistent-id-12345"])

        assert result.exit_code != 0

    def test_resolve_partial_id(self, runner, cli_db):
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

        with (
            patch("sentri.config.paths.SENTRI_HOME", home),
            patch("sentri.config.paths.DB_PATH", db_path),
        ):
            result = runner.invoke(resolve_cmd, [short_id])

        assert result.exit_code == 0

        updated = repo.get(wf_id)
        assert updated.status == WorkflowStatus.COMPLETED.value
