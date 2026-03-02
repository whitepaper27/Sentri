"""Tests for the Safety Mesh — structural enforcement layer (v5.0a).

Updated v5.1a: per-database autonomy override tests.
"""

from unittest.mock import MagicMock

import pytest

from sentri.core.constants import AutonomyLevel
from sentri.core.models import AuditRecord, ExecutionPlan, Workflow
from sentri.orchestrator.safety_mesh import (
    MeshDecision,
    MeshVerdict,
    SafetyMesh,
)
from sentri.policy.alert_patterns import AlertPatterns
from sentri.policy.environment_config import AutonomyOverride, EnvironmentConfig
from sentri.policy.rules_engine import RulesEngine, RuleVerdict

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def rules_engine(policy_loader):
    return RulesEngine(policy_loader)


@pytest.fixture
def alert_patterns(policy_loader):
    return AlertPatterns(policy_loader)


@pytest.fixture
def safety_mesh(rules_engine, tmp_db, workflow_repo, audit_repo, alert_patterns):
    return SafetyMesh(
        rules_engine=rules_engine,
        db=tmp_db,
        workflow_repo=workflow_repo,
        audit_repo=audit_repo,
        alert_patterns=alert_patterns,
    )


def _make_workflow(
    database_id="DEV-DB-01",
    environment="DEV",
    alert_type="tablespace_full",
    status="VERIFIED",
) -> Workflow:
    return Workflow(
        alert_type=alert_type,
        database_id=database_id,
        environment=environment,
        status=status,
    )


def _make_plan(
    forward_sql="ALTER TABLESPACE USERS ADD DATAFILE SIZE 100M",
    rollback_sql="ALTER TABLESPACE USERS DROP DATAFILE '/data/users_02.dbf'",
    action_type="ADD_DATAFILE",
    risk_level="LOW",
) -> ExecutionPlan:
    return ExecutionPlan(
        action_type=action_type,
        forward_sql=forward_sql,
        rollback_sql=rollback_sql,
        validation_sql="SELECT 1 FROM dual",
        expected_outcome={"status": "resolved"},
        risk_level=risk_level,
        estimated_duration_seconds=30,
    )


# ---------------------------------------------------------------------------
# Check 1: Policy Gate (delegates to RulesEngine)
# ---------------------------------------------------------------------------


class TestPolicyGate:
    """Test that the policy gate delegates to RulesEngine correctly."""

    def test_allow_dev_auto(self, safety_mesh, workflow_repo):
        """DEV + auto action type + high confidence → ALLOW."""
        wf = _make_workflow(environment="DEV")
        workflow_repo.create(wf)
        plan = _make_plan()

        verdict = safety_mesh._check_policy_gate(wf, plan, confidence=0.95)

        assert verdict.decision == MeshDecision.ALLOW
        assert verdict.rule_verdict is not None

    def test_block_low_confidence(self, safety_mesh, workflow_repo):
        """Confidence < 0.60 → BLOCK (from RulesEngine)."""
        wf = _make_workflow()
        workflow_repo.create(wf)
        plan = _make_plan()

        verdict = safety_mesh._check_policy_gate(wf, plan, confidence=0.40)

        assert verdict.decision == MeshDecision.BLOCK
        assert verdict.blocked_by == "policy_gate"

    def test_approval_medium_confidence(self, safety_mesh, workflow_repo):
        """Confidence 0.60-0.80 → REQUIRE_APPROVAL."""
        wf = _make_workflow()
        workflow_repo.create(wf)
        plan = _make_plan()

        verdict = safety_mesh._check_policy_gate(wf, plan, confidence=0.70)

        assert verdict.decision == MeshDecision.REQUIRE_APPROVAL

    def test_prod_always_approval(self, safety_mesh, workflow_repo):
        """PROD environment → REQUIRE_APPROVAL regardless of confidence."""
        wf = _make_workflow(environment="PROD", database_id="PROD-DB-07")
        workflow_repo.create(wf)
        plan = _make_plan()

        verdict = safety_mesh._check_policy_gate(wf, plan, confidence=0.99)

        assert verdict.decision == MeshDecision.REQUIRE_APPROVAL

    def test_rule_verdict_attached(self, safety_mesh, workflow_repo):
        """The RuleVerdict from RulesEngine is attached to the MeshVerdict."""
        wf = _make_workflow()
        workflow_repo.create(wf)
        plan = _make_plan()

        verdict = safety_mesh._check_policy_gate(wf, plan, confidence=0.95)

        assert verdict.rule_verdict is not None
        assert isinstance(verdict.rule_verdict, RuleVerdict)


