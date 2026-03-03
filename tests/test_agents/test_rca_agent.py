"""Tests for RCAAgent — root cause analysis specialist (v5.0e)."""

import json
from unittest.mock import patch

import pytest

from sentri.agents.rca_agent import (
    InvestigationTier,
    RCAAgent,
)
from sentri.core.models import ResearchOption, Workflow
from sentri.orchestrator.safety_mesh import SafetyMesh
from sentri.policy.alert_patterns import AlertPatterns
from sentri.policy.rules_engine import RulesEngine

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
def rca_agent(agent_context, safety_mesh):
    return RCAAgent(agent_context, safety_mesh)


def _create_workflow(
    workflow_repo,
    alert_type="session_blocker",
    database_id="DEV-DB-01",
    environment="DEV",
):
    wf = Workflow(
        alert_type=alert_type,
        database_id=database_id,
        environment=environment,
        status="VERIFIED",
    )
    workflow_repo.create(wf)
    return wf


# ---------------------------------------------------------------------------
# HANDLED_ALERTS
# ---------------------------------------------------------------------------


class TestHandledAlerts:
    def test_contains_session_blocker(self):
        assert "session_blocker" in RCAAgent.HANDLED_ALERTS

    def test_does_not_contain_storage(self):
        assert "tablespace_full" not in RCAAgent.HANDLED_ALERTS


# ---------------------------------------------------------------------------
# Verify
# ---------------------------------------------------------------------------


class TestVerify:
    """Test verify() per alert type."""

    def test_session_blocker_no_pool(self, rca_agent, agent_context):
        """session_blocker without pool → assume true."""
        wf = _create_workflow(agent_context.workflow_repo, alert_type="session_blocker")
        verified, confidence = rca_agent.verify(wf)
        assert verified is True
        assert confidence >= 0.70

    def test_correlated_always_true(self, rca_agent, agent_context):
        """Correlated incident → always true."""
        wf = _create_workflow(
            agent_context.workflow_repo,
            alert_type="tablespace_full",
        )
        verified, confidence = rca_agent.verify(wf)
        assert verified is True
        assert confidence == 0.90


# ---------------------------------------------------------------------------
# Tier 1 investigation
# ---------------------------------------------------------------------------


class TestTier1:
    """Test Tier 1 quick triage."""

    def test_no_pool_returns_basic(self, rca_agent, agent_context):
        """No oracle_pool → returns basic results dict."""
        wf = _create_workflow(agent_context.workflow_repo)
        results = rca_agent._investigate_tier1(wf)
        assert "conclusive" in results
        assert results["conclusive"] is False

    def test_tier1_always_runs(self, rca_agent, agent_context):
        """Tier 1 always runs regardless."""
        wf = _create_workflow(agent_context.workflow_repo)
        investigation = rca_agent.investigate(wf)
        assert "t1" in investigation
        assert investigation["tier"] >= InvestigationTier.T1_QUICK


# ---------------------------------------------------------------------------
# Focus area identification
# ---------------------------------------------------------------------------


