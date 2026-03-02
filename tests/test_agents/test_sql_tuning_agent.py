"""Tests for SQLTuningAgent — performance specialist (v5.0d)."""

import json

import pytest

from sentri.agents.sql_tuning_agent import SQLTuningAgent
from sentri.core.models import Workflow
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
def tuning_agent(agent_context, safety_mesh):
    return SQLTuningAgent(agent_context, safety_mesh)


def _create_workflow(
    workflow_repo,
    alert_type="cpu_high",
    database_id="DEV-DB-01",
    suggestion=None,
):
    wf = Workflow(
        alert_type=alert_type,
        database_id=database_id,
        environment="DEV",
        status="VERIFIED",
        suggestion=suggestion,
    )
    workflow_repo.create(wf)
    return wf


# ---------------------------------------------------------------------------
# HANDLED_ALERTS
# ---------------------------------------------------------------------------


class TestHandledAlerts:
    """Test HANDLED_ALERTS set."""

    def test_contains_performance_alerts(self):
        assert "long_running_sql" in SQLTuningAgent.HANDLED_ALERTS
        assert "cpu_high" in SQLTuningAgent.HANDLED_ALERTS
        assert "check_finding:stale_stats" in SQLTuningAgent.HANDLED_ALERTS

    def test_does_not_contain_storage(self):
        assert "tablespace_full" not in SQLTuningAgent.HANDLED_ALERTS


# ---------------------------------------------------------------------------
# Verify
# ---------------------------------------------------------------------------


class TestVerify:
    """Test verify() per alert type."""

    def test_stale_stats_always_true(self, tuning_agent, agent_context):
        """stale_stats always returns True with high confidence."""
        wf = _create_workflow(
            agent_context.workflow_repo,
            alert_type="check_finding:stale_stats",
        )
        verified, confidence = tuning_agent.verify(wf)
        assert verified is True
        assert confidence == 0.95

    def test_cpu_high_no_pool(self, tuning_agent, agent_context):
        """cpu_high without oracle_pool → assume true."""
        wf = _create_workflow(agent_context.workflow_repo, alert_type="cpu_high")
        verified, confidence = tuning_agent.verify(wf)
        assert verified is True
        assert confidence >= 0.70

    def test_long_running_no_pool(self, tuning_agent, agent_context):
        """long_running_sql without oracle_pool → assume true."""
        wf = _create_workflow(
            agent_context.workflow_repo,
            alert_type="long_running_sql",
        )
        verified, confidence = tuning_agent.verify(wf)
        assert verified is True
        assert confidence >= 0.70

    def test_unknown_alert_returns_true(self, tuning_agent, agent_context):
        """Unknown alert type → assume valid."""
        wf = _create_workflow(
            agent_context.workflow_repo,
            alert_type="some_unknown_perf_alert",
        )
        verified, confidence = tuning_agent.verify(wf)
        assert verified is True
        assert confidence == 0.70


# ---------------------------------------------------------------------------
# Investigate
# ---------------------------------------------------------------------------


class TestInvestigate:
    """Test investigate() per alert type."""

    def test_cpu_high_no_pool(self, tuning_agent, agent_context):
        """cpu_high without pool → returns basic context."""
        wf = _create_workflow(agent_context.workflow_repo, alert_type="cpu_high")
        result = tuning_agent.investigate(wf)
        assert result["alert_type"] == "cpu_high"
        assert result["database_id"] == "DEV-DB-01"

    def test_long_running_no_pool(self, tuning_agent, agent_context):
        """long_running without pool → returns basic context."""
        suggestion = json.dumps(
            {
                "extracted_data": {"sid": "42", "sql_id": "abc123"},
            }
        )
        wf = _create_workflow(
            agent_context.workflow_repo,
            alert_type="long_running_sql",
            suggestion=suggestion,
        )
        result = tuning_agent.investigate(wf)
        assert result["alert_type"] == "long_running_sql"

    def test_stale_stats_passes_findings(self, tuning_agent, agent_context):
        """stale_stats passes finding data through."""
        suggestion = json.dumps(
            {
                "extracted_data": {
                    "findings": [{"OWNER": "HR", "TABLE_NAME": "EMPLOYEES"}],
                },
            }
        )
        wf = _create_workflow(
            agent_context.workflow_repo,
            alert_type="check_finding:stale_stats",
            suggestion=suggestion,
        )
        result = tuning_agent.investigate(wf)
        assert result["alert_type"] == "check_finding:stale_stats"
        assert len(result["stale_tables"]) == 1

    def test_unknown_alert_returns_context(self, tuning_agent, agent_context):
        """Unknown alert → returns basic context."""
        wf = _create_workflow(
            agent_context.workflow_repo,
            alert_type="something_else",
        )
        result = tuning_agent.investigate(wf)
        assert result["alert_type"] == "something_else"


