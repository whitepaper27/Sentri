"""Approval routing logic for workflow execution."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

from sentri.core.constants import (
    APPROVAL_TIMEOUT,
    Environment,
)
from sentri.core.models import Workflow
from sentri.policy.brain_policies import BrainPolicies

logger = logging.getLogger("sentri.orchestrator.approval")


class ApprovalRouter:
    """Determine if approval is needed and route approval requests."""

    def __init__(self, brain_policies: BrainPolicies):
        self._brain = brain_policies

    def requires_approval(self, workflow: Workflow, risk_level: str) -> bool:
        """Determine if a workflow requires human approval."""
        try:
            env = Environment(workflow.environment)
        except ValueError:
            return True  # Unknown environment: require approval

        return self._brain.requires_approval(env, risk_level)

    def build_approval_package(self, workflow: Workflow) -> dict:
        """Build the approval request context for human review."""
        suggestion_data = {}
        if workflow.suggestion:
            try:
                suggestion_data = json.loads(workflow.suggestion)
            except json.JSONDecodeError:
                pass

        verification_data = {}
        if workflow.verification:
            try:
                verification_data = json.loads(workflow.verification)
            except json.JSONDecodeError:
                pass

        plan_data = {}
        if workflow.execution_plan:
            try:
                plan_data = json.loads(workflow.execution_plan)
            except json.JSONDecodeError:
                pass

        return {
            "workflow_id": workflow.id,
            "database": workflow.database_id,
            "environment": workflow.environment,
            "alert_type": workflow.alert_type,
            "detected_at": workflow.created_at,
            "suggestion": suggestion_data,
            "verification": verification_data,
            "proposed_action": plan_data.get("forward_sql", "N/A"),
            "rollback_plan": plan_data.get("rollback_sql", "N/A"),
            "risk_level": plan_data.get("risk_level", "UNKNOWN"),
            "estimated_duration": plan_data.get("estimated_duration_seconds", "N/A"),
        }

    def calculate_timeout(self, workflow: Workflow) -> str:
        """Calculate the approval timeout timestamp."""
        timeout_delta = timedelta(seconds=APPROVAL_TIMEOUT)
        timeout_at = datetime.now(timezone.utc) + timeout_delta
        return timeout_at.isoformat()

    def format_approval_message(self, package: dict) -> str:
        """Format approval package as a readable message."""
        lines = [
            "**Sentri Approval Request**",
            "",
            f"**Database**: {package['database']}",
            f"**Environment**: {package['environment']}",
            f"**Alert**: {package['alert_type']}",
            f"**Detected**: {package['detected_at']}",
            "",
        ]

        verification = package.get("verification", {})
        if verification:
            confidence = verification.get("confidence", "N/A")
            checks_passed = verification.get("checks_passed", [])
            lines.append(f"**Verification**: confidence={confidence}")
            for check in checks_passed:
                lines.append(f"  - {check}")
            lines.append("")

        lines.extend(
            [
                "**Proposed Action**:",
                "```sql",
                f"{package['proposed_action']}",
                "```",
                "",
                "**Rollback Plan**:",
                "```sql",
                f"{package['rollback_plan']}",
                "```",
                "",
                f"**Risk**: {package['risk_level']}",
                f"**Est. Duration**: {package['estimated_duration']}s",
                "",
                f"Workflow ID: {package['workflow_id']}",
            ]
        )

        return "\n".join(lines)