# ---------------------------------------------------------------------------
# Check 2: Conflict Detection
# ---------------------------------------------------------------------------


class TestConflictDetection:
    """Test conflict detection — another fix executing on same DB."""

    def test_no_conflict(self, safety_mesh):
        """No other EXECUTING workflow → ALLOW."""
        wf = _make_workflow()

        verdict = safety_mesh._check_conflict(wf)

        assert verdict.decision == MeshDecision.ALLOW

    def test_conflict_same_db(self, safety_mesh, workflow_repo):
        """Another workflow EXECUTING on same DB → QUEUE."""
        # Create a workflow that's currently executing
        existing = _make_workflow(status="EXECUTING")
        workflow_repo.create(existing)

        # New workflow on same DB
        new_wf = _make_workflow()
        new_wf.id = "new-workflow-id"

        verdict = safety_mesh._check_conflict(new_wf)

        assert verdict.decision == MeshDecision.QUEUE
        assert "conflict" in verdict.reasons[0].lower()

    def test_no_conflict_different_db(self, safety_mesh, workflow_repo):
        """EXECUTING on different DB → ALLOW."""
        existing = Workflow(
            alert_type="tablespace_full",
            database_id="OTHER-DB",
            environment="DEV",
            status="EXECUTING",
        )
        workflow_repo.create(existing)

        new_wf = _make_workflow(database_id="DEV-DB-01")

        verdict = safety_mesh._check_conflict(new_wf)

        assert verdict.decision == MeshDecision.ALLOW

    def test_no_conflict_completed(self, safety_mesh, workflow_repo):
        """Completed workflow on same DB → not a conflict."""
        existing = _make_workflow(status="COMPLETED")
        workflow_repo.create(existing)

        new_wf = _make_workflow()
        new_wf.id = "new-workflow-id"

        verdict = safety_mesh._check_conflict(new_wf)

        assert verdict.decision == MeshDecision.ALLOW


# ---------------------------------------------------------------------------
# Check 3: Blast Radius Classification
# ---------------------------------------------------------------------------


class TestBlastRadius:
    """Test blast radius — DDL/DML/SELECT classification."""

    def test_ddl_prod_requires_approval(self, safety_mesh):
        """DDL in PROD always requires approval."""
        plan = _make_plan(forward_sql="ALTER TABLESPACE USERS ADD DATAFILE SIZE 100M")

        verdict = safety_mesh._check_blast_radius(plan, "PROD")

        assert verdict.decision == MeshDecision.REQUIRE_APPROVAL
        assert "DDL in PROD" in verdict.reasons[0]

    def test_ddl_uat_medium_risk_requires_approval(self, safety_mesh):
        """DDL in UAT with risk > LOW requires approval."""
        plan = _make_plan(
            forward_sql="ALTER TABLESPACE USERS ADD DATAFILE SIZE 100M",
            risk_level="MEDIUM",
        )

        verdict = safety_mesh._check_blast_radius(plan, "UAT")

        assert verdict.decision == MeshDecision.REQUIRE_APPROVAL

    def test_ddl_uat_low_risk_allows(self, safety_mesh):
        """DDL in UAT with risk=LOW is allowed."""
        plan = _make_plan(
            forward_sql="ALTER TABLESPACE USERS ADD DATAFILE SIZE 100M",
            risk_level="LOW",
        )

        verdict = safety_mesh._check_blast_radius(plan, "UAT")

        assert verdict.decision == MeshDecision.ALLOW

    def test_ddl_dev_allows(self, safety_mesh):
        """DDL in DEV is allowed (auto-execution environment)."""
        plan = _make_plan(forward_sql="ALTER TABLESPACE USERS ADD DATAFILE SIZE 100M")

        verdict = safety_mesh._check_blast_radius(plan, "DEV")

        assert verdict.decision == MeshDecision.ALLOW

    def test_select_always_allows(self, safety_mesh):
        """Pure SELECT is always allowed regardless of environment."""
        plan = _make_plan(forward_sql="SELECT * FROM dba_tablespaces")

        verdict = safety_mesh._check_blast_radius(plan, "PROD")

        assert verdict.decision == MeshDecision.ALLOW


