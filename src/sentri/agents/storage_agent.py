"""Storage Agent — wraps the existing v1-v4 pipeline as a v5.0 specialist.

Handles: tablespace_full, temp_full, archive_dest_full, high_undo_usage.
Delegates to: Auditor (verify), Researcher (investigate+propose),
              Executor (execute), Analyst (learn).

This is the current pipeline wrapped as a specialist — zero rewrites.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Optional

from sentri.core.llm_interface import LLMProvider
from sentri.core.models import ResearchOption, Workflow

from .base import AgentContext
from .specialist_base import SpecialistBase

if TYPE_CHECKING:
    from sentri.agents.auditor import AuditorAgent
    from sentri.agents.executor import ExecutorAgent
    from sentri.agents.researcher import ResearcherAgent
    from sentri.llm.cost_tracker import CostTracker
    from sentri.orchestrator.safety_mesh import SafetyMesh

logger = logging.getLogger("sentri.agents.storage_agent")


class StorageAgent(SpecialistBase):
    """Specialist for storage alerts. Wraps the existing pipeline."""

    HANDLED_ALERTS = frozenset(
        {
            "tablespace_full",
            "temp_full",
            "archive_dest_full",
            "high_undo_usage",
        }
    )

    def __init__(
        self,
        context: AgentContext,
        safety_mesh: "SafetyMesh",
        auditor: Optional["AuditorAgent"] = None,
        researcher: Optional["ResearcherAgent"] = None,
        executor: Optional["ExecutorAgent"] = None,
        analyst=None,
        llm_provider: Optional[LLMProvider] = None,
        cost_tracker: Optional["CostTracker"] = None,
        investigation_store=None,
        notification_router=None,
    ):
        super().__init__(
            "storage_agent",
            context,
            safety_mesh,
            llm_provider,
            cost_tracker,
            investigation_store=investigation_store,
            notification_router=notification_router,
        )
        self._auditor = auditor
        self._researcher = researcher
        self._executor = executor
        self._analyst = analyst

    def verify(self, workflow: Workflow) -> tuple[bool, float]:
        """Delegate verification to the existing Auditor agent."""
        if not self._auditor:
            # No auditor → assume verified with moderate confidence
            return True, 0.80

        result = self._auditor.process(workflow.id)
        status = result.get("status", "failure")

        if status == "verified":
            confidence = result.get("confidence", 0.80)
            return True, confidence

        return False, result.get("confidence", 0.0)

    def investigate(self, workflow: Workflow) -> dict:
        """No separate investigation — Researcher handles investigate+propose."""
        return {}

    def propose(
        self,
        workflow: Workflow,
        investigation: dict,
    ) -> list[ResearchOption]:
        """Delegate to the existing Researcher agent.

        The Researcher already implements:
        - Agentic (LLM + 12 DBA tools)
        - One-shot (LLM only)
        - Template (.md policy)
        """
        if not self._researcher:
            return []

        result = self._researcher.process(workflow.id)
        if result.get("status") != "success":
            self.logger.warning(
                "Researcher failed for %s: %s",
                workflow.id,
                result.get("error", "unknown"),
            )
            return []

        return result.get("options", [])

    def learn(
        self,
        workflow: Workflow,
        selected: ResearchOption,
        result: dict,
    ) -> None:
        """Delegate learning to the existing Analyst agent."""
        if self._analyst:
            try:
                self._analyst.process(workflow.id)
            except Exception as e:
                self.logger.warning("Analyst failed for %s: %s", workflow.id, e)

        # Also call parent's default logging
        super().learn(workflow, selected, result)
