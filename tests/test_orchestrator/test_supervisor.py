"""Tests for Supervisor — deterministic router + category-aware correlation."""

import threading
from unittest.mock import MagicMock

import pytest

from sentri.core.constants import WorkflowStatus
from sentri.core.models import Workflow
from sentri.orchestrator.supervisor import RoutingRule, Supervisor

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def alert_event():
    return threading.Event()


@pytest.fixture
def supervisor(agent_context, alert_event):
    return Supervisor(agent_context, alert_event)


def _make_mock_agent(name="test_agent"):
    agent = MagicMock()
    agent.name = name
    agent.process.return_value = {"status": "success"}
    return agent


def _create_workflow(
    workflow_repo,
    alert_type="tablespace_full",
    database_id="DEV-DB-01",
    status="DETECTED",
):
    wf = Workflow(
        alert_type=alert_type,
        database_id=database_id,
        environment="DEV",
        status=status,
    )
    workflow_repo.create(wf)
    return wf


# ---------------------------------------------------------------------------
# Routing rule matching
# ---------------------------------------------------------------------------


class TestRoutingRuleMatching:
    """Test _match_routing_rule with various patterns."""

    def test_exact_match(self, supervisor):
        """Exact pattern matches alert_type."""
        supervisor._routing_rules = [
            RoutingRule(pattern="tablespace_full", agent_name="storage_agent", is_wildcard=False),
        ]
        assert supervisor._match_routing_rule("tablespace_full") == "storage_agent"

    def test_wildcard_match(self, supervisor):
        """Wildcard pattern matches prefix."""
        supervisor._routing_rules = [
            RoutingRule(pattern="check_finding:*", agent_name="proactive_agent", is_wildcard=True),
        ]
        assert supervisor._match_routing_rule("check_finding:stale_stats") == "proactive_agent"
        assert supervisor._match_routing_rule("check_finding:tablespace_trend") == "proactive_agent"

    def test_wildcard_no_match(self, supervisor):
        """Wildcard doesn't match different prefix."""
        supervisor._routing_rules = [
            RoutingRule(pattern="check_finding:*", agent_name="proactive_agent", is_wildcard=True),
        ]
        # Falls back to default
        assert supervisor._match_routing_rule("tablespace_full") == supervisor._fallback_agent

    def test_fallback_when_no_match(self, supervisor):
        """No matching rule → fallback agent."""
        supervisor._routing_rules = [
            RoutingRule(pattern="tablespace_full", agent_name="storage_agent", is_wildcard=False),
        ]
        assert supervisor._match_routing_rule("unknown_alert") == supervisor._fallback_agent

    def test_first_match_wins(self, supervisor):
        """Multiple matching rules → first wins."""
        supervisor._routing_rules = [
            RoutingRule(pattern="tablespace_full", agent_name="agent_a", is_wildcard=False),
            RoutingRule(pattern="tablespace_full", agent_name="agent_b", is_wildcard=False),
        ]
        assert supervisor._match_routing_rule("tablespace_full") == "agent_a"


# ---------------------------------------------------------------------------
# Routing workflow
# ---------------------------------------------------------------------------


class TestRouteWorkflow:
    """Test _route_workflow dispatching to agents."""

    def test_routes_to_registered_agent(self, supervisor, agent_context):
        """Workflow routes to the registered agent."""
        agent = _make_mock_agent("storage_agent")
        supervisor.register_agent("storage_agent", agent)
        supervisor._routing_rules = [
            RoutingRule(pattern="tablespace_full", agent_name="storage_agent", is_wildcard=False),
        ]

        wf = _create_workflow(agent_context.workflow_repo)
        supervisor._route_workflow(wf)

        agent.process.assert_called_once_with(wf.id)

    def test_fallback_agent_used(self, supervisor, agent_context):
        """Unknown alert_type falls back to fallback agent."""
        fallback = _make_mock_agent("storage_agent")
        supervisor.register_agent("storage_agent", fallback)
        supervisor._routing_rules = []

        wf = _create_workflow(agent_context.workflow_repo, alert_type="unknown_alert")
        supervisor._route_workflow(wf)

        fallback.process.assert_called_once_with(wf.id)

    def test_no_agent_escalates(self, supervisor, agent_context):
        """No registered agent → escalate workflow."""
        supervisor._routing_rules = [
            RoutingRule(pattern="tablespace_full", agent_name="missing_agent", is_wildcard=False),
        ]

        wf = _create_workflow(agent_context.workflow_repo, status="VERIFIED")
        supervisor._route_workflow(wf)

        updated = agent_context.workflow_repo.get(wf.id)
        assert updated.status == WorkflowStatus.ESCALATED.value


# ---------------------------------------------------------------------------
# Correlation detection
# ---------------------------------------------------------------------------


