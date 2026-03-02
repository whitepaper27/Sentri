"""Test workflow state machine transitions."""

import pytest

from sentri.core.exceptions import InvalidTransitionError
from sentri.core.models import Workflow
from sentri.orchestrator.state_machine import (
    StateMachine,
    is_terminal,
    validate_transition,
)


def test_valid_transitions():
    """Test that valid transitions pass validation."""
    assert validate_transition("DETECTED", "VERIFYING")
    assert validate_transition("VERIFYING", "VERIFIED")
    assert validate_transition("VERIFIED", "EXECUTING")
    assert validate_transition("VERIFIED", "AWAITING_APPROVAL")
    assert validate_transition("APPROVED", "EXECUTING")
    assert validate_transition("EXECUTING", "COMPLETED")
    assert validate_transition("EXECUTING", "FAILED")
    assert validate_transition("EXECUTING", "ROLLED_BACK")


def test_invalid_transitions():
    """Test that invalid transitions raise errors."""
    with pytest.raises(InvalidTransitionError):
        validate_transition("COMPLETED", "DETECTED")  # Terminal → non-terminal

    with pytest.raises(InvalidTransitionError):
        validate_transition("EXECUTING", "DETECTED")  # Cannot go backwards

    with pytest.raises(InvalidTransitionError):
        validate_transition("ESCALATED", "DETECTED")  # Terminal → non-terminal


def test_terminal_states():
    assert is_terminal("COMPLETED")
    assert is_terminal("ESCALATED")
    assert not is_terminal("DETECTED")
    assert not is_terminal("EXECUTING")


def test_state_machine_transition(workflow_repo):
    """Test StateMachine updates workflow in database."""
    wf = Workflow(
        alert_type="test",
        database_id="DB-01",
        environment="DEV",
        status="DETECTED",
    )
    workflow_repo.create(wf)

    sm = StateMachine(workflow_repo)
    sm.transition(wf.id, "VERIFYING")

    fetched = workflow_repo.get(wf.id)
    assert fetched.status == "VERIFYING"


def test_state_machine_rejects_invalid(workflow_repo):
    """Test StateMachine rejects invalid transitions."""
    wf = Workflow(
        alert_type="test",
        database_id="DB-01",
        environment="DEV",
        status="COMPLETED",
    )
    workflow_repo.create(wf)

    sm = StateMachine(workflow_repo)
    with pytest.raises(InvalidTransitionError):
        sm.transition(wf.id, "DETECTED")


def test_v5_specialist_transitions():
    """Test v5.0 specialist shortcut transitions from DETECTED."""
    # v5.0 specialists can go directly from DETECTED to terminal states
    assert validate_transition("DETECTED", "COMPLETED")
    assert validate_transition("DETECTED", "ESCALATED")
    assert validate_transition("DETECTED", "VERIFICATION_FAILED")
    assert validate_transition("DETECTED", "FAILED")
    assert validate_transition("DETECTED", "AWAITING_APPROVAL")
