"""Observer: captures learning observations from completed workflows.

Examines completed/failed/rolled-back workflows and records structured
observations in the learning_observations table for later analysis.
"""

from __future__ import annotations

import json
import logging
from typing import Optional

from sentri.core.constants import WorkflowStatus
from sentri.core.models import LearningObservation, Workflow
from sentri.db.learning_repo import LearningRepository

logger = logging.getLogger("sentri.learning.observer")


class Observer:
    """Captures observations from completed workflows."""

    def __init__(self, learning_repo: LearningRepository):
        self._repo = learning_repo

    def observe(self, workflow: Workflow) -> Optional[LearningObservation]:
        """Examine a completed workflow and capture an observation.

        Returns the observation if one was created, None otherwise.
        """
        obs_type = self._classify_observation(workflow)
        if obs_type is None:
            return None

        data = self._extract_observation_data(workflow)

        obs = LearningObservation(
            workflow_id=workflow.id,
            alert_type=workflow.alert_type,
            database_id=workflow.database_id,
            observation_type=obs_type,
            data=json.dumps(data),
        )

        obs_id = self._repo.create(obs)
        obs.id = obs_id

        logger.info(
            "Observation captured: %s for %s (workflow %s)",
            obs_type,
            workflow.alert_type,
            workflow.id,
        )
        return obs

    def _classify_observation(self, wf: Workflow) -> Optional[str]:
        """Determine the observation type based on workflow outcome."""
        status = wf.status

        if status == WorkflowStatus.COMPLETED.value:
            return "EXECUTION_SUCCESS"
        elif status == WorkflowStatus.FAILED.value:
            return "EXECUTION_FAILURE"
        elif status == WorkflowStatus.ROLLED_BACK.value:
            return "ROLLBACK"
        elif status == WorkflowStatus.VERIFICATION_FAILED.value:
            return "FALSE_POSITIVE"
        elif status == WorkflowStatus.PRE_FLIGHT_FAILED.value:
            return "PRE_FLIGHT_FAILURE"
        elif status == WorkflowStatus.ESCALATED.value:
            return "ESCALATION"
        elif status == WorkflowStatus.TIMEOUT.value:
            return "TIMEOUT"

        return None

    def _extract_observation_data(self, wf: Workflow) -> dict:
        """Extract structured data from the workflow for the observation."""
        data = {
            "status": wf.status,
            "environment": wf.environment,
        }

        # Extract verification confidence
        if wf.verification:
            try:
                verification = json.loads(wf.verification)
                data["confidence"] = verification.get("confidence")
                data["checks_passed"] = verification.get("checks_passed", [])
                data["checks_failed"] = verification.get("checks_failed", [])
            except (json.JSONDecodeError, AttributeError):
                pass

        # Extract execution result details
        if wf.execution_result:
            try:
                result = json.loads(wf.execution_result)
                data["execution_success"] = result.get("success")
                data["duration_seconds"] = result.get("duration_seconds")
                data["rolled_back"] = result.get("rolled_back", False)
                data["error_message"] = result.get("error_message")
                data["metrics_before"] = result.get("metrics_before")
                data["metrics_after"] = result.get("metrics_after")
            except (json.JSONDecodeError, AttributeError):
                pass

        # Extract execution plan details
        if wf.execution_plan:
            try:
                plan = json.loads(wf.execution_plan)
                data["action_type"] = plan.get("action_type")
                data["risk_level"] = plan.get("risk_level")
            except (json.JSONDecodeError, AttributeError):
                pass

        # Extract research metadata
        if wf.metadata:
            try:
                meta = json.loads(wf.metadata)
                if "source" in meta:
                    data["research_source"] = meta.get("source")
                    data["option_count"] = meta.get("option_count")
            except (json.JSONDecodeError, AttributeError):
                pass

        return data
