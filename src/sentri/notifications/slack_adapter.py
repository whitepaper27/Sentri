"""Slack notification adapter — wraps existing slack.py functions."""

from __future__ import annotations

import logging

from .adapter import NotificationAdapter, NotificationContext
from .slack import send_approval_request, send_completion_notice, send_slack_message

logger = logging.getLogger("sentri.notifications.slack_adapter")


class SlackAdapter(NotificationAdapter):
    """Sends notifications to Slack via incoming webhook.

    Wraps the existing slack.py functions.
    """

    def __init__(self, webhook_url: str):
        self._webhook_url = webhook_url

    def send_approval_request(self, ctx: NotificationContext) -> bool:
        """Send formatted approval request to Slack."""
        return send_approval_request(
            webhook_url=self._webhook_url,
            workflow_id=ctx.workflow_id,
            database=ctx.database_id,
            alert_type=ctx.alert_type,
            proposed_action=ctx.forward_sql,
            risk_level=ctx.risk_level,
        )

    def send_timeout_notification(self, ctx: NotificationContext) -> bool:
        """Send timeout notification to Slack."""
        message = (
            f":clock3: *Sentri Approval Timed Out*\n\n"
            f"*Workflow*: `{ctx.short_id}`\n"
            f"*Database*: `{ctx.database_id}`\n"
            f"*Alert*: `{ctx.alert_type}`\n"
            f"*Environment*: `{ctx.environment}`\n\n"
            f"Approval timed out after {ctx.elapsed_seconds / 3600:.1f}h. "
            f"Workflow has been escalated."
        )
        return send_slack_message(self._webhook_url, message)

    def send_completion_notice(self, ctx: NotificationContext) -> bool:
        """Send completion notice to Slack."""
        return send_completion_notice(
            webhook_url=self._webhook_url,
            workflow_id=ctx.workflow_id,
            database=ctx.database_id,
            alert_type=ctx.alert_type,
            result=ctx.result,
        )

    def send_escalation_notice(self, ctx: NotificationContext) -> bool:
        """Send escalation notice to Slack."""
        reasons_text = "\n".join(f"  - {r}" for r in ctx.reasons) if ctx.reasons else ""
        message = (
            f":rotating_light: *Sentri Escalation*\n\n"
            f"*Workflow*: `{ctx.short_id}`\n"
            f"*Database*: `{ctx.database_id}`\n"
            f"*Alert*: `{ctx.alert_type}`\n"
            f"*Environment*: `{ctx.environment}`\n\n"
            f"Reasons:\n{reasons_text}"
        )
        return send_slack_message(self._webhook_url, message)

    def send_denial_notice(self, ctx: NotificationContext) -> bool:
        """Send denial notice to Slack."""
        reason = ctx.denial_reason or "(no reason given)"
        message = (
            f":no_entry_sign: *Sentri Approval Denied*\n\n"
            f"*Workflow*: `{ctx.short_id}`\n"
            f"*Database*: `{ctx.database_id}`\n"
            f"*Alert*: `{ctx.alert_type}`\n"
            f"*Environment*: `{ctx.environment}`\n"
            f"*Denied by*: {ctx.denied_by}\n"
            f"*Reason*: {reason}"
        )
        return send_slack_message(self._webhook_url, message)