class TestFocusArea:
    """Test _identify_focus_area from T1 results."""

    def test_application_waits_blocking(self, rca_agent):
        """Application wait class → blocking focus."""
        t1 = {"wait_classes": [{"WAIT_CLASS": "Application", "TOTAL_WAIT": 1000}]}
        assert rca_agent._identify_focus_area(t1) == "blocking"

    def test_user_io_storage(self, rca_agent):
        """User I/O wait class → storage focus."""
        t1 = {"wait_classes": [{"WAIT_CLASS": "User I/O", "TOTAL_WAIT": 1000}]}
        assert rca_agent._identify_focus_area(t1) == "storage"

    def test_concurrency_blocking(self, rca_agent):
        """Concurrency wait class → blocking focus."""
        t1 = {"wait_classes": [{"WAIT_CLASS": "Concurrency", "TOTAL_WAIT": 1000}]}
        assert rca_agent._identify_focus_area(t1) == "blocking"

    def test_enqueue_event_blocking(self, rca_agent):
        """Lock event in top events → blocking."""
        t1 = {
            "wait_classes": [{"WAIT_CLASS": "Other", "TOTAL_WAIT": 100}],
            "top_events": [{"EVENT": "enq: TX - row lock contention", "TIME_WAITED": 500}],
        }
        assert rca_agent._identify_focus_area(t1) == "blocking"

    def test_empty_defaults_unknown(self, rca_agent):
        """No wait data → unknown."""
        assert rca_agent._identify_focus_area({}) == "unknown"

    def test_default_to_sql_perf(self, rca_agent):
        """Non-specific wait classes → default to sql_perf."""
        t1 = {"wait_classes": [{"WAIT_CLASS": "Other", "TOTAL_WAIT": 100}]}
        # No specific events either
        assert rca_agent._identify_focus_area(t1) == "sql_perf"


# ---------------------------------------------------------------------------
# Tier 2 investigation
# ---------------------------------------------------------------------------


class TestTier2:
    """Test Tier 2 focused investigation."""

    def test_t2_not_run_if_conclusive(self, rca_agent, agent_context):
        """If T1 is conclusive, T2 doesn't run."""
        wf = _create_workflow(agent_context.workflow_repo)
        # Mock T1 to be conclusive
        with (
            patch.object(rca_agent, "_investigate_tier1") as mock_t1,
            patch.object(rca_agent, "_investigate_tier2") as mock_t2,
        ):
            mock_t1.return_value = {"conclusive": True, "wait_classes": []}
            rca_agent.investigate(wf)
            mock_t2.assert_not_called()

    def test_t2_runs_if_inconclusive(self, rca_agent, agent_context):
        """If T1 inconclusive, T2 runs."""
        wf = _create_workflow(agent_context.workflow_repo)
        investigation = rca_agent.investigate(wf)
        # Without pool, T1 returns inconclusive, so T2 should have run
        assert investigation["tier"] >= InvestigationTier.T2_FOCUSED


# ---------------------------------------------------------------------------
# Tier 3 investigation
# ---------------------------------------------------------------------------


class TestTier3:
    """Test Tier 3 full investigation."""

    def test_t3_skipped_for_prod(self, rca_agent, agent_context):
        """PROD environment → T3 not run."""
        wf = _create_workflow(
            agent_context.workflow_repo,
            environment="PROD",
            database_id="PROD-DB-07",
        )
        investigation = rca_agent.investigate(wf)
        assert "t3" not in investigation

    def test_t3_runs_for_dev(self, rca_agent, agent_context):
        """DEV environment → T3 runs if T2 inconclusive."""
        wf = _create_workflow(agent_context.workflow_repo, environment="DEV")
        investigation = rca_agent.investigate(wf)
        # Without pool, both T1 and T2 are inconclusive → T3 runs
        assert investigation["tier"] == InvestigationTier.T3_FULL
        assert "t3" in investigation


# ---------------------------------------------------------------------------
# Theory generation
# ---------------------------------------------------------------------------