class TestCorrelationDetection:
    """Test _detect_correlations grouping logic."""

    def test_no_correlations_single(self, supervisor, agent_context):
        """Single workflow → no correlations."""
        wf = _create_workflow(agent_context.workflow_repo)
        groups = supervisor._detect_correlations([wf])
        assert groups == []

    def test_correlations_same_db_same_category(self, supervisor, agent_context):
        """Two DETECTED workflows, same DB, same category → correlated."""
        supervisor._categories = {"storage": ["tablespace_full", "temp_full"]}

        wf1 = _create_workflow(agent_context.workflow_repo, alert_type="tablespace_full")
        wf2 = _create_workflow(agent_context.workflow_repo, alert_type="temp_full")

        groups = supervisor._detect_correlations([wf1, wf2])

        assert len(groups) == 1
        assert len(groups[0]) == 2

    def test_no_correlation_different_db(self, supervisor, agent_context):
        """Two workflows on different DBs → not correlated."""
        supervisor._categories = {"storage": ["tablespace_full", "temp_full"]}

        wf1 = _create_workflow(
            agent_context.workflow_repo, alert_type="tablespace_full", database_id="DEV-DB-01"
        )
        wf2 = _create_workflow(
            agent_context.workflow_repo, alert_type="temp_full", database_id="UAT-DB-03"
        )

        groups = supervisor._detect_correlations([wf1, wf2])

        assert groups == []

    def test_no_correlation_different_category(self, supervisor, agent_context):
        """Two workflows same DB but different categories → not correlated."""
        supervisor._categories = {
            "storage": ["tablespace_full"],
            "performance": ["cpu_high"],
        }

        wf1 = _create_workflow(agent_context.workflow_repo, alert_type="tablespace_full")
        wf2 = _create_workflow(agent_context.workflow_repo, alert_type="cpu_high")

        groups = supervisor._detect_correlations([wf1, wf2])

        assert groups == []

    def test_unknown_category_not_correlated(self, supervisor, agent_context):
        """Workflows with unknown category are not correlated."""
        supervisor._categories = {}  # No categories defined

        wf1 = _create_workflow(agent_context.workflow_repo, alert_type="weird_alert")
        wf2 = _create_workflow(agent_context.workflow_repo, alert_type="weird_alert")

        groups = supervisor._detect_correlations([wf1, wf2])

        assert groups == []

    def test_non_detected_excluded(self, supervisor, agent_context):
        """Non-DETECTED workflows are excluded from correlation."""
        supervisor._categories = {"storage": ["tablespace_full", "temp_full"]}

        wf1 = _create_workflow(
            agent_context.workflow_repo, alert_type="tablespace_full", status="DETECTED"
        )
        wf2 = _create_workflow(
            agent_context.workflow_repo, alert_type="temp_full", status="VERIFIED"
        )

        groups = supervisor._detect_correlations([wf1, wf2])

        assert groups == []


# ---------------------------------------------------------------------------
# Category lookup
# ---------------------------------------------------------------------------


class TestCategoryLookup:
    """Test _get_alert_category."""

    def test_known_category(self, supervisor):
        supervisor._categories = {"storage": ["tablespace_full", "temp_full"]}
        assert supervisor._get_alert_category("tablespace_full") == "storage"

    def test_unknown_returns_unknown(self, supervisor):
        supervisor._categories = {"storage": ["tablespace_full"]}
        assert supervisor._get_alert_category("cpu_high") == "unknown"


# ---------------------------------------------------------------------------
# Correlated incident routing
# ---------------------------------------------------------------------------


class TestCorrelatedRouting:
    """Test _route_correlated_incident."""

    def test_routes_to_rca_agent(self, supervisor, agent_context):
        """Correlated group routes primary to RCA agent."""
        rca = _make_mock_agent("rca_agent")
        supervisor.register_agent("rca_agent", rca)

        wf1 = _create_workflow(agent_context.workflow_repo)
        wf2 = _create_workflow(agent_context.workflow_repo)

        supervisor._route_correlated_incident([wf1, wf2])

        rca.process.assert_called_once_with(wf1.id)

    def test_no_rca_routes_individually(self, supervisor, agent_context):
        """No RCA agent registered → route each individually."""
        storage = _make_mock_agent("storage_agent")
        supervisor.register_agent("storage_agent", storage)
        supervisor._routing_rules = [
            RoutingRule(pattern="tablespace_full", agent_name="storage_agent", is_wildcard=False),
        ]

        wf1 = _create_workflow(agent_context.workflow_repo)
        wf2 = _create_workflow(agent_context.workflow_repo)

        supervisor._route_correlated_incident([wf1, wf2])

        assert storage.process.call_count == 2


# ---------------------------------------------------------------------------
# Register agent
# ---------------------------------------------------------------------------


class TestRegisterAgent:
    """Test register_agent."""

    def test_register_and_lookup(self, supervisor):
        agent = _make_mock_agent("storage_agent")
        supervisor.register_agent("storage_agent", agent)
        assert supervisor._agents["storage_agent"] is agent

    def test_register_multiple(self, supervisor):
        a1 = _make_mock_agent("storage_agent")
        a2 = _make_mock_agent("rca_agent")
        supervisor.register_agent("storage_agent", a1)
        supervisor.register_agent("rca_agent", a2)
        assert len(supervisor._agents) == 2


# ---------------------------------------------------------------------------
# Stop
# ---------------------------------------------------------------------------


class TestStop:
    """Test stop() signals."""

    def test_stop_sets_flag(self, supervisor):
        supervisor.stop()
        assert supervisor._stop is True