# ---------------------------------------------------------------------------
# Propose (template fallback)
# ---------------------------------------------------------------------------


class TestProposeTemplate:
    """Test propose() template fallback (no LLM)."""

    def test_stale_stats_generates_options(self, tuning_agent, agent_context):
        """stale_stats template generates gather stats options."""
        wf = _create_workflow(
            agent_context.workflow_repo,
            alert_type="check_finding:stale_stats",
        )
        investigation = {
            "alert_type": "check_finding:stale_stats",
            "stale_tables": [
                {"OWNER": "HR", "TABLE_NAME": "EMPLOYEES"},
                {"OWNER": "HR", "TABLE_NAME": "DEPARTMENTS"},
            ],
            "extracted": {},
        }
        options = tuning_agent.propose(wf, investigation)
        assert len(options) >= 1
        assert "DBMS_STATS" in options[0].forward_sql

    def test_cpu_high_generates_options(self, tuning_agent, agent_context):
        """cpu_high template generates investigation options."""
        wf = _create_workflow(agent_context.workflow_repo, alert_type="cpu_high")
        investigation = {
            "alert_type": "cpu_high",
            "database_id": "DEV-DB-01",
            "top_sql": [{"SQL_ID": "abc123"}],
            "extracted": {},
        }
        options = tuning_agent.propose(wf, investigation)
        assert len(options) >= 1

    def test_long_running_generates_options(self, tuning_agent, agent_context):
        """long_running template generates analysis options."""
        wf = _create_workflow(
            agent_context.workflow_repo,
            alert_type="long_running_sql",
        )
        investigation = {
            "alert_type": "long_running_sql",
            "database_id": "DEV-DB-01",
            "extracted": {"sid": "42", "sql_id": "abc123"},
        }
        options = tuning_agent.propose(wf, investigation)
        assert len(options) >= 1

    def test_empty_stale_tables_returns_empty(self, tuning_agent, agent_context):
        """stale_stats with no tables → empty options."""
        wf = _create_workflow(
            agent_context.workflow_repo,
            alert_type="check_finding:stale_stats",
        )
        investigation = {
            "alert_type": "check_finding:stale_stats",
            "stale_tables": [],
            "extracted": {},
        }
        options = tuning_agent.propose(wf, investigation)
        assert options == []


# ---------------------------------------------------------------------------
# Parse options
# ---------------------------------------------------------------------------


class TestParseOptions:
    """Test _parse_options JSON parsing."""

    def test_valid_json(self, tuning_agent):
        raw = json.dumps(
            [
                {
                    "title": "Fix A",
                    "description": "Desc",
                    "forward_sql": "SELECT 1",
                    "rollback_sql": "N/A",
                    "confidence": 0.90,
                    "risk_level": "LOW",
                    "reasoning": "test",
                }
            ]
        )
        options = tuning_agent._parse_options(raw)
        assert len(options) == 1
        assert options[0].title == "Fix A"

    def test_empty_returns_empty(self, tuning_agent):
        assert tuning_agent._parse_options("") == []
        assert tuning_agent._parse_options("   ") == []

    def test_invalid_json_returns_empty(self, tuning_agent):
        assert tuning_agent._parse_options("not json") == []

    def test_markdown_fenced_json(self, tuning_agent):
        raw = '```json\n[{"title":"A","description":"B","forward_sql":"C","rollback_sql":"D","confidence":0.8,"risk_level":"LOW","reasoning":"E"}]\n```'
        options = tuning_agent._parse_options(raw)
        assert len(options) == 1


# ---------------------------------------------------------------------------
# Full pipeline
# ---------------------------------------------------------------------------


class TestFullPipeline:
    """Test full process() with SQL tuning agent."""

    def test_stale_stats_end_to_end(self, tuning_agent, agent_context):
        """stale_stats pipeline succeeds end to end."""
        suggestion = json.dumps(
            {
                "extracted_data": {
                    "findings": [{"OWNER": "HR", "TABLE_NAME": "EMPLOYEES"}],
                },
            }
        )
        wf = _create_workflow(
            agent_context.workflow_repo,
            alert_type="check_finding:stale_stats",
            suggestion=suggestion,
        )
        result = tuning_agent.process(wf.id)
        assert result["status"] == "success"
        assert result["agent"] == "sql_tuning_agent"

    def test_cpu_high_with_top_sql(self, tuning_agent, agent_context):
        """cpu_high pipeline completes (may need approval due to moderate confidence)."""
        wf = _create_workflow(agent_context.workflow_repo, alert_type="cpu_high")
        result = tuning_agent.process(wf.id)
        # Template confidence is 0.75 → may trigger approval in policy gate
        assert result["status"] in ("success", "needs_approval")
        assert result["agent"] == "sql_tuning_agent"
