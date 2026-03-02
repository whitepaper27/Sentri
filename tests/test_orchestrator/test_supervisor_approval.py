"""Tests for Supervisor approval handling (APPROVED execution + timeout)."""

from __future__ import annotations

import threading
from unittest.mock import MagicMock, patch

from sentri.core.constants import WorkflowStatus
from sentri.core.models import Workflow
from sentri.orchestrator.supervisor import Supervisor


class TestSupervisorApprovedHandling:
    """Test Supervisor._handle_approved()."""

    def test_approved_workflow_completes(self, agent_context):
        """APPROVED workflow should transition to EXECUTING -> COMPLETED."""
        alert_event = threading.Event()
        supervisor = Supervisor(agent_context, alert_event)

        # Create workflow in APPROVED with stored execution plan
        wf = Workflow(
            alert_type="cpu_high",
            database_id="DEV-DB-01",
            environment="DEV",
            status=WorkflowStatus.APPROVED.value,
            execution_plan='{"forward_sql":"SELECT 1","rollback_sql":"N/A","action_type":"CPU_HIGH","risk_level":"LOW"}',
        )
        wf_id = agent_context.workflow_repo.create(wf)

        # Register a mock agent
        mock_agent = MagicMock()
        mock_agent.name = "sql_tuning_agent"
        supervisor.register_agent("sql_tuning_agent", mock_agent)

        # Load routing rules (use empty rules, fallback will be storage_agent)
        supervisor._loaded = True
        supervisor._routing_rules = []
        supervisor._fallback_agent = "sql_tuning_agent"

        wf_obj = agent_context.workflow_repo.get(wf_id)
        supervisor._handle_approved(wf_obj)

        updated = agent_context.workflow_repo.get(wf_id)
        assert updated.status == WorkflowStatus.COMPLETED.value

    def test_approved_no_plan_escalates(self, agent_context):
        """APPROVED workflow without execution plan should escalate."""
        alert_event = threading.Event()
        supervisor = Supervisor(agent_context, alert_event)
        supervisor._loaded = True

        wf = Workflow(
            alert_type="cpu_high",
            database_id="DEV-DB-01",
            environment="DEV",
            status=WorkflowStatus.APPROVED.value,
            execution_plan="",  # No plan!
        )
        wf_id = agent_context.workflow_repo.create(wf)

        wf_obj = agent_context.workflow_repo.get(wf_id)
        supervisor._handle_approved(wf_obj)

        updated = agent_context.workflow_repo.get(wf_id)
        assert updated.status == WorkflowStatus.ESCALATED.value

    def test_process_cycle_handles_approved(self, agent_context):
        """_process_cycle should pick up APPROVED workflows."""
        alert_event = threading.Event()
        supervisor = Supervisor(agent_context, alert_event)
        supervisor._loaded = True
        supervisor._routing_rules = []
        supervisor._categories = {}
        supervisor._fallback_agent = "storage_agent"

        mock_agent = MagicMock()
        mock_agent.name = "storage_agent"
        supervisor.register_agent("storage_agent", mock_agent)

        wf = Workflow(
            alert_type="tablespace_full",
            database_id="DEV-DB-01",
            environment="DEV",
            status=WorkflowStatus.APPROVED.value,
            execution_plan='{"forward_sql":"ALTER TABLESPACE","rollback_sql":"N/A","action_type":"ADD_DATAFILE","risk_level":"LOW"}',
        )
        wf_id = agent_context.workflow_repo.create(wf)

        supervisor._process_cycle()

        updated = agent_context.workflow_repo.get(wf_id)
        assert updated.status == WorkflowStatus.COMPLETED.value