# ---------------------------------------------------------------------------
# Check 4: Circuit Breaker
# ---------------------------------------------------------------------------


class TestCircuitBreaker:
    """Test circuit breaker — too many recent failures."""

    def test_no_failures_allows(self, safety_mesh):
        """Zero recent failures → ALLOW."""
        wf = _make_workflow()

        verdict = safety_mesh._check_circuit_breaker(wf)

        assert verdict.decision == MeshDecision.ALLOW

    def test_below_threshold_allows(self, safety_mesh, audit_repo, workflow_repo):
        """2 failures (below threshold of 3) → ALLOW."""
        for i in range(2):
            # Create workflow record first (FK constraint)
            ref_wf = _make_workflow(database_id="DEV-DB-01")
            ref_wf.id = f"wf-fail-{i}"
            workflow_repo.create(ref_wf)
            audit_repo.create(
                AuditRecord(
                    workflow_id=f"wf-fail-{i}",
                    action_type="ADD_DATAFILE",
                    action_sql="ALTER TABLESPACE ...",
                    database_id="DEV-DB-01",
                    environment="DEV",
                    executed_by="agent4_executor",
                    result="FAILED",
                    error_message="test error",
                )
            )

        wf = _make_workflow(database_id="DEV-DB-01")

        verdict = safety_mesh._check_circuit_breaker(wf)

        assert verdict.decision == MeshDecision.ALLOW

    def test_at_threshold_blocks(self, safety_mesh, audit_repo, workflow_repo):
        """3 failures (= threshold) → BLOCK."""
        for i in range(3):
            ref_wf = _make_workflow(database_id="DEV-DB-01")
            ref_wf.id = f"wf-fail-{i}"
            workflow_repo.create(ref_wf)
            audit_repo.create(
                AuditRecord(
                    workflow_id=f"wf-fail-{i}",
                    action_type="ADD_DATAFILE",
                    action_sql="ALTER TABLESPACE ...",
                    database_id="DEV-DB-01",
                    environment="DEV",
                    executed_by="agent4_executor",
                    result="FAILED",
                    error_message="test error",
                )
            )

        wf = _make_workflow(database_id="DEV-DB-01")

        verdict = safety_mesh._check_circuit_breaker(wf)

        assert verdict.decision == MeshDecision.BLOCK
        assert verdict.blocked_by == "circuit_breaker"
        assert "3 failures" in verdict.reasons[0]

    def test_different_db_not_affected(self, safety_mesh, audit_repo, workflow_repo):
        """Failures on DB-A don't trigger circuit breaker for DB-B."""
        for i in range(5):
            ref_wf = _make_workflow(database_id="OTHER-DB")
            ref_wf.id = f"wf-fail-{i}"
            workflow_repo.create(ref_wf)
            audit_repo.create(
                AuditRecord(
                    workflow_id=f"wf-fail-{i}",
                    action_type="ADD_DATAFILE",
                    action_sql="ALTER TABLESPACE ...",
                    database_id="OTHER-DB",
                    environment="DEV",
                    executed_by="agent4_executor",
                    result="FAILED",
                    error_message="test error",
                )
            )

        wf = _make_workflow(database_id="DEV-DB-01")

        verdict = safety_mesh._check_circuit_breaker(wf)

        assert verdict.decision == MeshDecision.ALLOW


# ---------------------------------------------------------------------------
# Check 5: Rollback Availability
# ---------------------------------------------------------------------------


