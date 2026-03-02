"""Tests for ProactiveAgent — scheduled health checker (v5.0c)."""

import json
import threading
from datetime import datetime, timedelta, timezone

import pytest

from sentri.agents.proactive_agent import (
    SCHEDULE_INTERVALS,
    CheckState,
    ProactiveAgent,
)
from sentri.config.settings import DatabaseConfig

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def alert_event():
    return threading.Event()


@pytest.fixture
def proactive_agent(agent_context, alert_event):
    return ProactiveAgent(agent_context, alert_event)


# ---------------------------------------------------------------------------
# Schedule calculation
# ---------------------------------------------------------------------------


class TestScheduleCalculation:
    """Test _is_due and schedule intervals."""

    def test_never_run_is_due(self, proactive_agent):
        """Check that was never run is always due."""
        state = CheckState(check_type="test", last_run=None)
        now = datetime.now(timezone.utc)
        assert proactive_agent._is_due(state, now) is True

    def test_recently_run_not_due(self, proactive_agent):
        """Check that ran 1 hour ago is not due (daily schedule)."""
        now = datetime.now(timezone.utc)
        state = CheckState(
            check_type="test",
            last_run=now - timedelta(hours=1),
            interval_seconds=SCHEDULE_INTERVALS["daily"],
        )
        assert proactive_agent._is_due(state, now) is False

    def test_expired_is_due(self, proactive_agent):
        """Check that ran 25 hours ago is due (daily schedule)."""
        now = datetime.now(timezone.utc)
        state = CheckState(
            check_type="test",
            last_run=now - timedelta(hours=25),
            interval_seconds=SCHEDULE_INTERVALS["daily"],
        )
        assert proactive_agent._is_due(state, now) is True

    def test_6_hour_schedule(self, proactive_agent):
        """6-hour schedule: 7 hours ago is due."""
        now = datetime.now(timezone.utc)
        state = CheckState(
            check_type="test",
            last_run=now - timedelta(hours=7),
            interval_seconds=SCHEDULE_INTERVALS["every_6_hours"],
        )
        assert proactive_agent._is_due(state, now) is True


# ---------------------------------------------------------------------------
# Threshold evaluation
# ---------------------------------------------------------------------------


class TestThresholdEvaluation:
    """Test _exceeds_threshold logic."""

    def test_exceeds_numeric_threshold(self, proactive_agent):
        """Finding above threshold returns True."""
        findings = [{"pct_used": 92.5}]
        threshold = {"pct_used": 85}
        assert proactive_agent._exceeds_threshold(findings, threshold) is True

    def test_below_threshold(self, proactive_agent):
        """Finding below threshold returns False."""
        findings = [{"pct_used": 70}]
        threshold = {"pct_used": 85}
        assert proactive_agent._exceeds_threshold(findings, threshold) is False

    def test_empty_findings_false(self, proactive_agent):
        """No findings → False."""
        assert proactive_agent._exceeds_threshold([], {"pct_used": 85}) is False

    def test_no_threshold_any_finding_true(self, proactive_agent):
        """No threshold configured → any findings = True."""
        findings = [{"something": 1}]
        assert proactive_agent._exceeds_threshold(findings, {}) is True

    def test_threshold_key_not_in_finding(self, proactive_agent):
        """Threshold key not in finding → False (no match)."""
        findings = [{"other_key": 99}]
        threshold = {"pct_used": 85}
        assert proactive_agent._exceeds_threshold(findings, threshold) is False


# ---------------------------------------------------------------------------
# Workflow creation
# ---------------------------------------------------------------------------


