"""PagerDuty notification adapter — Events API v2.

Triggers incidents for approval requests and escalations.
Resolves incidents on completion.

Requires a PagerDuty Events API v2 integration key (routing_key).
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request

from .adapter import NotificationAdapter, NotificationContext

logger = logging.getLogger("sentri.notifications.pagerduty")

# Default severity mapping: Sentri risk level → PagerDuty severity
_DEFAULT_SEVERITY_MAP = {
    "CRITICAL": "critical",
    "HIGH": "error",
    "MEDIUM": "warning",
    "LOW": "info",
    "": "warning",
}


class PagerDutyAdapter(NotificationAdapter):
    """Sends notifications via PagerDuty Events API v2.

    - Approval requests → trigger incident (severity based on risk level)
    - Timeouts → trigger incident (error severity)
    - Escalations → trigger incident (critical severity)
    - Completions → resolve incident
    """

    EVENTS_URL = "https://events.pagerduty.com/v2/enqueue"

    def __init__(
        self,
        routing_key: str,
        severity_map: dict[str, str] | None = None,
    ):
        self._routing_key = routing_key
        self._severity_map = severity_map or _DEFAULT_SEVERITY_MAP

    def send_approval_request(self, ctx: NotificationContext) -> bool:
        """Trigger a PagerDuty incident for approval request."""
        severity = self._severity_map.get(ctx.risk_level.upper(), "warning")
        return self._send_event(
            action="trigger",
            dedup_key=f"sentri-{ctx.short_id}",
            summary=(
                f"[SENTRI] Approval needed: {ctx.alert_type} on "
                f"{ctx.database_id} ({ctx.environment})"
            ),
            severity=severity,
            source=ctx.database_id,
            custom_details={
                "workflow_id": ctx.workflow_id,
                "alert_type": ctx.alert_type,
                "risk_level": ctx.risk_level,
                "confidence": ctx.confidence,
                "forward_sql": ctx.forward_sql[:500],
                "reasons": ctx.reasons,
            },
        )

    def send_timeout_notification(self, ctx: NotificationContext) -> bool:
        """Trigger a PagerDuty incident for approval timeout."""
        return self._send_event(
            action="trigger",
            dedup_key=f"sentri-timeout-{ctx.short_id}",
            summary=(
                f"[SENTRI] Approval timed out: {ctx.alert_type} on "
                f"{ctx.database_id} ({ctx.environment})"
            ),
            severity="error",
            source=ctx.database_id,
            custom_details={
                "workflow_id": ctx.workflow_id,
                "elapsed_seconds": ctx.elapsed_seconds,
                "timeout_seconds": ctx.timeout_seconds,
            },
        )

    def send_completion_notice(self, ctx: NotificationContext) -> bool:
        """Resolve the PagerDuty incident on completion."""
        return self._send_event(
            action="resolve",
            dedup_key=f"sentri-{ctx.short_id}",
            summary=(
                f"[SENTRI] Resolved: {ctx.alert_type} on " f"{ctx.database_id} — {ctx.result}"
            ),
            severity="info",
            source=ctx.database_id,
        )

    def send_escalation_notice(self, ctx: NotificationContext) -> bool:
        """Trigger a critical PagerDuty incident for escalation."""
        return self._send_event(
            action="trigger",
            dedup_key=f"sentri-escalation-{ctx.short_id}",
            summary=(
                f"[SENTRI] ESCALATED: {ctx.alert_type} on " f"{ctx.database_id} ({ctx.environment})"
            ),
            severity="critical",
            source=ctx.database_id,
            custom_details={
                "workflow_id": ctx.workflow_id,
                "reasons": ctx.reasons,
            },
        )

    def send_denial_notice(self, ctx: NotificationContext) -> bool:
        """Resolve the PagerDuty incident on denial (action not taken)."""
        return self._send_event(
            action="resolve",
            dedup_key=f"sentri-{ctx.short_id}",
            summary=(
                f"[SENTRI] Denied: {ctx.alert_type} on "
                f"{ctx.database_id} — denied by {ctx.denied_by}"
            ),
            severity="info",
            source=ctx.database_id,
        )

    def _send_event(
        self,
        action: str,
        dedup_key: str,
        summary: str,
        severity: str,
        source: str,
        custom_details: dict | None = None,
    ) -> bool:
        """Send an event to PagerDuty Events API v2."""
        if not self._routing_key:
            logger.debug("No PagerDuty routing key configured, skipping")
            return False

        payload: dict = {
            "routing_key": self._routing_key,
            "event_action": action,
            "dedup_key": dedup_key,
        }

        if action != "resolve":
            payload["payload"] = {
                "summary": summary,
                "severity": severity,
                "source": source,
                "component": "sentri",
                "group": "database",
                "class": "dba-automation",
            }
            if custom_details:
                payload["payload"]["custom_details"] = custom_details

        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            self.EVENTS_URL,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                if resp.status < 300:
                    logger.info(
                        "PagerDuty event sent: %s %s",
                        action,
                        dedup_key,
                    )
                    return True
                logger.warning("PagerDuty returned status %d", resp.status)
                return False
        except urllib.error.URLError as e:
            logger.error("PagerDuty event failed: %s", e)
            return False