class TestRollbackCheck:
    """Test rollback availability check."""

    def test_with_rollback_allows(self, safety_mesh):
        """Rollback SQL present → ALLOW regardless of risk."""
        plan = _make_plan(
            rollback_sql="DROP DATAFILE ...",
            risk_level="HIGH",
        )

        verdict = safety_mesh._check_rollback(plan)

        assert verdict.decision == MeshDecision.ALLOW

    def test_high_risk_no_rollback_blocks(self, safety_mesh):
        """HIGH risk + no rollback → BLOCK."""
        plan = _make_plan(rollback_sql="", risk_level="HIGH")

        verdict = safety_mesh._check_rollback(plan)

        assert verdict.decision == MeshDecision.BLOCK
        assert verdict.blocked_by == "rollback_check"

    def test_critical_risk_no_rollback_blocks(self, safety_mesh):
        """CRITICAL risk + no rollback → BLOCK."""
        plan = _make_plan(rollback_sql="", risk_level="CRITICAL")

        verdict = safety_mesh._check_rollback(plan)

        assert verdict.decision == MeshDecision.BLOCK

    def test_medium_risk_no_rollback_needs_approval(self, safety_mesh):
        """MEDIUM risk + no rollback → REQUIRE_APPROVAL."""
        plan = _make_plan(rollback_sql="", risk_level="MEDIUM")

        verdict = safety_mesh._check_rollback(plan)

        assert verdict.decision == MeshDecision.REQUIRE_APPROVAL

    def test_low_risk_no_rollback_allows(self, safety_mesh):
        """LOW risk + no rollback → ALLOW (acceptable risk)."""
        plan = _make_plan(rollback_sql="", risk_level="LOW")

        verdict = safety_mesh._check_rollback(plan)

        assert verdict.decision == MeshDecision.ALLOW

    def test_na_rollback_treated_as_absent(self, safety_mesh):
        """'N/A: irreversible' is treated as no rollback."""
        plan = _make_plan(
            rollback_sql="N/A: irreversible",
            risk_level="HIGH",
        )

        verdict = safety_mesh._check_rollback(plan)

        assert verdict.decision == MeshDecision.BLOCK


# ---------------------------------------------------------------------------
# Integration: Full check() pipeline
# ---------------------------------------------------------------------------


class TestFullPipeline:
    """Test the full check() pipeline with all 5 checks."""

    def test_all_pass_returns_allow(self, safety_mesh, workflow_repo):
        """All checks pass → ALLOW."""
        wf = _make_workflow(environment="DEV")
        workflow_repo.create(wf)
        plan = _make_plan(risk_level="LOW")

        verdict = safety_mesh.check(wf, plan, confidence=0.95)

        assert verdict.allowed
        assert not verdict.blocked
        assert not verdict.needs_approval
        assert not verdict.queued

    def test_most_restrictive_wins(self, safety_mesh, workflow_repo):
        """When multiple checks flag issues, the most restrictive wins."""
        # PROD + DDL → blast radius says REQUIRE_APPROVAL
        # No rollback + MEDIUM → rollback check says REQUIRE_APPROVAL
        wf = _make_workflow(environment="PROD", database_id="PROD-DB-07")
        workflow_repo.create(wf)
        plan = _make_plan(
            rollback_sql="",
            risk_level="MEDIUM",
        )

        verdict = safety_mesh.check(wf, plan, confidence=0.95)

        assert verdict.needs_approval
        assert len(verdict.reasons) >= 2  # Multiple checks flagged

    def test_block_short_circuits(self, safety_mesh, workflow_repo, audit_repo):
        """BLOCK from circuit breaker short-circuits remaining checks."""
        # Create 3 failures for circuit breaker (need workflow records for FK)
        for i in range(3):
            ref_wf = _make_workflow(database_id="DEV-DB-01")
            ref_wf.id = f"wf-fail-{i}"
            workflow_repo.create(ref_wf)
            audit_repo.create(
                AuditRecord(
                    workflow_id=f"wf-fail-{i}",
                    action_type="ADD_DATAFILE",
                    action_sql="ALTER TABLESPACE ...",
                    database_id="DEV-DB-01",
                    environment="DEV",
                    executed_by="agent4_executor",
                    result="FAILED",
                    error_message="test error",
                )
            )

        wf = _make_workflow(database_id="DEV-DB-01")
        workflow_repo.create(wf)
        plan = _make_plan()

        verdict = safety_mesh.check(wf, plan, confidence=0.95)

        assert verdict.blocked
        assert verdict.blocked_by == "circuit_breaker"


# ---------------------------------------------------------------------------
# MeshVerdict properties
# ---------------------------------------------------------------------------


