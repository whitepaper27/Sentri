"""Load brain policies (autonomy levels, state machine rules, etc.)."""

from __future__ import annotations

import logging

from sentri.core.constants import Environment, RiskLevel

from .loader import PolicyLoader

logger = logging.getLogger("sentri.policy.brain")


class BrainPolicies:
    """Read brain policy files and provide structured access."""

    def __init__(self, policy_loader: PolicyLoader):
        self._loader = policy_loader

    def get_global_policy(self) -> dict:
        """Load the global policy."""
        return self._loader.load_brain("global_policy")

    def get_autonomy_levels(self) -> dict:
        """Load autonomy level definitions."""
        return self._loader.load_brain("autonomy_levels")

    def get_state_machine(self) -> dict:
        """Load state machine transition rules."""
        return self._loader.load_brain("state_machine")

    def get_violation_protocol(self) -> dict:
        """Load violation response protocol."""
        return self._loader.load_brain("violation_protocol")

    def get_locking_rules(self) -> dict:
        """Load resource locking rules."""
        return self._loader.load_brain("locking_rules")

    def get_memory_rules(self) -> dict:
        """Load data retention and memory rules."""
        return self._loader.load_brain("memory_rules")

    def requires_approval(self, environment: Environment, risk_level: str) -> bool:
        """Determine if an action requires human approval.

        Based on autonomy_levels.md Risk Matrix:
        - DEV (AUTONOMOUS): never requires approval
        - UAT (SUPERVISED): requires approval for MEDIUM+ risk
        - PROD (ADVISORY): always requires approval
        """
        if environment == Environment.DEV:
            return False
        if environment == Environment.PROD:
            return True
        # UAT: supervised - approve MEDIUM and above
        if environment == Environment.UAT:
            return risk_level in (
                RiskLevel.MEDIUM.value,
                RiskLevel.HIGH.value,
                RiskLevel.CRITICAL.value,
            )
        return True  # Default: require approval