class TestWorkflowCreation:
    """Test _create_finding_workflow."""

    def test_creates_workflow(self, proactive_agent, agent_context):
        """Creates a workflow with check_finding: prefix."""
        db_cfg = DatabaseConfig(
            name="DEV-DB-01",
            connection_string="oracle://test",
            environment="DEV",
        )
        findings = [{"tablespace_name": "USERS", "pct_used": 92}]

        wf_id = proactive_agent._create_finding_workflow(
            "tablespace_trend",
            db_cfg,
            findings,
        )

        assert wf_id is not None
        wf = agent_context.workflow_repo.get(wf_id)
        assert wf.alert_type == "check_finding:tablespace_trend"
        assert wf.database_id == "DEV-DB-01"
        assert wf.status == "DETECTED"

    def test_suggestion_contains_findings(self, proactive_agent, agent_context):
        """Created workflow suggestion contains findings JSON."""
        db_cfg = DatabaseConfig(
            name="DEV-DB-01",
            connection_string="oracle://test",
            environment="DEV",
        )
        findings = [{"tablespace_name": "USERS", "pct_used": 92}]

        wf_id = proactive_agent._create_finding_workflow(
            "tablespace_trend",
            db_cfg,
            findings,
        )
        wf = agent_context.workflow_repo.get(wf_id)
        suggestion = json.loads(wf.suggestion)
        assert suggestion["check_type"] == "tablespace_trend"
        assert len(suggestion["findings"]) == 1

    def test_signals_alert_event(self, proactive_agent, alert_event, agent_context):
        """Creating a finding signals the alert event for Supervisor."""
        db_cfg = DatabaseConfig(
            name="DEV-DB-01",
            connection_string="oracle://test",
            environment="DEV",
        )
        findings = [{"pct_used": 92}]

        proactive_agent._create_finding_workflow(
            "tablespace_trend",
            db_cfg,
            findings,
        )

        assert alert_event.is_set()

    def test_dedup_skips_recent(self, proactive_agent, agent_context):
        """Duplicate finding within 6 hours is skipped."""
        db_cfg = DatabaseConfig(
            name="DEV-DB-01",
            connection_string="oracle://test",
            environment="DEV",
        )
        findings = [{"pct_used": 92}]

        # First creation
        wf_id1 = proactive_agent._create_finding_workflow(
            "tablespace_trend",
            db_cfg,
            findings,
        )
        assert wf_id1 is not None

        # Second creation (within 6 hours)
        wf_id2 = proactive_agent._create_finding_workflow(
            "tablespace_trend",
            db_cfg,
            findings,
        )
        assert wf_id2 is None  # Skipped as duplicate


# ---------------------------------------------------------------------------
# Load check definitions
# ---------------------------------------------------------------------------


class TestLoadChecks:
    """Test _load_check_definitions."""

    def test_loads_check_states(self, proactive_agent):
        """Loading discovers stale_stats and tablespace_trend."""
        proactive_agent._load_check_definitions()

        assert "stale_stats" in proactive_agent._check_states
        assert "tablespace_trend" in proactive_agent._check_states

    def test_schedule_interval_mapped(self, proactive_agent):
        """Schedule strings are mapped to interval seconds."""
        proactive_agent._load_check_definitions()

        stale = proactive_agent._check_states["stale_stats"]
        assert stale.interval_seconds == SCHEDULE_INTERVALS["daily"]

        trend = proactive_agent._check_states["tablespace_trend"]
        assert trend.interval_seconds == SCHEDULE_INTERVALS["every_6_hours"]


# ---------------------------------------------------------------------------
# Stop
# ---------------------------------------------------------------------------


class TestStop:
    """Test stop() signals."""

    def test_stop_sets_flag(self, proactive_agent):
        proactive_agent.stop()
        assert proactive_agent._stop is True


# ---------------------------------------------------------------------------
# Execute health query (no pool)
# ---------------------------------------------------------------------------


class TestExecuteHealthQuery:
    """Test _execute_health_query."""

    def test_no_pool_returns_empty(self, proactive_agent):
        """No oracle_pool → empty results."""
        result = proactive_agent._execute_health_query("DEV-DB-01", "SELECT 1")
        assert result == []