class TestMeshVerdict:
    """Test MeshVerdict dataclass properties."""

    def test_allowed(self):
        v = MeshVerdict(decision=MeshDecision.ALLOW)
        assert v.allowed and not v.blocked and not v.needs_approval and not v.queued

    def test_blocked(self):
        v = MeshVerdict(decision=MeshDecision.BLOCK)
        assert v.blocked and not v.allowed

    def test_needs_approval(self):
        v = MeshVerdict(decision=MeshDecision.REQUIRE_APPROVAL)
        assert v.needs_approval and not v.allowed and not v.blocked

    def test_queued(self):
        v = MeshVerdict(decision=MeshDecision.QUEUE)
        assert v.queued and not v.allowed and not v.blocked


# ---------------------------------------------------------------------------
# Per-Database Autonomy Overrides (v5.1a)
# ---------------------------------------------------------------------------


class TestAutonomyOverrides:
    """Test per-database autonomy override enforcement in Safety Mesh."""

    @pytest.fixture
    def env_config_mock(self):
        """Mock EnvironmentConfig that returns configurable overrides."""
        return MagicMock(spec=EnvironmentConfig)

    @pytest.fixture
    def safety_mesh_with_env(
        self,
        rules_engine,
        tmp_db,
        workflow_repo,
        audit_repo,
        alert_patterns,
        env_config_mock,
    ):
        """SafetyMesh with an EnvironmentConfig wired in."""
        return SafetyMesh(
            rules_engine=rules_engine,
            db=tmp_db,
            workflow_repo=workflow_repo,
            audit_repo=audit_repo,
            alert_patterns=alert_patterns,
            environment_config=env_config_mock,
        )

    def test_advisory_override_forces_approval_on_dev(
        self,
        safety_mesh_with_env,
        env_config_mock,
        workflow_repo,
    ):
        """ADVISORY override on a DEV DB should force REQUIRE_APPROVAL
        even though DEV normally auto-executes."""
        env_config_mock.get_autonomy_override.return_value = AutonomyOverride(
            level=AutonomyLevel.ADVISORY,
            reason="Contains copy of production data for migration testing",
            approved_by="john.smith",
        )

        wf = _make_workflow(environment="DEV", database_id="DEV-DB-01")
        workflow_repo.create(wf)
        # ADD_DATAFILE on DEV would normally be ALLOW
        plan = _make_plan(action_type="ADD_DATAFILE")

        verdict = safety_mesh_with_env.check(wf, plan, confidence=0.95)

        assert verdict.needs_approval
        assert any("ADVISORY" in r for r in verdict.reasons)

    def test_no_override_uses_normal_rules(
        self,
        safety_mesh_with_env,
        env_config_mock,
        workflow_repo,
    ):
        """No autonomy override → normal rules engine decides."""
        env_config_mock.get_autonomy_override.return_value = None

        wf = _make_workflow(environment="DEV", database_id="DEV-DB-01")
        workflow_repo.create(wf)
        plan = _make_plan(action_type="ADD_DATAFILE")

        verdict = safety_mesh_with_env.check(wf, plan, confidence=0.95)

        assert verdict.allowed

    def test_without_env_config_works_normally(self, safety_mesh, workflow_repo):
        """SafetyMesh without EnvironmentConfig (None) works normally."""
        wf = _make_workflow(environment="DEV")
        workflow_repo.create(wf)
        plan = _make_plan(action_type="ADD_DATAFILE")

        verdict = safety_mesh.check(wf, plan, confidence=0.95)

        assert verdict.allowed

    def test_override_exception_handled_gracefully(
        self,
        safety_mesh_with_env,
        env_config_mock,
        workflow_repo,
    ):
        """If get_autonomy_override() throws, mesh continues with normal rules."""
        env_config_mock.get_autonomy_override.side_effect = RuntimeError("disk error")

        wf = _make_workflow(environment="DEV", database_id="DEV-DB-01")
        workflow_repo.create(wf)
        plan = _make_plan(action_type="ADD_DATAFILE")

        # Should not raise, should fall back to normal rules
        verdict = safety_mesh_with_env.check(wf, plan, confidence=0.95)
        assert verdict.allowed
