"""Tests for UnknownAlertAgent — handles unrecognized email alerts."""

import json
from unittest.mock import MagicMock

import pytest

from sentri.agents.unknown_alert_agent import UnknownAlertAgent
from sentri.core.constants import WorkflowStatus
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
def mock_llm():
    m = MagicMock()
    m.is_available.return_value = True
    m.name = "mock"
    m.model_id = "mock-1"
    return m


@pytest.fixture
def unknown_agent(agent_context, safety_mesh, mock_llm):
    return UnknownAlertAgent(
        context=agent_context,
        safety_mesh=safety_mesh,
        llm_provider=mock_llm,
    )


def _create_unknown_workflow(agent_context, subject="RAC Node 2 Down", body="Node 2 on PROD-DB-07 is evicted"):
    """Create a workflow with alert_type='unknown' like Scout would."""
    wf = Workflow(
        alert_type="unknown",
        database_id="UNKNOWN",
        environment="DEV",
        status=WorkflowStatus.DETECTED.value,
        suggestion=json.dumps({
            "alert_type": "unknown",
            "database_id": "UNKNOWN",
            "raw_email_subject": subject,
            "raw_email_body": body,
            "extracted_data": {
                "raw_subject": subject,
                "raw_body": body,
            },
        }),
    )
    wf_id = agent_context.workflow_repo.create(wf)
    return agent_context.workflow_repo.get(wf_id)


# ---------------------------------------------------------------------------
# Tests: Classification
# ---------------------------------------------------------------------------


class TestClassification:
    """Test LLM classification of unknown alerts."""

    def test_classify_rac_node_down(self, unknown_agent, mock_llm, agent_context):
        """LLM classifies a RAC node down alert."""
        classification = {
            "alert_type": "rac_node_down",
            "database_id": "PROD-DB-07",
            "severity": "CRITICAL",
            "description": "RAC node eviction detected",
            "email_pattern_regex": r"(?i)rac\s+node\s+(\d+)\s+.*?(down|evicted).*?(?:on|database)\s+(\S+)",
            "extracted_fields": [
                "node_number = group(1)",
                "database_id = group(3)",
            ],
            "options": [
                {
                    "title": "Check cluster status",
                    "description": "Query V$CLUSTER_INTERCONNECTS",
                    "forward_sql": "SELECT * FROM v$cluster_interconnects",
                    "rollback_sql": "N/A",
                    "confidence": 0.80,
                    "risk_level": "LOW",
                    "reasoning": "Read-only diagnostic query",
                },
            ],
            "verification_query": "SELECT inst_id, status FROM gv$instance",
            "validation_query": "SELECT COUNT(*) FROM gv$instance WHERE status='OPEN'",
        }

        # Mock LLM to return classification JSON
        mock_response = MagicMock()
        mock_response.text = json.dumps(classification)
        mock_response.is_final = True
        mock_response.tool_calls = []
        mock_llm.generate_with_tools.return_value = mock_response

        wf = _create_unknown_workflow(agent_context)
        verified, confidence = unknown_agent.verify(wf)

        assert verified is True
        assert confidence == 0.70  # Unknown alert default
        assert unknown_agent._classification["alert_type"] == "rac_node_down"

    def test_classify_non_alert(self, unknown_agent, mock_llm, agent_context):
        """LLM classifies a non-alert email."""
        classification = {
            "alert_type": "not_a_db_alert",
            "database_id": "UNKNOWN",
            "severity": "LOW",
            "description": "This is a newsletter, not a database alert",
            "options": [],
        }

        mock_response = MagicMock()
        mock_response.text = json.dumps(classification)
        mock_response.is_final = True
        mock_response.tool_calls = []
        mock_llm.generate_with_tools.return_value = mock_response

        wf = _create_unknown_workflow(
            agent_context,
            subject="Weekly DBA Newsletter",
            body="Here are this week's Oracle tips...",
        )
        verified, confidence = unknown_agent.verify(wf)

        assert verified is False
        assert confidence == 0.0

    def test_classify_no_llm(self, agent_context, safety_mesh):
        """Without LLM, classification returns unknown."""
        agent = UnknownAlertAgent(
            context=agent_context,
            safety_mesh=safety_mesh,
            llm_provider=None,
        )

        wf = _create_unknown_workflow(agent_context)
        verified, confidence = agent.verify(wf)
        assert verified is False
        assert confidence == 0.0


