"""Enumerations and constants for the Sentri system."""

from enum import Enum


class WorkflowStatus(str, Enum):
    """Workflow state machine states."""

    DETECTED = "DETECTED"
    VERIFYING = "VERIFYING"
    VERIFIED = "VERIFIED"
    VERIFICATION_FAILED = "VERIFICATION_FAILED"
    PRE_FLIGHT = "PRE_FLIGHT"
    PRE_FLIGHT_FAILED = "PRE_FLIGHT_FAILED"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    APPROVED = "APPROVED"
    DENIED = "DENIED"
    EXECUTING = "EXECUTING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    ROLLED_BACK = "ROLLED_BACK"
    TIMEOUT = "TIMEOUT"
    ESCALATED = "ESCALATED"


class Environment(str, Enum):
    """Database environment tiers."""

    DEV = "DEV"
    UAT = "UAT"
    PROD = "PROD"


class AutonomyLevel(str, Enum):
    """How much freedom the system has per environment."""

    AUTONOMOUS = "AUTONOMOUS"  # DEV: auto-execute everything
    SUPERVISED = "SUPERVISED"  # UAT: approval for high-risk only
    ADVISORY = "ADVISORY"  # PROD: always require approval


class AlertType(str, Enum):
    """The 5 alert types handled in POC."""

    TABLESPACE_FULL = "tablespace_full"
    ARCHIVE_DEST_FULL = "archive_dest_full"
    TEMP_FULL = "temp_full"
    LISTENER_DOWN = "listener_down"
    ARCHIVE_GAP = "archive_gap"


class ActionType(str, Enum):
    """Types of remediation actions."""

    ADD_DATAFILE = "ADD_DATAFILE"
    DELETE_ARCHIVES = "DELETE_ARCHIVES"
    ADD_TEMPFILE = "ADD_TEMPFILE"
    START_LISTENER = "START_LISTENER"
    RESOLVE_GAP = "RESOLVE_GAP"


class RiskLevel(str, Enum):
    """Risk classification for actions."""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class ExecutionOutcome(str, Enum):
    """Result of an execution attempt."""

    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    ROLLED_BACK = "ROLLED_BACK"


class Severity(str, Enum):
    """Alert severity classification (v2.0)."""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class ConfidenceThreshold(float, Enum):
    """Confidence thresholds for execution routing (v2.0)."""

    AUTO_EXECUTE = 0.95
    PRE_FLIGHT = 0.80
    APPROVAL_REQUIRED = 0.60
    ESCALATE = 0.0  # Below APPROVAL_REQUIRED


class LearningStatus(str, Enum):
    """Status of a learning observation (v2.0)."""

    CAPTURED = "CAPTURED"
    PROCESSED = "PROCESSED"
    PROPOSED = "PROPOSED"
    APPROVED = "APPROVED"
    APPLIED = "APPLIED"
    REJECTED = "REJECTED"


# Environment -> Autonomy mapping
ENVIRONMENT_AUTONOMY: dict[Environment, AutonomyLevel] = {
    Environment.DEV: AutonomyLevel.AUTONOMOUS,
    Environment.UAT: AutonomyLevel.SUPERVISED,
    Environment.PROD: AutonomyLevel.ADVISORY,
}

# Default timeouts (seconds)
VERIFICATION_TIMEOUT = 30
EXECUTION_TIMEOUT = 300
APPROVAL_TIMEOUT = 3600
LOCK_TIMEOUT = 30
LOCK_EXPIRY = 600
SCOUT_POLL_INTERVAL = 60
ORCHESTRATOR_POLL_INTERVAL = 10
