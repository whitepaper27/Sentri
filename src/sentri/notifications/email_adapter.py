"""Email notification adapter — wraps existing email_sender.py functions.

No rewrite of email logic. This adapter delegates to the proven SMTP functions.
"""

from __future__ import annotations

import logging

from .adapter import NotificationAdapter, NotificationContext
from .email_sender import send_approval_request_email, send_timeout_notification_email

logger = logging.getLogger("sentri.notifications.email_adapter")


class EmailAdapter(NotificationAdapter):
    """Sends notifications via SMTP email.

    Wraps the existing email_sender.py functions that are already
    production-tested (HTML formatting, [WF:] tracking tags, etc.).
    """

    def __init__(
        self,
        smtp_server: str,
        smtp_port: int,
        from_addr: str,
        to_addrs: list[str],
        username: str = "",
        password: str = "",
        use_tls: bool = True,
    ):
        self._smtp_server = smtp_server
        self._smtp_port = smtp_port
        self._from_addr = from_addr
        self._to_addrs = to_addrs
        self._username = username
        self._password = password
        self._use_tls = use_tls

    def send_approval_request(self, ctx: NotificationContext) -> bool:
        """Send approval request email with [WF:xxxxxxxx] tracking tag."""
        return send_approval_request_email(
            smtp_server=self._smtp_server,
            smtp_port=self._smtp_port,
            from_addr=self._from_addr,
            to_addrs=self._to_addrs,
            workflow_id=ctx.workflow_id,
            database_id=ctx.database_id,
            alert_type=ctx.alert_type,
            environment=ctx.environment,
            forward_sql=ctx.forward_sql,
            rollback_sql=ctx.rollback_sql,
            risk_level=ctx.risk_level,
            confidence=ctx.confidence,
            reasons=ctx.reasons,
            username=self._username,
            password=self._password,
            use_tls=self._use_tls,
        )

    def send_timeout_notification(self, ctx: NotificationContext) -> bool:
        """Send timeout notification email."""
        return send_timeout_notification_email(
            smtp_server=self._smtp_server,
            smtp_port=self._smtp_port,
            from_addr=self._from_addr,
            to_addrs=self._to_addrs,
            workflow_id=ctx.workflow_id,
            database_id=ctx.database_id,
            alert_type=ctx.alert_type,
            environment=ctx.environment,
            elapsed_seconds=ctx.elapsed_seconds,
            timeout_seconds=ctx.timeout_seconds,
            username=self._username,
            password=self._password,
            use_tls=self._use_tls,
        )

    def send_completion_notice(self, ctx: NotificationContext) -> bool:
        """Email completion notice (not yet implemented — returns False)."""
        logger.debug("Email completion notice not implemented, skipping")
        return False

    def send_escalation_notice(self, ctx: NotificationContext) -> bool:
        """Email escalation notice (not yet implemented — returns False)."""
        logger.debug("Email escalation notice not implemented, skipping")
        return False

    def send_denial_notice(self, ctx: NotificationContext) -> bool:
        """Email denial notice (not yet implemented — returns False)."""
        logger.debug("Email denial notice not implemented, skipping")
        return False