class TestSupervisorApprovalTimeout:
    """Test Supervisor._check_approval_timeout()."""

    def test_timed_out_workflow_escalates(self, agent_context):
        """AWAITING_APPROVAL past timeout should transition to ESCALATED."""
        # Set a very short timeout
        agent_context.settings.approvals.approval_timeout = 1  # 1 second

        alert_event = threading.Event()
        supervisor = Supervisor(agent_context, alert_event)
        supervisor._loaded = True

        wf = Workflow(
            alert_type="cpu_high",
            database_id="DEV-DB-01",
            environment="DEV",
            status=WorkflowStatus.AWAITING_APPROVAL.value,
        )
        wf_id = agent_context.workflow_repo.create(wf)

        # Backdate created_at to 2 hours ago
        agent_context.db.execute_write(
            "UPDATE workflows SET created_at = datetime('now', '-2 hours') WHERE id = ?",
            (wf_id,),
        )

        wf_obj = agent_context.workflow_repo.get(wf_id)
        supervisor._check_approval_timeout(wf_obj)

        updated = agent_context.workflow_repo.get(wf_id)
        assert updated.status == WorkflowStatus.ESCALATED.value

    def test_not_timed_out_stays_awaiting(self, agent_context):
        """AWAITING_APPROVAL within timeout should not change."""
        agent_context.settings.approvals.approval_timeout = 86400  # 24 hours

        alert_event = threading.Event()
        supervisor = Supervisor(agent_context, alert_event)
        supervisor._loaded = True

        wf = Workflow(
            alert_type="cpu_high",
            database_id="DEV-DB-01",
            environment="DEV",
            status=WorkflowStatus.AWAITING_APPROVAL.value,
        )
        wf_id = agent_context.workflow_repo.create(wf)

        wf_obj = agent_context.workflow_repo.get(wf_id)
        supervisor._check_approval_timeout(wf_obj)

        updated = agent_context.workflow_repo.get(wf_id)
        assert updated.status == WorkflowStatus.AWAITING_APPROVAL.value

    def test_timeout_creates_audit_record(self, agent_context):
        """Timeout should create an APPROVAL_TIMEOUT audit record."""
        agent_context.settings.approvals.approval_timeout = 1

        alert_event = threading.Event()
        supervisor = Supervisor(agent_context, alert_event)
        supervisor._loaded = True

        wf = Workflow(
            alert_type="cpu_high",
            database_id="DEV-DB-01",
            environment="DEV",
            status=WorkflowStatus.AWAITING_APPROVAL.value,
        )
        wf_id = agent_context.workflow_repo.create(wf)

        agent_context.db.execute_write(
            "UPDATE workflows SET created_at = datetime('now', '-2 hours') WHERE id = ?",
            (wf_id,),
        )

        wf_obj = agent_context.workflow_repo.get(wf_id)
        supervisor._check_approval_timeout(wf_obj)

        records = agent_context.audit_repo.find_by_workflow(wf_id)
        assert len(records) == 1
        assert records[0].action_type == "APPROVAL_TIMEOUT"
        assert records[0].result == "TIMEOUT"
        assert "elapsed=" in records[0].evidence
        assert "timeout=" in records[0].evidence

    def test_timeout_sends_notification_email(self, agent_context):
        """Timeout should attempt to send notification email when configured."""
        agent_context.settings.approvals.approval_timeout = 1
        agent_context.settings.approvals.email_enabled = True
        agent_context.settings.email.smtp_server = "smtp.test.com"
        agent_context.settings.email.username = "sentri@test.com"

        alert_event = threading.Event()
        supervisor = Supervisor(agent_context, alert_event)
        supervisor._loaded = True

        wf = Workflow(
            alert_type="cpu_high",
            database_id="DEV-DB-01",
            environment="DEV",
            status=WorkflowStatus.AWAITING_APPROVAL.value,
        )
        wf_id = agent_context.workflow_repo.create(wf)

        agent_context.db.execute_write(
            "UPDATE workflows SET created_at = datetime('now', '-2 hours') WHERE id = ?",
            (wf_id,),
        )

        wf_obj = agent_context.workflow_repo.get(wf_id)
        with patch(
            "sentri.notifications.email_sender.send_timeout_notification_email", return_value=True
        ) as mock_send:
            supervisor._check_approval_timeout(wf_obj)
            mock_send.assert_called_once()
            call_kwargs = mock_send.call_args
            assert call_kwargs[1]["workflow_id"] == wf_id
            assert call_kwargs[1]["database_id"] == "DEV-DB-01"