# ---------------------------------------------------------------------------
# Tests: Propose
# ---------------------------------------------------------------------------


class TestPropose:
    """Test remediation option extraction from classification."""

    def test_propose_extracts_options(self, unknown_agent):
        """Options are extracted from the classification response."""
        unknown_agent._classification = {
            "alert_type": "exadata_cell_down",
            "options": [
                {
                    "title": "Check cell status",
                    "forward_sql": "SELECT * FROM v$cell",
                    "rollback_sql": "N/A",
                    "confidence": 0.85,
                    "risk_level": "LOW",
                    "reasoning": "Diagnostic",
                },
                {
                    "title": "Restart cell services",
                    "forward_sql": "ALTER SYSTEM RESTART CELL 2",
                    "rollback_sql": "ALTER SYSTEM STOP CELL 2",
                    "confidence": 0.60,
                    "risk_level": "HIGH",
                    "reasoning": "Restarts the failed cell",
                },
            ],
        }

        wf = MagicMock(spec=Workflow)
        options = unknown_agent.propose(wf, unknown_agent._classification)

        assert len(options) == 2
        assert options[0].title == "Check cell status"
        assert options[0].source == "llm_unknown"
        assert options[1].risk_level == "HIGH"

    def test_propose_empty_classification(self, unknown_agent):
        """No options when classification has no remediation."""
        unknown_agent._classification = {"alert_type": "unknown", "options": []}
        wf = MagicMock(spec=Workflow)
        options = unknown_agent.propose(wf, unknown_agent._classification)
        assert options == []


# ---------------------------------------------------------------------------
# Tests: Alert .md generation
# ---------------------------------------------------------------------------


class TestAlertMdGeneration:
    """Test auto-generation of alert .md files."""

    def test_generate_md_from_template(self, unknown_agent, agent_context):
        """Generate a basic .md file from template (no LLM)."""
        from pathlib import Path

        unknown_agent._classification = {
            "alert_type": "asm_disk_failure",
            "severity": "CRITICAL",
            "description": "ASM disk group failure detected",
            "email_pattern_regex": r"(?i)asm\s+disk\s+.*?(fail|offline).*?(\S+)",
            "extracted_fields": ["database_id = group(2)"],
            "verification_query": "SELECT name, state FROM v$asm_diskgroup",
            "validation_query": "SELECT COUNT(*) FROM v$asm_diskgroup WHERE state='MOUNTED'",
        }

        selected = ResearchOption(
            title="Mount disk group",
            forward_sql="ALTER DISKGROUP DATA MOUNT",
            rollback_sql="ALTER DISKGROUP DATA DISMOUNT",
            confidence=0.80,
            risk_level="HIGH",
        )

        # Create a real workflow in DB so foreign key constraint passes
        wf = _create_unknown_workflow(agent_context, subject="ASM disk failure", body="Disk offline")

        # Force no LLM so template is used
        unknown_agent._llm = MagicMock()
        unknown_agent._llm.is_available.return_value = False

        try:
            unknown_agent._generate_alert_md(wf, selected)

            # Verify file was written — reload policy cache to pick it up
            agent_context.policy_loader.reload()
            generated = agent_context.policy_loader.load_alert("asm_disk_failure")
            assert generated.get("frontmatter", {}).get("name") == "asm_disk_failure"
            assert generated.get("frontmatter", {}).get("severity") == "CRITICAL"
            assert "ALTER DISKGROUP DATA MOUNT" in str(generated.get("forward_action", ""))
        finally:
            # Clean up generated file
            gen_path = Path(agent_context.policy_loader.base_path) / "alerts" / "asm_disk_failure.md"
            gen_path.unlink(missing_ok=True)

    def test_no_overwrite_existing_md(self, unknown_agent, agent_context):
        """Don't overwrite an existing alert .md file."""
        unknown_agent._classification = {
            "alert_type": "tablespace_full",  # Already exists
        }

        selected = ResearchOption(
            title="Test",
            forward_sql="SELECT 1",
            rollback_sql="N/A",
            confidence=0.80,
        )

        wf = _create_unknown_workflow(agent_context)

        # Should not overwrite — tablespace_full.md already exists
        unknown_agent._generate_alert_md(wf, selected)

        # Verify original is unchanged
        existing = agent_context.policy_loader.load_alert("tablespace_full")
        assert existing.get("frontmatter", {}).get("action_type") == "ADD_DATAFILE"

    def test_skip_generation_for_unknown_type(self, unknown_agent, agent_context):
        """Don't generate .md if alert_type is still 'unknown'."""
        unknown_agent._classification = {"alert_type": "unknown"}

        selected = ResearchOption(
            title="Test",
            forward_sql="SELECT 1",
            rollback_sql="N/A",
        )

        wf = _create_unknown_workflow(agent_context)

        # Should be a no-op
        unknown_agent._generate_alert_md(wf, selected)


