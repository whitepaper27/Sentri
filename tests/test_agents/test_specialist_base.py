"""Tests for SpecialistBase — Universal Agent Contract (v5.0b)."""

import json

import pytest

from sentri.agents.specialist_base import SpecialistBase
from sentri.core.models import ResearchOption, Workflow
from sentri.orchestrator.safety_mesh import MeshDecision, MeshVerdict, SafetyMesh
from sentri.policy.alert_patterns import AlertPatterns
from sentri.policy.rules_engine import RulesEngine

# ---------------------------------------------------------------------------
# Concrete test subclass (SpecialistBase is abstract)
# ---------------------------------------------------------------------------


class _TestSpecialist(SpecialistBase):
    """Minimal concrete specialist for testing."""

    def __init__(self, context, safety_mesh, **kwargs):
        super().__init__("test_specialist", context, safety_mesh, **kwargs)
        self._verify_result = (True, 0.90)
        self._investigate_result = {"data": "test"}
        self._propose_result = []

    def verify(self, workflow):
        return self._verify_result

    def investigate(self, workflow):
        return self._investigate_result

    def propose(self, workflow, investigation):
        return self._propose_result


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def safety_mesh(agent_context):
    rules = RulesEngine(agent_context.policy_loader)
    alerts = AlertPatterns(agent_context.policy_loader)
    return SafetyMesh(
        rules_engine=rules,
        db=agent_context.db,
        workflow_repo=agent_context.workflow_repo,
        audit_repo=agent_context.audit_repo,
        alert_patterns=alerts,
    )


@pytest.fixture
def specialist(agent_context, safety_mesh):
    return _TestSpecialist(agent_context, safety_mesh)


def _make_option(title="Fix A", confidence=0.90, risk_level="LOW"):
    return ResearchOption(
        title=title,
        description="Test option",
        forward_sql="ALTER TABLESPACE USERS ADD DATAFILE SIZE 100M",
        rollback_sql="DROP DATAFILE ...",
        confidence=confidence,
        risk_level=risk_level,
        reasoning="test",
        source="test",
    )


def _create_workflow(workflow_repo, alert_type="tablespace_full", database_id="DEV-DB-01"):
    wf = Workflow(
        alert_type=alert_type,
        database_id=database_id,
        environment="DEV",
        status="VERIFIED",
    )
    workflow_repo.create(wf)
    return wf


# ---------------------------------------------------------------------------
# Process orchestration
# ---------------------------------------------------------------------------


class TestProcess:
    """Test the 7-step process() orchestration."""

    def test_workflow_not_found(self, specialist, agent_context):
        """Non-existent workflow returns failure."""
        result = specialist.process("nonexistent")
        assert result["status"] == "failure"
        assert "not found" in result["error"]

    def test_verify_fail_returns_failure(self, specialist, agent_context):
        """When verify() returns False, process returns failure."""
        wf = _create_workflow(agent_context.workflow_repo)
        specialist._verify_result = (False, 0.30)

        result = specialist.process(wf.id)

        assert result["status"] == "failure"
        assert "Verification failed" in result["error"]

    def test_no_candidates_returns_failure(self, specialist, agent_context):
        """When propose() returns empty, process returns failure."""
        wf = _create_workflow(agent_context.workflow_repo)
        specialist._propose_result = []

        result = specialist.process(wf.id)

        assert result["status"] == "failure"
        assert "No candidates" in result["error"]

    def test_success_with_candidates(self, specialist, agent_context):
        """Full pipeline succeeds when candidates are generated."""
        wf = _create_workflow(agent_context.workflow_repo)
        specialist._propose_result = [_make_option()]

        result = specialist.process(wf.id)

        # Should succeed (DEV, high confidence, plan built)
        assert result["status"] == "success"
        assert result["agent"] == "test_specialist"

    def test_verify_exception_handled(self, specialist, agent_context):
        """Exception in verify() is caught and returned as failure."""
        wf = _create_workflow(agent_context.workflow_repo)

        def _bad_verify(workflow):
            raise RuntimeError("verify broke")

        specialist.verify = _bad_verify

        result = specialist.process(wf.id)
        assert result["status"] == "failure"
        assert "verify" in result["error"].lower()


