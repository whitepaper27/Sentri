"""Agent 5: The Analyst - Learning engine.

v2.0: Observes outcomes, proposes improvements, runs LLM judge consensus,
applies safe versioned updates to .md policy files.

Pipeline: observe -> (propose -> judge -> apply) -> monitor
- observe: runs on every completed workflow
- propose/judge/apply: runs when enough observations accumulate
- monitor: tracks post-improvement performance

Disabled by default (learning.enabled: false in sentri.yaml).
"""

from __future__ import annotations

import logging
from typing import Optional

from sentri.core.llm_interface import LLMProvider, NoOpLLMProvider
from sentri.db.learning_repo import LearningRepository
from sentri.db.md_version_repo import MdVersionRepository

from .base import AgentContext, BaseAgent
from .learning.applier import Applier
from .learning.judge import JudgePanel
from .learning.monitor import Monitor
from .learning.observer import Observer
from .learning.proposer import Proposer

logger = logging.getLogger("sentri.agents.analyst")


class AnalystAgent(BaseAgent):
    """Learning engine: observes, proposes, judges, applies, monitors."""

    def __init__(
        self,
        context: AgentContext,
        learning_repo: Optional[LearningRepository] = None,
        md_version_repo: Optional[MdVersionRepository] = None,
        llm_provider: Optional[LLMProvider] = None,
        judge_providers: Optional[list[LLMProvider]] = None,
    ):
        super().__init__("analyst", context)

        self._enabled = context.settings.learning.enabled
        learning_cfg = context.settings.learning

        # Repositories
        self._learning_repo = learning_repo or LearningRepository(context.db)
        self._md_version_repo = md_version_repo or MdVersionRepository(context.db)

        # LLM provider (for proposer)
        self._llm = llm_provider or NoOpLLMProvider()

        # Pipeline components
        self._observer = Observer(self._learning_repo)
        self._proposer = Proposer(
            self._learning_repo,
            llm_provider=self._llm,
            min_observations=learning_cfg.min_observations,
        )

        # Judge panel: use diverse providers if available, else fall back to single
        if judge_providers:
            judge_llms = judge_providers
        elif llm_provider:
            judge_llms = [llm_provider]
        else:
            judge_llms = []

        self._judge = JudgePanel(
            llm_providers=judge_llms,
            judge_count=learning_cfg.judge_count,
            required_agreement=learning_cfg.judge_agreement,
        )

        from sentri.config.paths import ALERTS_DIR

        self._applier = Applier(
            self._md_version_repo,
            alerts_dir=ALERTS_DIR,
        )
        self._monitor = Monitor(
            self._learning_repo,
            monitoring_days=learning_cfg.monitoring_days,
        )

    def process(self, workflow_id: str) -> dict:
        """Process a completed workflow through the learning pipeline.

        Always observes. Only proposes/judges/applies if learning is enabled.
        """
        workflow = self.context.workflow_repo.get(workflow_id)
        if not workflow:
            return {"status": "failure", "error": f"Workflow {workflow_id} not found"}

        result = {
            "status": "success",
            "agent": "analyst",
            "workflow_id": workflow_id,
            "observation": None,
            "proposal": None,
            "judgment": None,
            "application": None,
        }

        # Step 1: Always observe (even if learning is disabled)
        observation = self._observer.observe(workflow)
        if observation:
            result["observation"] = {
                "id": observation.id,
                "type": observation.observation_type,
                "alert_type": observation.alert_type,
            }
            logger.info(
                "Observation: %s for %s (workflow %s)",
                observation.observation_type,
                workflow.alert_type,
                workflow_id,
            )

        # Step 2-4: Only run improvement pipeline if learning is enabled
        if not self._enabled:
            result["note"] = "Learning disabled — observation captured only"
            return result

        if not observation:
            return result

        # Step 2: Check for proposal
        proposal = self._proposer.check_and_propose(workflow.alert_type)
        if not proposal:
            return result

        result["proposal"] = {
            "section": proposal.get("section"),
            "reasoning": proposal.get("reasoning"),
            "source": proposal.get("source"),
        }
        logger.info(
            "Proposal generated for %s: %s",
            workflow.alert_type,
            proposal.get("section"),
        )

        # Step 3: Judge consensus
        judgment = self._judge.evaluate(proposal)
        result["judgment"] = {
            "approved": judgment["approved"],
            "agreement": f"{judgment['agreement_count']}/{judgment['total_judges']}",
        }

        if not judgment["approved"]:
            logger.info(
                "Proposal rejected by judges (%d/%d)",
                judgment["agreement_count"],
                judgment["total_judges"],
            )
            return result

        # Step 4: Apply (backup + version, human review still required)
        application = self._applier.apply(proposal)
        result["application"] = application

        if application.get("applied"):
            logger.info(
                "Improvement recorded for %s (backup: %s)",
                workflow.alert_type,
                application.get("backup_path"),
            )

        return result

    def get_learning_summary(self) -> dict:
        """Get overall learning engine status and metrics."""
        total_observations = self._learning_repo.count_total()
        by_alert_type = self._learning_repo.count_by_alert_type()
        impact_summaries = self._monitor.get_all_summaries()
        tracked_files = self._md_version_repo.list_tracked_files()

        return {
            "enabled": self._enabled,
            "llm_available": self._llm.is_available(),
            "total_observations": total_observations,
            "observations_by_alert_type": by_alert_type,
            "impact_summaries": impact_summaries,
            "tracked_policy_files": len(tracked_files),
        }
