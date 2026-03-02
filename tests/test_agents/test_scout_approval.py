"""Tests for Scout approval reply detection."""

from __future__ import annotations

import email
from email.mime.text import MIMEText

from sentri.agents.scout import ScoutAgent
from sentri.core.constants import WorkflowStatus
from sentri.core.models import Workflow


def _make_email_msg(
    subject: str, body: str, from_addr: str = "dba@test.com"
) -> email.message.Message:
    """Create a simple email message for testing."""
    msg = MIMEText(body, "plain")
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["Message-ID"] = f"<test-{hash(subject)}@test.com>"
    return msg


class TestScoutApprovalReply:
    """Test approval reply detection in Scout."""

    def test_detects_approved_reply(self, agent_context):
        """Scout should detect APPROVED reply and transition workflow."""
        scout = ScoutAgent(agent_context)

        # Create a workflow in AWAITING_APPROVAL
        wf = Workflow(
            alert_type="cpu_high",
            database_id="DEV-DB-01",
            environment="DEV",
            status=WorkflowStatus.AWAITING_APPROVAL.value,
        )
        wf_id = agent_context.workflow_repo.create(wf)
        short_id = wf_id[:8]

        msg = _make_email_msg(
            subject=f"Re: [SENTRI] Approval needed: cpu_high on DEV-DB-01 [WF:{short_id}]",
            body="APPROVED\n\nLooks good, go ahead.",
        )

        result = scout._check_approval_reply(msg, "<test-approved@test.com>")
        assert result is True

        updated = agent_context.workflow_repo.get(wf_id)
        assert updated.status == WorkflowStatus.APPROVED.value

    def test_detects_denied_reply(self, agent_context):
        """Scout should detect DENIED reply and transition to COMPLETED."""
        scout = ScoutAgent(agent_context)

        wf = Workflow(
            alert_type="tablespace_full",
            database_id="PROD-DB-07",
            environment="PROD",
            status=WorkflowStatus.AWAITING_APPROVAL.value,
        )
        wf_id = agent_context.workflow_repo.create(wf)
        short_id = wf_id[:8]

        msg = _make_email_msg(
            subject=f"Re: [SENTRI] Approval needed [WF:{short_id}]",
            body="DENIED - too risky right now",
        )

        result = scout._check_approval_reply(msg, "<test-denied@test.com>")
        assert result is True

        updated = agent_context.workflow_repo.get(wf_id)
        # Scout now leaves DENIED for Supervisor to handle (v5.2)
        assert updated.status == WorkflowStatus.DENIED.value

    def test_ignores_non_approval_email(self, agent_context):
        """Emails without [WF:] tag should not be consumed."""
        scout = ScoutAgent(agent_context)

        msg = _make_email_msg(
            subject="OEM Alert: tablespace_full on DEV-DB-01",
            body="Tablespace USERS is 95% full",
        )

        result = scout._check_approval_reply(msg, "<test-alert@test.com>")
        assert result is False

    def test_approved_in_body_not_subject(self, agent_context):
        """APPROVED keyword in body should work (not just subject)."""
        scout = ScoutAgent(agent_context)

        wf = Workflow(
            alert_type="cpu_high",
            database_id="DEV-DB-01",
            environment="DEV",
            status=WorkflowStatus.AWAITING_APPROVAL.value,
        )
        wf_id = agent_context.workflow_repo.create(wf)
        short_id = wf_id[:8]

        msg = _make_email_msg(
            subject=f"Re: [SENTRI] Approval needed [WF:{short_id}]",
            body="Yes, APPROVED. Please proceed.",
        )

        result = scout._check_approval_reply(msg, "<test@test.com>")
        assert result is True

        updated = agent_context.workflow_repo.get(wf_id)
        assert updated.status == WorkflowStatus.APPROVED.value

    def test_no_decision_in_reply(self, agent_context):
        """Reply with [WF:] tag but no APPROVED/DENIED should be consumed but no transition."""
        scout = ScoutAgent(agent_context)

        wf = Workflow(
            alert_type="cpu_high",
            database_id="DEV-DB-01",
            environment="DEV",
            status=WorkflowStatus.AWAITING_APPROVAL.value,
        )
        wf_id = agent_context.workflow_repo.create(wf)
        short_id = wf_id[:8]

        msg = _make_email_msg(
            subject=f"Re: [SENTRI] Approval needed [WF:{short_id}]",
            body="I need more details first.",
        )

        result = scout._check_approval_reply(msg, "<test@test.com>")
        assert result is True  # Consumed

        updated = agent_context.workflow_repo.get(wf_id)
        assert updated.status == WorkflowStatus.AWAITING_APPROVAL.value  # Unchanged

    def test_wrong_status_ignored(self, agent_context):
        """Reply for a workflow not in AWAITING_APPROVAL should be consumed, no error."""
        scout = ScoutAgent(agent_context)

        wf = Workflow(
            alert_type="cpu_high",
            database_id="DEV-DB-01",
            environment="DEV",
            status=WorkflowStatus.COMPLETED.value,
        )
        wf_id = agent_context.workflow_repo.create(wf)
        short_id = wf_id[:8]

        msg = _make_email_msg(
            subject=f"Re: [SENTRI] [WF:{short_id}]",
            body="APPROVED",
        )

        result = scout._check_approval_reply(msg, "<test@test.com>")
        assert result is True  # Consumed (no error)

        updated = agent_context.workflow_repo.get(wf_id)
        assert updated.status == WorkflowStatus.COMPLETED.value  # Unchanged

    def test_unknown_workflow_id(self, agent_context):
        """Reply with unrecognized [WF:] tag should be consumed without error."""
        scout = ScoutAgent(agent_context)

        msg = _make_email_msg(
            subject="Re: [SENTRI] [WF:deadbeef]",
            body="APPROVED",
        )

        result = scout._check_approval_reply(msg, "<test@test.com>")
        assert result is True  # Consumed (no crash)

    def test_approved_wakes_alert_event(self, agent_context):
        """APPROVED should set the alert_event to wake Supervisor."""
        scout = ScoutAgent(agent_context)

        wf = Workflow(
            alert_type="cpu_high",
            database_id="DEV-DB-01",
            environment="DEV",
            status=WorkflowStatus.AWAITING_APPROVAL.value,
        )
        wf_id = agent_context.workflow_repo.create(wf)
        short_id = wf_id[:8]

        assert not scout.alert_event.is_set()

        msg = _make_email_msg(
            subject=f"Re: [WF:{short_id}]",
            body="APPROVED",
        )
        scout._check_approval_reply(msg, "<test@test.com>")

        assert scout.alert_event.is_set()

    def test_approved_creates_audit_record(self, agent_context):
        """APPROVED reply should create an audit record with WHO and channel."""
        scout = ScoutAgent(agent_context)

        wf = Workflow(
            alert_type="cpu_high",
            database_id="DEV-DB-01",
            environment="DEV",
            status=WorkflowStatus.AWAITING_APPROVAL.value,
            execution_plan='{"forward_sql":"SELECT 1"}',
        )
        wf_id = agent_context.workflow_repo.create(wf)
        short_id = wf_id[:8]

        msg = _make_email_msg(
            subject=f"Re: [WF:{short_id}]",
            body="APPROVED",
            from_addr="lead.dba@company.com",
        )
        scout._check_approval_reply(msg, "<test@test.com>")

        # Check audit record
        records = agent_context.audit_repo.find_by_workflow(wf_id)
        assert len(records) == 1
        assert records[0].action_type == "APPROVAL_DECISION"
        assert records[0].result == "APPROVED"
        assert records[0].approved_by == "lead.dba@company.com"
        assert "channel=email" in records[0].evidence

        # Check approved_by populated on workflow
        updated = agent_context.workflow_repo.get(wf_id)
        assert updated.approved_by == "lead.dba@company.com"
        assert updated.approved_at is not None

    def test_denied_extracts_reason(self, agent_context):
        """DENIED reply should extract reason and store in audit evidence."""
        scout = ScoutAgent(agent_context)

        wf = Workflow(
            alert_type="tablespace_full",
            database_id="PROD-DB-07",
            environment="PROD",
            status=WorkflowStatus.AWAITING_APPROVAL.value,
        )
        wf_id = agent_context.workflow_repo.create(wf)
        short_id = wf_id[:8]

        msg = _make_email_msg(
            subject=f"Re: [WF:{short_id}]",
            body="DENIED - too risky during peak hours",
            from_addr="senior.dba@company.com",
        )
        scout._check_approval_reply(msg, "<test@test.com>")

        records = agent_context.audit_repo.find_by_workflow(wf_id)
        assert len(records) == 1
        assert records[0].result == "DENIED"
        assert "denied_reason=" in records[0].evidence
        assert "too risky during peak hours" in records[0].evidence
