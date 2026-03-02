"""Base agent class and shared context for all Sentri agents."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from sentri.config.settings import Settings
    from sentri.db.audit_repo import AuditRepository
    from sentri.db.connection import Database
    from sentri.db.environment_repo import EnvironmentRepository
    from sentri.db.workflow_repo import WorkflowRepository
    from sentri.policy.loader import PolicyLoader


@dataclass
class AgentContext:
    """Shared context injected into all agents."""

    db: Database
    workflow_repo: WorkflowRepository
    audit_repo: AuditRepository
    environment_repo: EnvironmentRepository
    policy_loader: PolicyLoader
    settings: Settings
    oracle_pool: Any = None  # Optional Oracle connection pool


class BaseAgent(ABC):
    """Abstract base for all Sentri agents.

    The process(workflow_id) interface is designed for future
    LangGraph node compatibility.
    """

    def __init__(self, name: str, context: AgentContext):
        self.name = name
        self.context = context
        self.logger = logging.getLogger(f"sentri.agents.{name}")

    @abstractmethod
    def process(self, workflow_id: str) -> dict:
        """Process a workflow step.

        Returns a dict with at minimum {"status": "success"|"failure"}.
        """
        ...

    def _load_alert_policy(self, alert_type: str) -> dict:
        """Load the policy .md file for an alert type."""
        return self.context.policy_loader.load_alert(alert_type)