class TestTheoryGeneration:
    """Test _generate_theories."""

    def test_blocking_focus_generates_theory(self, rca_agent, agent_context):
        """Blocking focus → generates blocking theory."""
        wf = _create_workflow(agent_context.workflow_repo)
        investigation = {"focus_area": "blocking", "tier": 1}
        theories = rca_agent._generate_theories_template(wf, investigation)
        assert len(theories) >= 1
        assert theories[0].focus_area == "blocking"
        assert theories[0].fix is not None

    def test_sql_perf_focus_generates_theory(self, rca_agent, agent_context):
        """sql_perf focus → generates SQL perf theory."""
        wf = _create_workflow(agent_context.workflow_repo)
        investigation = {"focus_area": "sql_perf", "tier": 1}
        theories = rca_agent._generate_theories_template(wf, investigation)
        assert len(theories) >= 1
        assert theories[0].focus_area == "sql_perf"

    def test_storage_focus_generates_theory(self, rca_agent, agent_context):
        """storage focus → generates storage theory."""
        wf = _create_workflow(agent_context.workflow_repo)
        investigation = {"focus_area": "storage", "tier": 1}
        theories = rca_agent._generate_theories_template(wf, investigation)
        assert len(theories) >= 1
        assert theories[0].focus_area == "storage"

    def test_unknown_focus_escalates(self, rca_agent, agent_context):
        """unknown focus → generates escalation theory."""
        wf = _create_workflow(agent_context.workflow_repo)
        investigation = {"focus_area": "unknown", "tier": 1}
        theories = rca_agent._generate_theories_template(wf, investigation)
        assert len(theories) >= 1
        assert (
            "escalat" in theories[0].description.lower()
            or "investigation" in theories[0].description.lower()
        )


# ---------------------------------------------------------------------------
# Parse theories
# ---------------------------------------------------------------------------


class TestParseTheories:
    """Test _parse_theories JSON parsing."""

    def test_valid_json(self, rca_agent):
        raw = json.dumps(
            [
                {
                    "description": "Root cause: blocking",
                    "confidence": 0.85,
                    "evidence": ["Wait events show lock contention"],
                    "focus_area": "blocking",
                    "fix": {
                        "title": "Kill blocking session",
                        "description": "Terminate the root blocker",
                        "forward_sql": "ALTER SYSTEM KILL SESSION '123,456'",
                        "rollback_sql": "N/A",
                        "risk_level": "MEDIUM",
                    },
                }
            ]
        )
        theories = rca_agent._parse_theories(raw)
        assert len(theories) == 1
        assert theories[0].confidence == 0.85
        assert theories[0].fix is not None

    def test_empty_returns_empty(self, rca_agent):
        assert rca_agent._parse_theories("") == []
        assert rca_agent._parse_theories("  ") == []

    def test_invalid_json_returns_empty(self, rca_agent):
        assert rca_agent._parse_theories("not json") == []


# ---------------------------------------------------------------------------
# Propose
# ---------------------------------------------------------------------------


class TestPropose:
    """Test propose() with theory generation."""

    def test_propose_converts_theories(self, rca_agent, agent_context):
        """Theories are converted to ResearchOptions."""
        wf = _create_workflow(agent_context.workflow_repo)
        investigation = {"focus_area": "blocking", "tier": 1}
        options = rca_agent.propose(wf, investigation)
        assert len(options) >= 1
        assert all(isinstance(o, ResearchOption) for o in options)

    def test_fallback_when_no_theories(self, rca_agent, agent_context):
        """Empty theories → fallback escalation option."""
        wf = _create_workflow(agent_context.workflow_repo)
        with patch.object(rca_agent, "_generate_theories", return_value=[]):
            options = rca_agent.propose(wf, {"focus_area": "unknown"})
        assert len(options) == 1
        assert "escalat" in options[0].title.lower()


# ---------------------------------------------------------------------------
# Full pipeline
# ---------------------------------------------------------------------------


class TestFullPipeline:
    """Test full process() with RCA agent."""

    def test_session_blocker_end_to_end(self, rca_agent, agent_context):
        """session_blocker pipeline completes."""
        wf = _create_workflow(agent_context.workflow_repo, alert_type="session_blocker")
        result = rca_agent.process(wf.id)
        # May succeed or need approval depending on confidence
        assert result["status"] in ("success", "needs_approval")
        assert result["agent"] == "rca_agent"

    def test_correlated_incident(self, rca_agent, agent_context):
        """Correlated incident (non-standard alert) completes."""
        wf = _create_workflow(
            agent_context.workflow_repo,
            alert_type="tablespace_full",
        )
        result = rca_agent.process(wf.id)
        assert result["status"] in ("success", "needs_approval")
