"""Exception hierarchy for the Sentri system."""


class SentriError(Exception):
    """Base exception for all Sentri errors."""


class PolicyLoadError(SentriError):
    """Failed to load or parse a policy .md file."""


class DatabaseError(SentriError):
    """Internal SQLite database error."""


class OracleConnectionError(SentriError):
    """Failed to connect to target Oracle database."""


class OracleQueryError(SentriError):
    """Failed to execute a query on target Oracle database."""


class InvalidTransitionError(SentriError):
    """Attempted an invalid workflow state transition."""


class LockAcquisitionError(SentriError):
    """Failed to acquire a resource lock."""


class VerificationError(SentriError):
    """Alert verification failed or timed out."""


class VerificationTimeoutError(VerificationError):
    """Verification query exceeded timeout."""


class ExecutionError(SentriError):
    """Remediation execution failed."""


class RollbackError(ExecutionError):
    """Rollback after a failed execution also failed."""


class ApprovalTimeoutError(SentriError):
    """Approval request timed out without response."""


class ConfigurationError(SentriError):
    """Invalid or missing configuration."""


class LLMError(SentriError):
    """LLM provider call failed."""


class ProfileError(SentriError):
    """Database profiling failed."""


class PreFlightError(SentriError):
    """Pre-flight check failed or blocked execution."""


class LearningError(SentriError):
    """Learning engine error (observation, proposal, or apply)."""


class SafetyMeshBlockError(SentriError):
    """Safety Mesh blocked an action due to policy, blast radius, or circuit breaker."""


class ConflictError(SentriError):
    """Resource conflict — another operation is already executing on the same database."""
