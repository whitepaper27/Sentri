"""Workflow state machine: valid transitions and enforcement."""

from __future__ import annotations

import logging

from sentri.core.constants import WorkflowStatus
from sentri.core.exceptions import InvalidTransitionError
from sentri.db.workflow_repo import WorkflowRepository

logger = logging.getLogger("sentri.orchestrator.state_machine")

# Valid state transitions
VALID_TRANSITIONS: dict[str, list[str]] = {
    WorkflowStatus.DETECTED.value: [
        WorkflowStatus.VERIFYING.value,
        WorkflowStatus.COMPLETED.value,  # v5.0: specialist handled directly
        WorkflowStatus.ESCALATED.value,  # v5.0: specialist blocked/failed
        WorkflowStatus.VERIFICATION_FAILED.value,  # v5.0: specialist verify failed
        WorkflowStatus.FAILED.value,  # v5.0: specialist propose failed
        WorkflowStatus.AWAITING_APPROVAL.value,  # v5.0: specialist needs approval
    ],
    WorkflowStatus.VERIFYING.value: [
        WorkflowStatus.VERIFIED.value,
        WorkflowStatus.VERIFICATION_FAILED.value,
    ],
    WorkflowStatus.VERIFIED.value: [
        WorkflowStatus.PRE_FLIGHT.value,
        WorkflowStatus.AWAITING_APPROVAL.value,
        WorkflowStatus.EXECUTING.value,  # DEV auto-execute
        WorkflowStatus.ESCALATED.value,  # Low confidence escalation
    ],
    WorkflowStatus.PRE_FLIGHT.value: [
        WorkflowStatus.EXECUTING.value,
        WorkflowStatus.AWAITING_APPROVAL.value,
        WorkflowStatus.PRE_FLIGHT_FAILED.value,
    ],
    WorkflowStatus.PRE_FLIGHT_FAILED.value: [
        WorkflowStatus.ESCALATED.value,
        WorkflowStatus.DETECTED.value,  # Retry
    ],
    WorkflowStatus.VERIFICATION_FAILED.value: [
        WorkflowStatus.DETECTED.value,  # Retry
        WorkflowStatus.ESCALATED.value,
    ],
    WorkflowStatus.AWAITING_APPROVAL.value: [
        WorkflowStatus.APPROVED.value,
        WorkflowStatus.DENIED.value,
        WorkflowStatus.TIMEOUT.value,
        WorkflowStatus.COMPLETED.value,  # Manual resolution (sentri resolve)
        WorkflowStatus.ESCALATED.value,  # Manual escalation (sentri resolve --escalate)
    ],
    WorkflowStatus.APPROVED.value: [
        WorkflowStatus.EXECUTING.value,
        WorkflowStatus.ESCALATED.value,  # No plan or agent unavailable
        WorkflowStatus.FAILED.value,  # Execution setup failed
    ],
    WorkflowStatus.DENIED.value: [
        WorkflowStatus.COMPLETED.value,
        WorkflowStatus.ESCALATED.value,  # Deny + escalate (sentri approve --deny --escalate)
    ],
    WorkflowStatus.EXECUTING.value: [
        WorkflowStatus.COMPLETED.value,
        WorkflowStatus.FAILED.value,
        WorkflowStatus.ROLLED_BACK.value,
    ],
    WorkflowStatus.FAILED.value: [
        WorkflowStatus.ESCALATED.value,
        WorkflowStatus.ROLLED_BACK.value,
    ],
    WorkflowStatus.ROLLED_BACK.value: [
        WorkflowStatus.ESCALATED.value,
        WorkflowStatus.COMPLETED.value,
    ],
    WorkflowStatus.TIMEOUT.value: [
        WorkflowStatus.ESCALATED.value,
    ],
    WorkflowStatus.ESCALATED.value: [],  # Terminal
    WorkflowStatus.COMPLETED.value: [],  # Terminal
}

TERMINAL_STATES = {WorkflowStatus.COMPLETED.value, WorkflowStatus.ESCALATED.value}


def validate_transition(current: str, target: str) -> bool:
    """Validate a state transition is allowed. Raises on invalid."""
    allowed = VALID_TRANSITIONS.get(current, [])
    if target not in allowed:
        raise InvalidTransitionError(
            f"Cannot transition from {current} to {target}. Allowed: {allowed}"
        )
    return True


def is_terminal(status: str) -> bool:
    """Check if a status is a terminal state."""
    return status in TERMINAL_STATES


class StateMachine:
    """Execute state transitions on workflows with validation."""

    def __init__(self, workflow_repo: WorkflowRepository):
        self._repo = workflow_repo

    def transition(self, workflow_id: str, target_status: str, **kwargs) -> None:
        """Transition a workflow to a new status.

        Validates the transition is allowed, then updates the database.
        """
        workflow = self._repo.get(workflow_id)
        if not workflow:
            raise InvalidTransitionError(f"Workflow {workflow_id} not found")

        validate_transition(workflow.status, target_status)

        self._repo.update_status(workflow_id, target_status, **kwargs)
        logger.info("Workflow %s: %s -> %s", workflow_id, workflow.status, target_status)