# ---------------------------------------------------------------------------
# Cost gate
# ---------------------------------------------------------------------------


class TestCostGate:
    """Test the cost-gated selection logic."""

    def test_template_path_high_success(self, specialist, agent_context):
        """≥95% success + ≥0.95 confidence + ≥5 history → template path (no argue)."""
        # Create 10 completed workflows for history
        for i in range(10):
            wf = Workflow(
                alert_type="tablespace_full",
                database_id="DEV-DB-01",
                environment="DEV",
                status="COMPLETED",
            )
            agent_context.workflow_repo.create(wf)

        candidates = [
            _make_option("A", confidence=0.98),
            _make_option("B", confidence=0.80),
        ]
        test_wf = Workflow(
            alert_type="tablespace_full",
            database_id="DEV-DB-01",
            environment="DEV",
            status="VERIFIED",
        )

        selected = specialist._cost_gated_selection(test_wf, candidates)

        # Should pick highest confidence without argue/select
        assert selected.title == "A"

    def test_oneshot_path_moderate_success(self, specialist, agent_context):
        """80-95% success → one-shot path (no argue, just pick best)."""
        # Create 8 completed + 2 failed = 80%
        for i in range(8):
            wf = Workflow(
                alert_type="tablespace_full",
                database_id="DEV-DB-01",
                environment="DEV",
                status="COMPLETED",
            )
            agent_context.workflow_repo.create(wf)
        for i in range(2):
            wf = Workflow(
                alert_type="tablespace_full",
                database_id="DEV-DB-01",
                environment="DEV",
                status="FAILED",
            )
            agent_context.workflow_repo.create(wf)

        candidates = [
            _make_option("A", confidence=0.80),
            _make_option("B", confidence=0.95),
        ]
        test_wf = Workflow(
            alert_type="tablespace_full",
            database_id="DEV-DB-01",
            environment="DEV",
            status="VERIFIED",
        )

        selected = specialist._cost_gated_selection(test_wf, candidates)

        assert selected.title == "B"  # Highest confidence

    def test_full_argue_select_novel(self, specialist, agent_context):
        """No history → full argue/select path."""
        candidates = [
            _make_option("A", confidence=0.80),
            _make_option("B", confidence=0.70),
        ]
        test_wf = Workflow(
            alert_type="novel_alert",
            database_id="UNKNOWN-DB",
            environment="DEV",
            status="VERIFIED",
        )

        # argue() with NoOpLLMProvider returns confidence-based scoring
        selected = specialist._cost_gated_selection(test_wf, candidates)

        # Should still work (argue falls back to confidence scoring)
        assert selected.title == "A"

    def test_historical_success_rate_query(self, specialist, agent_context):
        """Test the SQL query for historical success rate."""
        rate, total = specialist._get_historical_success_rate("tablespace_full", "DEV-DB-01")
        assert rate == 0.0
        assert total == 0

        # Create some history
        wf = Workflow(
            alert_type="tablespace_full",
            database_id="DEV-DB-01",
            environment="DEV",
            status="COMPLETED",
        )
        agent_context.workflow_repo.create(wf)

        rate, total = specialist._get_historical_success_rate("tablespace_full", "DEV-DB-01")
        assert rate == 1.0
        assert total == 1


# ---------------------------------------------------------------------------
# Scoring weights
# ---------------------------------------------------------------------------


