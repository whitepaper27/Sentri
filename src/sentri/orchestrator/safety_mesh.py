"""Safety Mesh — structural enforcement point for ALL agent actions.

v5.0: Every execute() call from any specialist agent passes through
the Safety Mesh before reaching the Executor.

5 checks (in order):
1. Policy Gate     — delegate to existing RulesEngine
2. Conflict Detect — another fix already executing on same DB?
3. Blast Radius    — DDL > DML > SELECT risk classification
4. Circuit Breaker — too many recent failures on this DB?
5. Rollback Check  — can we undo this? If not + risk > LOW, block
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Optional

from sentri.core.constants import WorkflowStatus
from sentri.core.models import ExecutionPlan, Workflow
from sentri.policy.rules_engine import RulesEngine, RuleVerdict

if TYPE_CHECKING:
    from sentri.db.audit_repo import AuditRepository
    from sentri.db.connection import Database
    from sentri.db.workflow_repo import WorkflowRepository
    from sentri.policy.alert_patterns import AlertPatterns
    from sentri.policy.environment_config import EnvironmentConfig

logger = logging.getLogger("sentri.orchestrator.safety_mesh")


class MeshDecision(str, Enum):
    """Possible outcomes of the Safety Mesh evaluation."""

    ALLOW = "ALLOW"
    REQUIRE_APPROVAL = "REQUIRE_APPROVAL"
    BLOCK = "BLOCK"
    QUEUE = "QUEUE"  # Conflict detected — queue for later


@dataclass
class MeshVerdict:
    """Result of all 5 Safety Mesh checks."""

    decision: MeshDecision
    reasons: list[str] = field(default_factory=list)
    blocked_by: Optional[str] = None
    rule_verdict: Optional[RuleVerdict] = None

    @property
    def allowed(self) -> bool:
        return self.decision == MeshDecision.ALLOW

    @property
    def needs_approval(self) -> bool:
        return self.decision == MeshDecision.REQUIRE_APPROVAL

    @property
    def blocked(self) -> bool:
        return self.decision == MeshDecision.BLOCK

    @property
    def queued(self) -> bool:
        return self.decision == MeshDecision.QUEUE


# Decision severity ordering (higher = more restrictive)
_DECISION_ORDER = {
    MeshDecision.ALLOW: 0,
    MeshDecision.QUEUE: 1,
    MeshDecision.REQUIRE_APPROVAL: 2,
    MeshDecision.BLOCK: 3,
}


class SafetyMesh:
    """Structural safety enforcement for all agent actions.

    Composes WITH the existing RulesEngine (policy gate delegates to it).
    The Executor's internal safety checks remain as defense-in-depth.
    """

    # Circuit breaker defaults (overridable via brain/rules.md Circuit Breaker section)
    CIRCUIT_BREAKER_THRESHOLD = 3
    CIRCUIT_BREAKER_HOURS = 24

    # Blast radius classification patterns
    _DDL_PATTERN = re.compile(r"\b(ALTER|CREATE|DROP|TRUNCATE|GRANT|REVOKE)\b", re.IGNORECASE)
    _DML_PATTERN = re.compile(r"\b(INSERT|UPDATE|DELETE|MERGE)\b", re.IGNORECASE)

    def __init__(
        self,
        rules_engine: RulesEngine,
        db: "Database",
        workflow_repo: "WorkflowRepository",
        audit_repo: "AuditRepository",
        alert_patterns: "AlertPatterns",
        environment_config: Optional["EnvironmentConfig"] = None,
    ):
        self._rules = rules_engine
        self._db = db
        self._workflow_repo = workflow_repo
        self._audit_repo = audit_repo
        self._alert_patterns = alert_patterns
        self._env_config = environment_config

    def check(
        self,
        workflow: Workflow,
        plan: ExecutionPlan,
        confidence: float = 1.0,
    ) -> MeshVerdict:
        """Run all 5 safety checks. Returns the most restrictive verdict.

        Check order: policy gate → conflict → blast radius → circuit breaker → rollback.
        Short-circuits on BLOCK (no point checking further).
        """
        reasons: list[str] = []
        decision = MeshDecision.ALLOW
        blocked_by: Optional[str] = None
        rule_verdict: Optional[RuleVerdict] = None

        checks = [
            ("policy_gate", self._check_policy_gate),
            ("conflict_detect", self._check_conflict),
            ("blast_radius", self._check_blast_radius),
            ("circuit_breaker", self._check_circuit_breaker),
            ("rollback_check", self._check_rollback),
        ]

        for check_name, check_fn in checks:
            if check_name == "policy_gate":
                result = check_fn(workflow, plan, confidence)
            elif check_name in ("conflict_detect", "circuit_breaker"):
                result = check_fn(workflow)
            elif check_name == "blast_radius":
                result = check_fn(plan, workflow.environment)
            else:  # rollback_check
                result = check_fn(plan)

            # Capture rule_verdict from policy gate
            if check_name == "policy_gate" and result.rule_verdict:
                rule_verdict = result.rule_verdict

            # Escalate to most restrictive decision
            if _DECISION_ORDER[result.decision] > _DECISION_ORDER[decision]:
                decision = result.decision
                if result.blocked_by:
                    blocked_by = result.blocked_by
            reasons.extend(result.reasons)

            # Short-circuit on BLOCK
            if decision == MeshDecision.BLOCK:
                logger.warning(
                    "Safety Mesh BLOCKED %s at check '%s': %s",
                    workflow.id,
                    check_name,
                    "; ".join(result.reasons),
                )
                break

        if decision == MeshDecision.ALLOW:
            logger.info("Safety Mesh ALLOW for %s", workflow.id)
        elif decision == MeshDecision.REQUIRE_APPROVAL:
            logger.info(
                "Safety Mesh REQUIRE_APPROVAL for %s: %s",
                workflow.id,
                "; ".join(reasons),
            )

        return MeshVerdict(
            decision=decision,
            reasons=reasons,
            blocked_by=blocked_by,
            rule_verdict=rule_verdict,
        )

    # ------------------------------------------------------------------
    # Check 1: Policy Gate (delegates to existing RulesEngine)
    # ------------------------------------------------------------------

    def _check_policy_gate(
        self,
        workflow: Workflow,
        plan: ExecutionPlan,
        confidence: float,
    ) -> MeshVerdict:
        """Delegate to the existing RulesEngine for policy evaluation.

        Also checks per-database autonomy overrides from environments/*.md.
        Override hierarchy: per-database override > environment default.
        ADVISORY is most restrictive (always requires approval).
        """
        action_type = plan.action_type

        # Get repeat alert info
        recent_count, hours_since = self._workflow_repo.count_recent_same(
            workflow.database_id,
            workflow.alert_type,
            hours=24,
        )

        rule_verdict = self._rules.evaluate(
            action_type=action_type,
            environment=workflow.environment,
            database_id=workflow.database_id,
            confidence=confidence,
            recent_same_alerts=recent_count,
            hours_since_last_same=hours_since,
        )

        # Check per-database autonomy override (higher priority than env default)
        override_verdict = self._check_autonomy_override(workflow)

        # Map RulesEngine verdict to MeshDecision
        if rule_verdict.blocked:
            return MeshVerdict(
                decision=MeshDecision.BLOCK,
                reasons=rule_verdict.reasons,
                blocked_by="policy_gate",
                rule_verdict=rule_verdict,
            )

        # Apply autonomy override: if override says REQUIRE_APPROVAL and
        # rules engine says ALLOW, escalate to REQUIRE_APPROVAL
        if override_verdict and override_verdict.needs_approval and rule_verdict.allowed:
            combined_reasons = rule_verdict.reasons + override_verdict.reasons
            return MeshVerdict(
                decision=MeshDecision.REQUIRE_APPROVAL,
                reasons=combined_reasons,
                rule_verdict=rule_verdict,
            )

        if rule_verdict.needs_approval:
            return MeshVerdict(
                decision=MeshDecision.REQUIRE_APPROVAL,
                reasons=rule_verdict.reasons,
                rule_verdict=rule_verdict,
            )
        return MeshVerdict(
            decision=MeshDecision.ALLOW,
            reasons=rule_verdict.reasons,
            rule_verdict=rule_verdict,
        )

    def _check_autonomy_override(self, workflow: Workflow) -> Optional[MeshVerdict]:
        """Check if a per-database autonomy override forces approval.

        Returns a MeshVerdict with REQUIRE_APPROVAL if an override is active,
        or None if no override applies.
        """
        if not self._env_config:
            return None

        try:
            from sentri.core.constants import AutonomyLevel

            override = self._env_config.get_autonomy_override(workflow.database_id)
            if not override:
                return None

            # ADVISORY = always require approval
            if override.level == AutonomyLevel.ADVISORY:
                reason = (
                    f"Per-database autonomy override: {workflow.database_id} is "
                    f"ADVISORY (reason: {override.reason or 'not specified'})"
                )
                logger.info(reason)
                return MeshVerdict(
                    decision=MeshDecision.REQUIRE_APPROVAL,
                    reasons=[reason],
                )

            # SUPERVISED = require approval for non-LOW risk
            if override.level == AutonomyLevel.SUPERVISED:
                risk = (
                    workflow.risk_level.upper()
                    if hasattr(workflow, "risk_level") and workflow.risk_level
                    else "MEDIUM"
                )
                if risk not in ("LOW",):
                    reason = (
                        f"Per-database autonomy override: {workflow.database_id} is "
                        f"SUPERVISED, risk={risk} requires approval "
                        f"(reason: {override.reason or 'not specified'})"
                    )
                    logger.info(reason)
                    return MeshVerdict(
                        decision=MeshDecision.REQUIRE_APPROVAL,
                        reasons=[reason],
                    )

            return None
        except Exception as e:
            logger.warning("Failed to check autonomy override for %s: %s", workflow.database_id, e)
            return None

    # ------------------------------------------------------------------
    # Check 2: Conflict Detection
    # ------------------------------------------------------------------

    def _check_conflict(self, workflow: Workflow) -> MeshVerdict:
        """Is another fix currently executing on the same database?"""
        rows = self._db.execute_read(
            """SELECT id FROM workflows
               WHERE database_id = ?
               AND status = ?
               AND id != ?
               LIMIT 1""",
            (
                workflow.database_id,
                WorkflowStatus.EXECUTING.value,
                workflow.id,
            ),
        )

        if rows:
            conflicting_id = rows[0]["id"]
            return MeshVerdict(
                decision=MeshDecision.QUEUE,
                reasons=[
                    f"Conflict: workflow {conflicting_id} is already executing "
                    f"on {workflow.database_id}"
                ],
                blocked_by="conflict_detect",
            )

        return MeshVerdict(decision=MeshDecision.ALLOW)

    # ------------------------------------------------------------------
    # Check 3: Blast Radius Classification
    # ------------------------------------------------------------------

    def _check_blast_radius(
        self,
        plan: ExecutionPlan,
        environment: str,
    ) -> MeshVerdict:
        """Classify the SQL risk: DDL > DML > SELECT.

        DDL in PROD always requires approval.
        DDL in UAT requires approval if risk > LOW.
        DML follows existing rules engine.
        SELECT (read-only) always passes.
        """
        sql = plan.forward_sql
        risk = plan.risk_level.upper() if plan.risk_level else "MEDIUM"
        env = environment.upper()

        is_ddl = bool(self._DDL_PATTERN.search(sql))
        _is_dml = bool(self._DML_PATTERN.search(sql))

        if is_ddl:
            if env == "PROD":
                return MeshVerdict(
                    decision=MeshDecision.REQUIRE_APPROVAL,
                    reasons=[f"DDL in PROD requires approval: {sql[:80]}..."],
                )
            if env == "UAT" and risk not in ("LOW",):
                return MeshVerdict(
                    decision=MeshDecision.REQUIRE_APPROVAL,
                    reasons=[f"DDL in UAT with risk={risk} requires approval: {sql[:80]}..."],
                )

        # DML and SELECT pass through (existing rules engine already handles)
        return MeshVerdict(decision=MeshDecision.ALLOW)

    # ------------------------------------------------------------------
    # Check 4: Circuit Breaker
    # ------------------------------------------------------------------

    def _check_circuit_breaker(self, workflow: Workflow) -> MeshVerdict:
        """Too many recent failures on this database?

        If >= threshold failures in the last N hours, block execution.
        Threshold and window are configurable via brain/rules.md
        Circuit Breaker section (defaults: 3 failures / 24h).
        """
        # Read config from RulesEngine (DBA-configurable via rules.md)
        threshold = getattr(self._rules, "circuit_breaker_threshold", self.CIRCUIT_BREAKER_THRESHOLD)
        hours = getattr(self._rules, "circuit_breaker_hours", self.CIRCUIT_BREAKER_HOURS)

        rows = self._db.execute_read(
            """SELECT COUNT(*) as fail_count
               FROM audit_log
               WHERE database_id = ?
               AND result = 'FAILED'
               AND timestamp > datetime('now', ?)""",
            (
                workflow.database_id,
                f"-{hours} hours",
            ),
        )

        fail_count = rows[0]["fail_count"] if rows else 0

        if fail_count >= threshold:
            return MeshVerdict(
                decision=MeshDecision.BLOCK,
                reasons=[
                    f"Circuit breaker: {fail_count} failures on "
                    f"{workflow.database_id} in last {hours}h "
                    f"(threshold={threshold}, configurable in brain/rules.md)"
                ],
                blocked_by="circuit_breaker",
            )

        return MeshVerdict(decision=MeshDecision.ALLOW)

    # ------------------------------------------------------------------
    # Check 5: Rollback Availability
    # ------------------------------------------------------------------

    def _check_rollback(self, plan: ExecutionPlan) -> MeshVerdict:
        """Can we undo this? If not and risk > LOW, require approval or block.

        - HIGH/CRITICAL risk without rollback → BLOCK
        - MEDIUM risk without rollback → REQUIRE_APPROVAL
        - LOW risk without rollback → ALLOW (acceptable risk)
        """
        has_rollback = bool(
            plan.rollback_sql
            and plan.rollback_sql.strip()
            and plan.rollback_sql.strip().upper() not in ("N/A", "N/A: IRREVERSIBLE")
        )

        if has_rollback:
            return MeshVerdict(decision=MeshDecision.ALLOW)

        risk = plan.risk_level.upper() if plan.risk_level else "MEDIUM"

        if risk in ("HIGH", "CRITICAL"):
            return MeshVerdict(
                decision=MeshDecision.BLOCK,
                reasons=[f"No rollback SQL for {risk} risk action — blocked for safety"],
                blocked_by="rollback_check",
            )

        if risk == "MEDIUM":
            return MeshVerdict(
                decision=MeshDecision.REQUIRE_APPROVAL,
                reasons=["No rollback SQL for MEDIUM risk action — requires approval"],
            )

        # LOW risk without rollback is acceptable
        return MeshVerdict(decision=MeshDecision.ALLOW)
