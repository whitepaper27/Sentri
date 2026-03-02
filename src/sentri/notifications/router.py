"""Notification router — dispatches to all configured adapters.

Replaces direct email/slack calls throughout the codebase.
Backwards compatible with existing approvals.email_enabled / slack_webhook_url config.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from .adapter import NotificationAdapter, NotificationContext

if TYPE_CHECKING:
    from sentri.config.settings import Settings

logger = logging.getLogger("sentri.notifications.router")


class NotificationRouter:
    """Holds all configured notification adapters and dispatches to them.

    Usage:
        router = NotificationRouter.from_settings(settings)
        ctx = NotificationContext(workflow_id=..., ...)
        sent = router.send_approval_request(ctx)
    """

    def __init__(self) -> None:
        self._adapters: list[NotificationAdapter] = []

    def add_adapter(self, adapter: NotificationAdapter) -> None:
        """Register a notification adapter."""
        self._adapters.append(adapter)

    @property
    def adapter_count(self) -> int:
        """Number of registered adapters."""
        return len(self._adapters)

    def send_approval_request(self, ctx: NotificationContext) -> int:
        """Send approval request to all adapters. Returns count of successful sends."""
        return self._dispatch("send_approval_request", ctx)

    def send_timeout_notification(self, ctx: NotificationContext) -> int:
        """Send timeout notification to all adapters."""
        return self._dispatch("send_timeout_notification", ctx)

    def send_completion_notice(self, ctx: NotificationContext) -> int:
        """Send completion notice to all adapters."""
        return self._dispatch("send_completion_notice", ctx)

    def send_escalation_notice(self, ctx: NotificationContext) -> int:
        """Send escalation notice to all adapters."""
        return self._dispatch("send_escalation_notice", ctx)

    def send_denial_notice(self, ctx: NotificationContext) -> int:
        """Send denial notice to all adapters."""
        return self._dispatch("send_denial_notice", ctx)

    def _dispatch(self, method_name: str, ctx: NotificationContext) -> int:
        """Call the named method on all adapters. Returns success count."""
        if not self._adapters:
            logger.debug("No notification adapters configured")
            return 0

        success = 0
        for adapter in self._adapters:
            try:
                fn = getattr(adapter, method_name)
                if fn(ctx):
                    success += 1
            except Exception as e:
                logger.error(
                    "Notification adapter %s.%s failed: %s",
                    type(adapter).__name__,
                    method_name,
                    e,
                )
        return success

    @classmethod
    def from_settings(cls, settings: "Settings") -> "NotificationRouter":
        """Build a router from sentri.yaml settings (backwards compatible).

        Reads both legacy config (approvals.email_enabled, approvals.slack_webhook_url)
        and new config (notifications.adapters list).
        """
        router = cls()

        # Legacy: Email adapter from approvals config
        if settings.approvals.email_enabled:
            from .email_adapter import EmailAdapter

            to_addrs_str = settings.approvals.approval_recipients or settings.email.username
            to_addrs = [a.strip() for a in to_addrs_str.split(",") if a.strip()]

            if to_addrs and settings.email.smtp_server:
                adapter = EmailAdapter(
                    smtp_server=settings.email.smtp_server,
                    smtp_port=settings.email.smtp_port,
                    from_addr=settings.email.username,
                    to_addrs=to_addrs,
                    username=settings.email.username,
                    password=settings.email.password,
                    use_tls=settings.email.use_tls,
                )
                router.add_adapter(adapter)
                logger.info("Email adapter configured (to: %s)", ", ".join(to_addrs))

        # Legacy: Slack adapter from approvals config
        if settings.approvals.slack_webhook_url:
            from .slack_adapter import SlackAdapter

            adapter = SlackAdapter(settings.approvals.slack_webhook_url)
            router.add_adapter(adapter)
            logger.info("Slack adapter configured")

        # New: notifications.adapters list (v5.1b)
        if hasattr(settings, "notifications"):
            for adapter_cfg in settings.notifications.adapters:
                if not adapter_cfg.enabled:
                    continue
                try:
                    adapter = _build_adapter(adapter_cfg)
                    if adapter:
                        router.add_adapter(adapter)
                        logger.info(
                            "Notification adapter configured: %s",
                            adapter_cfg.type,
                        )
                except Exception as e:
                    logger.error(
                        "Failed to configure %s adapter: %s",
                        adapter_cfg.type,
                        e,
                    )

        if router.adapter_count == 0:
            logger.warning("No notification adapters configured")

        return router


def _build_adapter(adapter_cfg) -> NotificationAdapter | None:
    """Build a notification adapter from config."""
    adapter_type = adapter_cfg.type.lower()

    if adapter_type == "webhook":
        from .webhook_adapter import WebhookAdapter

        return WebhookAdapter(
            url=adapter_cfg.url,
            headers=adapter_cfg.headers or None,
        )

    if adapter_type == "pagerduty":
        from .pagerduty_adapter import PagerDutyAdapter

        return PagerDutyAdapter(routing_key=adapter_cfg.routing_key)

    if adapter_type == "email":
        # Email via new config (not legacy approvals.email_enabled)
        logger.debug(
            "Email adapter via notifications.adapters — use approvals.email_enabled instead"
        )
        return None

    logger.warning("Unknown notification adapter type: %s", adapter_type)
    return None
