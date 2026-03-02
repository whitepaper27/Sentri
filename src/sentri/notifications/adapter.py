"""Notification adapter ABC and shared context dataclass.

v5.1b: All notification channels implement this interface.
Adding a new channel = new adapter class + config entry. No code changes elsewhere.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

logger = logging.getLogger("sentri.notifications.adapter")


@dataclass
class NotificationContext:
    """All data needed for any notification type.

    Passed to every adapter method. Adapters pick the fields they need.
    """

    workflow_id: str
    database_id: str
    alert_type: str
    environment: str
    risk_level: str = ""
    confidence: float = 0.0
    forward_sql: str = ""
    rollback_sql: str = ""
    reasons: list[str] = field(default_factory=list)
    result: str = ""  # SUCCESS / FAILED
    elapsed_seconds: float = 0.0
    timeout_seconds: int = 0
    denied_by: str = ""
    denial_reason: str = ""

    @property
    def short_id(self) -> str:
        """First 8 chars of workflow_id (used in email subjects)."""
        return self.workflow_id[:8]


class NotificationAdapter(ABC):
    """Base class for all notification channels.

    Subclasses implement the 5 notification types. If a channel doesn't
    support a notification type, return False (no-op).
    """

    @abstractmethod
    def send_approval_request(self, ctx: NotificationContext) -> bool:
        """Notify that a workflow needs DBA approval."""
        ...

    @abstractmethod
    def send_timeout_notification(self, ctx: NotificationContext) -> bool:
        """Notify that an approval request has timed out."""
        ...

    @abstractmethod
    def send_completion_notice(self, ctx: NotificationContext) -> bool:
        """Notify that a workflow completed (success or failure)."""
        ...

    @abstractmethod
    def send_escalation_notice(self, ctx: NotificationContext) -> bool:
        """Notify that a workflow was escalated (blocked or circuit breaker)."""
        ...

    @abstractmethod
    def send_denial_notice(self, ctx: NotificationContext) -> bool:
        """Notify that a workflow was denied by a DBA."""
        ...