class TestScoringWeights:
    """Test loading scoring weights from agent .md file."""

    def test_no_policy_returns_empty(self, specialist):
        """Missing policy file → empty weights."""
        weights = specialist._get_scoring_weights()
        assert weights == {}

    def test_argue_no_llm_uses_confidence(self, specialist, agent_context):
        """Without LLM, argue() falls back to confidence-based scoring."""
        candidates = [
            _make_option("A", confidence=0.90),
            _make_option("B", confidence=0.70),
        ]
        wf = _create_workflow(agent_context.workflow_repo)

        scored = specialist.argue(candidates, wf)

        assert len(scored) == 2
        assert scored[0].total_score == 0.90
        assert scored[1].total_score == 0.70


# ---------------------------------------------------------------------------
# Mesh verdict handling
# ---------------------------------------------------------------------------


class TestMeshVerdictHandling:
    """Test _handle_mesh_verdict routing."""

    def test_allow_returns_success(self, specialist, agent_context):
        """ALLOW verdict returns success."""
        wf = _create_workflow(agent_context.workflow_repo)
        plan = specialist._build_plan(wf, _make_option())
        verdict = MeshVerdict(decision=MeshDecision.ALLOW)

        result = specialist._handle_mesh_verdict(wf, plan, verdict)

        assert result["status"] == "success"

    def test_block_returns_blocked(self, specialist, agent_context):
        """BLOCK verdict returns blocked."""
        wf = _create_workflow(agent_context.workflow_repo)
        plan = specialist._build_plan(wf, _make_option())
        verdict = MeshVerdict(
            decision=MeshDecision.BLOCK,
            reasons=["circuit breaker"],
            blocked_by="circuit_breaker",
        )

        result = specialist._handle_mesh_verdict(wf, plan, verdict)

        assert result["status"] == "blocked"
        assert result["blocked_by"] == "circuit_breaker"

    def test_queue_returns_queued(self, specialist, agent_context):
        """QUEUE verdict returns queued."""
        wf = _create_workflow(agent_context.workflow_repo)
        plan = specialist._build_plan(wf, _make_option())
        verdict = MeshVerdict(
            decision=MeshDecision.QUEUE,
            reasons=["conflict"],
        )

        result = specialist._handle_mesh_verdict(wf, plan, verdict)

        assert result["status"] == "queued"

    def test_approval_updates_workflow(self, specialist, agent_context):
        """REQUIRE_APPROVAL stores the plan and updates status."""
        wf = _create_workflow(agent_context.workflow_repo)
        plan = specialist._build_plan(wf, _make_option())
        verdict = MeshVerdict(
            decision=MeshDecision.REQUIRE_APPROVAL,
            reasons=["PROD"],
        )

        result = specialist._handle_mesh_verdict(wf, plan, verdict)

        assert result["status"] == "needs_approval"
        updated = agent_context.workflow_repo.get(wf.id)
        assert updated.status == "AWAITING_APPROVAL"


# ---------------------------------------------------------------------------
# Build plan
# ---------------------------------------------------------------------------


class TestBuildPlan:
    """Test _build_plan helper."""

    def test_builds_from_option(self, specialist, agent_context):
        """Builds ExecutionPlan from ResearchOption."""
        wf = _create_workflow(agent_context.workflow_repo)
        option = _make_option()

        plan = specialist._build_plan(wf, option)

        assert plan.forward_sql == option.forward_sql
        assert plan.rollback_sql == option.rollback_sql
        assert plan.risk_level == "LOW"
        assert plan.action_type == "TABLESPACE_FULL"

    def test_extracts_params_from_suggestion(self, specialist, agent_context):
        """Params extracted from workflow suggestion."""
        wf = Workflow(
            alert_type="tablespace_full",
            database_id="DEV-DB-01",
            environment="DEV",
            status="VERIFIED",
            suggestion=json.dumps(
                {
                    "extracted_data": {"tablespace_name": "USERS", "percent_used": "95"},
                }
            ),
        )
        agent_context.workflow_repo.create(wf)

        plan = specialist._build_plan(wf, _make_option())

        assert plan.params["tablespace_name"] == "USERS"
