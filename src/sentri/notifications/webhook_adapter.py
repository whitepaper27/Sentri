"""Generic HTTP webhook notification adapter.

Works with any HTTP endpoint that accepts JSON payloads:
Slack, Microsoft Teams, custom dashboards, monitoring systems, etc.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request

from .adapter import NotificationAdapter, NotificationContext

logger = logging.getLogger("sentri.notifications.webhook")


class WebhookAdapter(NotificationAdapter):
    """Sends notifications via generic HTTP POST webhook.

    Configurable URL and headers. Sends a structured JSON payload
    for each notification type.
    """

    def __init__(
        self,
        url: str,
        headers: dict[str, str] | None = None,
    ):
        self._url = url
        self._headers = headers or {"Content-Type": "application/json"}

    def send_approval_request(self, ctx: NotificationContext) -> bool:
        """POST approval request as JSON."""
        payload = {
            "event": "approval_request",
            "workflow_id": ctx.workflow_id,
            "database_id": ctx.database_id,
            "alert_type": ctx.alert_type,
            "environment": ctx.environment,
            "risk_level": ctx.risk_level,
            "confidence": ctx.confidence,
            "forward_sql": ctx.forward_sql,
            "rollback_sql": ctx.rollback_sql,
            "reasons": ctx.reasons,
        }
        return self._post(payload)

    def send_timeout_notification(self, ctx: NotificationContext) -> bool:
        """POST timeout notification as JSON."""
        payload = {
            "event": "approval_timeout",
            "workflow_id": ctx.workflow_id,
            "database_id": ctx.database_id,
            "alert_type": ctx.alert_type,
            "environment": ctx.environment,
            "elapsed_seconds": ctx.elapsed_seconds,
            "timeout_seconds": ctx.timeout_seconds,
        }
        return self._post(payload)

    def send_completion_notice(self, ctx: NotificationContext) -> bool:
        """POST completion notice as JSON."""
        payload = {
            "event": "workflow_completed",
            "workflow_id": ctx.workflow_id,
            "database_id": ctx.database_id,
            "alert_type": ctx.alert_type,
            "environment": ctx.environment,
            "result": ctx.result,
        }
        return self._post(payload)

    def send_escalation_notice(self, ctx: NotificationContext) -> bool:
        """POST escalation notice as JSON."""
        payload = {
            "event": "workflow_escalated",
            "workflow_id": ctx.workflow_id,
            "database_id": ctx.database_id,
            "alert_type": ctx.alert_type,
            "environment": ctx.environment,
            "reasons": ctx.reasons,
        }
        return self._post(payload)

    def send_denial_notice(self, ctx: NotificationContext) -> bool:
        """POST denial notice as JSON."""
        payload = {
            "event": "approval_denied",
            "workflow_id": ctx.workflow_id,
            "database_id": ctx.database_id,
            "alert_type": ctx.alert_type,
            "environment": ctx.environment,
            "denied_by": ctx.denied_by,
            "denial_reason": ctx.denial_reason,
        }
        return self._post(payload)

    def _post(self, payload: dict) -> bool:
        """Send JSON payload to the configured webhook URL."""
        if not self._url:
            logger.debug("No webhook URL configured, skipping")
            return False

        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            self._url,
            data=data,
            headers=self._headers,
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                if resp.status < 300:
                    logger.info("Webhook sent: %s → %s", payload.get("event"), self._url)
                    return True
                logger.warning("Webhook returned status %d", resp.status)
                return False
        except urllib.error.URLError as e:
            logger.error("Webhook failed (%s): %s", self._url, e)
            return False