# ---------------------------------------------------------------------------
# Tests: Integration (full process flow)
# ---------------------------------------------------------------------------


class TestFullProcess:
    """Test the full 7-step process flow for unknown alerts."""

    def test_full_process_with_classification(self, unknown_agent, mock_llm, agent_context, tmp_path):
        """Full process: classify → propose → safety mesh → approval."""
        classification = {
            "alert_type": "dataguard_lag",
            "database_id": "PROD-DB-07",
            "severity": "HIGH",
            "description": "Data Guard apply lag exceeding threshold",
            "email_pattern_regex": r"(?i)data\s*guard.*lag.*?(\d+)\s*min.*?(\S+)",
            "extracted_fields": [
                "lag_minutes = group(1)",
                "database_id = group(2)",
            ],
            "options": [
                {
                    "title": "Force log apply",
                    "forward_sql": "ALTER DATABASE RECOVER MANAGED STANDBY DATABASE USING CURRENT LOGFILE DISCONNECT FROM SESSION",
                    "rollback_sql": "ALTER DATABASE RECOVER MANAGED STANDBY DATABASE CANCEL",
                    "confidence": 0.75,
                    "risk_level": "MEDIUM",
                    "reasoning": "Restarts managed recovery process",
                },
            ],
            "verification_query": "SELECT APPLY_LAG FROM V$DATAGUARD_STATS",
            "validation_query": "SELECT APPLY_LAG FROM V$DATAGUARD_STATS",
        }

        mock_response = MagicMock()
        mock_response.text = json.dumps(classification)
        mock_response.is_final = True
        mock_response.tool_calls = []
        mock_llm.generate_with_tools.return_value = mock_response

        wf = _create_unknown_workflow(
            agent_context,
            subject="Data Guard lag alert: 45 min on PROD-DB-07",
            body="Apply lag has exceeded 30 minute threshold",
        )

        from pathlib import Path

        try:
            result = unknown_agent.process(wf.id)

            # Unknown alerts with confidence 0.70 should require approval
            assert result["status"] in ("needs_approval", "success")
            assert result["agent"] == "unknown_alert_agent"
        finally:
            # Clean up any auto-generated alert .md
            gen_path = Path(agent_context.policy_loader.base_path) / "alerts" / "dataguard_lag.md"
            gen_path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Tests: Routing
# ---------------------------------------------------------------------------


class TestRouting:
    """Test that unknown alerts are routed to UnknownAlertAgent."""

    def test_routing_rule_matches_unknown(self, agent_context):
        """Supervisor routes alert_type='unknown' to unknown_alert_agent."""
        from sentri.orchestrator.supervisor import Supervisor

        supervisor = Supervisor(agent_context, MagicMock())
        supervisor._ensure_loaded()

        agent_name = supervisor._match_routing_rule("unknown")
        assert agent_name == "unknown_alert_agent"
