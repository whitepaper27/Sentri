"""Tests for StorageAgent — wraps existing pipeline as v5.0 specialist."""

from unittest.mock import MagicMock

import pytest

from sentri.agents.storage_agent import StorageAgent
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
def mock_auditor():
    m = MagicMock()
    m.process.return_value = {"status": "verified", "confidence": 0.92}
    return m


@pytest.fixture
def mock_researcher():
    m = MagicMock()
    m.process.return_value = {
        "status": "success",
        "options": [
            ResearchOption(
                title="Add datafile",
                description="Add a datafile to USERS tablespace",
                forward_sql="ALTER TABLESPACE USERS ADD DATAFILE SIZE 100M",
                rollback_sql="DROP DATAFILE ...",
                confidence=0.95,
                risk_level="LOW",
                reasoning="Standard fix",
                source="template",
            ),
        ],
    }
    return m


@pytest.fixture
def mock_executor():
    return MagicMock()


@pytest.fixture
def mock_analyst():
    return MagicMock()


@pytest.fixture
def storage_agent(
    agent_context, safety_mesh, mock_auditor, mock_researcher, mock_executor, mock_analyst
):
    return StorageAgent(
        context=agent_context,
        safety_mesh=safety_mesh,
        auditor=mock_auditor,
        researcher=mock_researcher,
        executor=mock_executor,
        analyst=mock_analyst,
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
# HANDLED_ALERTS
# ---------------------------------------------------------------------------


class TestHandledAlerts:
    """Test the HANDLED_ALERTS set."""

    def test_contains_expected_types(self):
        assert "tablespace_full" in StorageAgent.HANDLED_ALERTS
        assert "temp_full" in StorageAgent.HANDLED_ALERTS
        assert "archive_dest_full" in StorageAgent.HANDLED_ALERTS
        assert "high_undo_usage" in StorageAgent.HANDLED_ALERTS

    def test_does_not_contain_other_types(self):
        assert "session_blocker" not in StorageAgent.HANDLED_ALERTS
        assert "cpu_high" not in StorageAgent.HANDLED_ALERTS
        assert "long_running_sql" not in StorageAgent.HANDLED_ALERTS

    def test_is_frozen(self):
        """HANDLED_ALERTS should be immutable."""
        assert isinstance(StorageAgent.HANDLED_ALERTS, frozenset)


# ---------------------------------------------------------------------------
# Verify delegation
# ---------------------------------------------------------------------------


class TestVerify:
    """Test verify() delegating to Auditor."""

    def test_auditor_success(self, storage_agent, agent_context):
        """Auditor returns verified → True with confidence."""
        wf = _create_workflow(agent_context.workflow_repo)
        result = storage_agent.verify(wf)

        assert result == (True, 0.92)
        storage_agent._auditor.process.assert_called_once_with(wf.id)

    def test_auditor_failure(self, storage_agent, agent_context):
        """Auditor returns failure → False."""
        storage_agent._auditor.process.return_value = {
            "status": "failed",
            "confidence": 0.30,
        }
        wf = _create_workflow(agent_context.workflow_repo)
        result = storage_agent.verify(wf)

        assert result == (False, 0.30)

    def test_no_auditor_returns_default(self, agent_context, safety_mesh):
        """No auditor → assume verified with 0.80 confidence."""
        agent = StorageAgent(agent_context, safety_mesh, auditor=None)
        wf = _create_workflow(agent_context.workflow_repo)
        result = agent.verify(wf)

        assert result == (True, 0.80)


# ---------------------------------------------------------------------------
# Investigate delegation
# ---------------------------------------------------------------------------


class TestInvestigate:
    """Test investigate() — returns empty (Researcher handles both)."""

    def test_returns_empty(self, storage_agent, agent_context):
        """investigate() always returns empty dict."""
        wf = _create_workflow(agent_context.workflow_repo)
        result = storage_agent.investigate(wf)
        assert result == {}


# ---------------------------------------------------------------------------
# Propose delegation
# ---------------------------------------------------------------------------


class TestPropose:
    """Test propose() delegating to Researcher."""

    def test_researcher_success(self, storage_agent, agent_context):
        """Researcher returns options → those options returned."""
        wf = _create_workflow(agent_context.workflow_repo)
        result = storage_agent.propose(wf, {})

        assert len(result) == 1
        assert result[0].title == "Add datafile"
        storage_agent._researcher.process.assert_called_once_with(wf.id)

    def test_researcher_failure(self, storage_agent, agent_context):
        """Researcher fails → empty list."""
        storage_agent._researcher.process.return_value = {
            "status": "failure",
            "error": "LLM unavailable",
        }
        wf = _create_workflow(agent_context.workflow_repo)
        result = storage_agent.propose(wf, {})

        assert result == []

    def test_no_researcher_returns_empty(self, agent_context, safety_mesh):
        """No researcher → empty list."""
        agent = StorageAgent(agent_context, safety_mesh, researcher=None)
        wf = _create_workflow(agent_context.workflow_repo)
        result = agent.propose(wf, {})

        assert result == []


# ---------------------------------------------------------------------------
# Learn delegation
# ---------------------------------------------------------------------------


class TestLearn:
    """Test learn() delegating to Analyst."""

    def test_analyst_called(self, storage_agent, agent_context):
        """learn() delegates to Analyst."""
        wf = _create_workflow(agent_context.workflow_repo)
        option = ResearchOption(
            title="Fix",
            description="",
            forward_sql="ALTER ...",
            rollback_sql="",
            confidence=0.9,
            risk_level="LOW",
            reasoning="",
            source="test",
        )
        storage_agent.learn(wf, option, {"status": "success"})

        storage_agent._analyst.process.assert_called_once_with(wf.id)

    def test_analyst_exception_handled(self, storage_agent, agent_context):
        """Analyst exception is caught, doesn't propagate."""
        storage_agent._analyst.process.side_effect = RuntimeError("boom")
        wf = _create_workflow(agent_context.workflow_repo)
        option = ResearchOption(
            title="Fix",
            description="",
            forward_sql="ALTER ...",
            rollback_sql="",
            confidence=0.9,
            risk_level="LOW",
            reasoning="",
            source="test",
        )
        # Should not raise
        storage_agent.learn(wf, option, {"status": "success"})

    def test_no_analyst_no_error(self, agent_context, safety_mesh):
        """No analyst → no error (parent learn still runs)."""
        agent = StorageAgent(agent_context, safety_mesh, analyst=None)
        wf = _create_workflow(agent_context.workflow_repo)
        option = ResearchOption(
            title="Fix",
            description="",
            forward_sql="ALTER ...",
            rollback_sql="",
            confidence=0.9,
            risk_level="LOW",
            reasoning="",
            source="test",
        )
        agent.learn(wf, option, {"status": "success"})


# ---------------------------------------------------------------------------
# Full pipeline (process)
# ---------------------------------------------------------------------------


class TestFullPipeline:
    """Test full process() with storage agent."""

    def test_success_end_to_end(self, storage_agent, agent_context):
        """Full pipeline succeeds with mock auditor + researcher."""
        wf = _create_workflow(agent_context.workflow_repo)
        result = storage_agent.process(wf.id)

        assert result["status"] == "success"
        assert result["agent"] == "storage_agent"

    def test_verify_failure_stops_pipeline(self, storage_agent, agent_context):
        """Auditor says not verified → pipeline stops."""
        storage_agent._auditor.process.return_value = {
            "status": "failed",
            "confidence": 0.20,
        }
        wf = _create_workflow(agent_context.workflow_repo)
        result = storage_agent.process(wf.id)

        assert result["status"] == "failure"
        assert "Verification failed" in result["error"]
        # Researcher should NOT have been called
        storage_agent._researcher.process.assert_not_called()
