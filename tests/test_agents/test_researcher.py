"""Tests for Agent 3 (Researcher) — three-level fallback and tool loop."""

import json
from unittest.mock import MagicMock, patch

import pytest

from sentri.agents.researcher import _MAX_TOOL_CALLS, ResearcherAgent
from sentri.core.llm_interface import (
    GenerateWithToolsResponse,
    NoOpLLMProvider,
    ToolCall,
    ToolResult,
)
from sentri.core.models import Workflow

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_llm():
    """LLM provider mock that supports both generate() and generate_with_tools()."""
    llm = MagicMock()
    llm.is_available.return_value = True
    llm.name = "MockLLM"
    return llm


@pytest.fixture
def workflow(agent_context):
    """Create a test workflow in the DB."""
    from sentri.core.models import Suggestion

    suggestion = Suggestion(
        alert_type="tablespace_full",
        database_id="DEV-DB-01",
        raw_email_subject="Tablespace USERS 92% full on DEV-DB-01",
        raw_email_body="Body text",
        extracted_data={
            "tablespace_name": "USERS",
            "used_percent": "92",
            "database_id": "DEV-DB-01",
        },
    )

    wf = Workflow(
        alert_type="tablespace_full",
        database_id="DEV-DB-01",
        environment="DEV",
        suggestion=suggestion.to_json(),
    )
    wf_id = agent_context.workflow_repo.create(wf)
    return agent_context.workflow_repo.get(wf_id)


# ---------------------------------------------------------------------------
# Level 3: Template fallback (no LLM)
# ---------------------------------------------------------------------------


class TestTemplateLevel:
    def test_no_llm_uses_template(self, agent_context, workflow):
        """When LLM is unavailable, fall back to .md template."""
        researcher = ResearcherAgent(agent_context, llm_provider=NoOpLLMProvider())
        result = researcher.process(workflow.id)

        assert result["status"] == "success"
        assert result["source"] == "template"
        assert len(result["options"]) >= 1
        assert result["selected_option"].source == "template"

    def test_template_has_forward_sql(self, agent_context, workflow):
        researcher = ResearcherAgent(agent_context, llm_provider=NoOpLLMProvider())
        result = researcher.process(workflow.id)

        option = result["selected_option"]
        assert option.forward_sql, "Template option must have forward SQL"
        assert option.rollback_sql, "Template option must have rollback SQL"

    def test_template_substitutes_params(self, agent_context, workflow):
        """Template should replace :tablespace_name with actual value."""
        researcher = ResearcherAgent(agent_context, llm_provider=NoOpLLMProvider())
        result = researcher.process(workflow.id)

        option = result["selected_option"]
        # Should NOT contain :tablespace_name placeholder
        assert ":tablespace_name" not in option.forward_sql
        # Should contain actual tablespace name
        assert "USERS" in option.forward_sql


# ---------------------------------------------------------------------------
# Level 2: One-shot LLM (no tool calling)
# ---------------------------------------------------------------------------


class TestOneshotLevel:
    def test_oneshot_parses_json_response(self, agent_context, workflow, mock_llm):
        """When agentic fails, fall back to one-shot generate()."""
        llm_response = json.dumps(
            [
                {
                    "title": "Add datafile",
                    "description": "Add 10G datafile",
                    "forward_sql": "ALTER TABLESPACE USERS ADD DATAFILE SIZE 10G",
                    "rollback_sql": "ALTER TABLESPACE USERS DROP DATAFILE '/path'",
                    "confidence": 0.9,
                    "risk_level": "LOW",
                    "reasoning": "Standard approach",
                }
            ]
        )

        # Make agentic fail, one-shot succeed
        mock_llm.generate_with_tools.side_effect = Exception("Agentic not supported")
        mock_llm.generate.return_value = llm_response

        researcher = ResearcherAgent(agent_context, llm_provider=mock_llm)
        result = researcher.process(workflow.id)

        assert result["status"] == "success"
        assert result["source"] == "llm"
        assert len(result["options"]) == 1
        assert result["options"][0].title == "Add datafile"

    def test_oneshot_strips_markdown_fences(self, agent_context, workflow, mock_llm):
        """LLM sometimes wraps JSON in ```json ... ```."""
        llm_response = '```json\n[{"title":"Fix","forward_sql":"SELECT 1","rollback_sql":"N/A","confidence":0.8,"risk_level":"LOW","reasoning":"test"}]\n```'

        mock_llm.generate_with_tools.side_effect = Exception("fail")
        mock_llm.generate.return_value = llm_response

        researcher = ResearcherAgent(agent_context, llm_provider=mock_llm)
        result = researcher.process(workflow.id)

        assert result["status"] == "success"
        assert len(result["options"]) == 1

    def test_oneshot_invalid_json_falls_to_template(self, agent_context, workflow, mock_llm):
        """If LLM returns garbage, fall back to template."""
        mock_llm.generate_with_tools.side_effect = Exception("fail")
        mock_llm.generate.return_value = "I think you should add a datafile."

        researcher = ResearcherAgent(agent_context, llm_provider=mock_llm)
        result = researcher.process(workflow.id)

        assert result["status"] == "success"
        assert result["source"] == "template"


# ---------------------------------------------------------------------------
# Level 1: Agentic tool-calling
# ---------------------------------------------------------------------------


class TestAgenticLevel:
    def test_agentic_single_tool_call(self, agent_context, workflow, mock_llm):
        """LLM calls one tool, then returns final JSON."""
        # Turn 1: LLM wants to call a tool
        turn1 = GenerateWithToolsResponse(
            text="Let me check the tablespace.",
            tool_calls=[
                ToolCall(
                    "c1",
                    "get_tablespace_info",
                    {
                        "database_id": "DEV-DB-01",
                        "tablespace_name": "USERS",
                    },
                )
            ],
            is_final=False,
        )
        # Turn 2: LLM returns final answer
        turn2 = GenerateWithToolsResponse(
            text=json.dumps(
                [
                    {
                        "title": "Resize bigfile datafile",
                        "description": "USERS is a bigfile tablespace",
                        "forward_sql": "ALTER DATABASE DATAFILE '/opt/oracle/users01.dbf' RESIZE 20G",
                        "rollback_sql": "ALTER DATABASE DATAFILE '/opt/oracle/users01.dbf' RESIZE 10G",
                        "confidence": 0.95,
                        "risk_level": "LOW",
                        "reasoning": "Bigfile tablespace — must resize, not add",
                    }
                ]
            ),
            is_final=True,
        )
        mock_llm.generate_with_tools.side_effect = [turn1, turn2]
        mock_llm.format_tool_results.return_value = [
            {"role": "assistant", "content": "checking..."},
            {"role": "tool", "content": '{"bigfile": "YES"}'},
        ]

        # Mock tool executor
        mock_tool_result = ToolResult("c1", "get_tablespace_info", '{"bigfile": "YES"}')
        with patch("sentri.llm.tools.DBAToolExecutor") as MockExec:
            MockExec.return_value.execute.return_value = mock_tool_result
            researcher = ResearcherAgent(agent_context, llm_provider=mock_llm)
            result = researcher.process(workflow.id)

        assert result["status"] == "success"
        assert result["source"] == "llm_agentic"
        assert "RESIZE" in result["selected_option"].forward_sql

    def test_agentic_respects_max_tool_calls(self, agent_context, workflow, mock_llm):
        """Must stop after _MAX_TOOL_CALLS tool calls."""
        # Create responses that keep requesting tools
        tool_response = GenerateWithToolsResponse(
            text="Need more info.",
            tool_calls=[
                ToolCall(f"c{i}", "get_instance_info", {"database_id": "DEV-DB-01"})
                for i in range(1)
            ],
            is_final=False,
        )
        final_response = GenerateWithToolsResponse(
            text=json.dumps(
                [
                    {
                        "title": "Fix",
                        "forward_sql": "SELECT 1 FROM dual",
                        "rollback_sql": "N/A",
                        "confidence": 0.8,
                        "risk_level": "LOW",
                        "reasoning": "test",
                    }
                ]
            ),
            is_final=True,
        )

        # Return tool_response for _MAX_TOOL_CALLS times, then final
        responses = [tool_response] * _MAX_TOOL_CALLS
        # After tool limit, the researcher appends "limit reached" message
        # and the next call should get the final response
        responses.append(final_response)
        mock_llm.generate_with_tools.side_effect = responses
        mock_llm.format_tool_results.return_value = [
            {"role": "assistant", "content": ""},
            {"role": "tool", "content": "{}"},
        ]

        mock_tool_result = ToolResult("c0", "get_instance_info", '{"version": "21c"}')
        with patch("sentri.llm.tools.DBAToolExecutor") as MockExec:
            MockExec.return_value.execute.return_value = mock_tool_result
            researcher = ResearcherAgent(agent_context, llm_provider=mock_llm)
            result = researcher.process(workflow.id)

        assert result["status"] == "success"

    def test_agentic_failure_falls_to_oneshot(self, agent_context, workflow, mock_llm):
        """If agentic crashes, fall to one-shot."""
        mock_llm.generate_with_tools.side_effect = Exception("API rate limit")
        mock_llm.generate.return_value = json.dumps(
            [
                {
                    "title": "Template fix",
                    "forward_sql": "ALTER TABLESPACE USERS ADD DATAFILE SIZE 10G",
                    "rollback_sql": "N/A",
                    "confidence": 0.85,
                    "risk_level": "LOW",
                    "reasoning": "Standard",
                }
            ]
        )

        researcher = ResearcherAgent(agent_context, llm_provider=mock_llm)
        result = researcher.process(workflow.id)

        assert result["status"] == "success"
        assert result["source"] == "llm"

    def test_all_llm_fails_falls_to_template(self, agent_context, workflow, mock_llm):
        """If both agentic and one-shot fail, fall to template."""
        mock_llm.generate_with_tools.side_effect = Exception("Agentic failed")
        mock_llm.generate.side_effect = Exception("One-shot also failed")

        researcher = ResearcherAgent(agent_context, llm_provider=mock_llm)
        result = researcher.process(workflow.id)

        assert result["status"] == "success"
        assert result["source"] == "template"


# ---------------------------------------------------------------------------
# Budget / availability checks
# ---------------------------------------------------------------------------


class TestBudgetAndAvailability:
    def test_budget_exhausted_uses_template(self, agent_context, workflow):
        llm = MagicMock()
        llm.is_available.return_value = True
        cost_tracker = MagicMock()
        cost_tracker.is_within_budget.return_value = False

        researcher = ResearcherAgent(
            agent_context,
            llm_provider=llm,
            cost_tracker=cost_tracker,
        )
        result = researcher.process(workflow.id)

        assert result["source"] == "template"
        llm.generate.assert_not_called()

    def test_llm_unavailable_uses_template(self, agent_context, workflow):
        llm = MagicMock()
        llm.is_available.return_value = False

        researcher = ResearcherAgent(agent_context, llm_provider=llm)
        result = researcher.process(workflow.id)

        assert result["source"] == "template"


# ---------------------------------------------------------------------------
# Workflow not found
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_workflow_not_found(self, agent_context):
        researcher = ResearcherAgent(agent_context, llm_provider=NoOpLLMProvider())
        result = researcher.process("nonexistent-id")
        assert result["status"] == "failure"
        assert "not found" in result["error"]

    def test_options_sorted_by_confidence(self, agent_context, workflow, mock_llm):
        """Selected option should be the highest confidence."""
        mock_llm.generate_with_tools.side_effect = Exception("fail")
        mock_llm.generate.return_value = json.dumps(
            [
                {
                    "title": "Low",
                    "forward_sql": "SELECT 1",
                    "rollback_sql": "N/A",
                    "confidence": 0.5,
                    "risk_level": "HIGH",
                    "reasoning": "risky",
                },
                {
                    "title": "High",
                    "forward_sql": "SELECT 2",
                    "rollback_sql": "N/A",
                    "confidence": 0.95,
                    "risk_level": "LOW",
                    "reasoning": "safe",
                },
            ]
        )

        researcher = ResearcherAgent(agent_context, llm_provider=mock_llm)
        result = researcher.process(workflow.id)

        assert result["selected_option"].title == "High"
        assert result["selected_option"].confidence == 0.95


# ---------------------------------------------------------------------------
# SQL Validation Pipeline (v3.1 — ground truth)
# ---------------------------------------------------------------------------


class TestSQLValidationPipeline:
    """Tests for the post-LLM SQL validation step in the researcher."""

    def test_valid_sql_passes_through(self, agent_context, workflow, mock_llm):
        """Valid SQL should pass validation and be returned."""
        mock_llm.generate_with_tools.side_effect = Exception("fail")
        mock_llm.generate.return_value = json.dumps(
            [
                {
                    "title": "Safe resize",
                    "forward_sql": "ALTER DATABASE DATAFILE '/opt/users01.dbf' RESIZE 20G",
                    "rollback_sql": "ALTER DATABASE DATAFILE '/opt/users01.dbf' RESIZE 10G",
                    "confidence": 0.9,
                    "risk_level": "LOW",
                    "reasoning": "Safe resize operation",
                }
            ]
        )

        researcher = ResearcherAgent(agent_context, llm_provider=mock_llm)
        result = researcher.process(workflow.id)

        assert result["status"] == "success"
        assert result["source"] == "llm"
        assert len(result["options"]) == 1
        assert "RESIZE" in result["selected_option"].forward_sql

    def test_validation_disabled_skips_check(self, agent_context, workflow, mock_llm):
        """When validate_sql=False, all options pass through unchecked."""
        researcher = ResearcherAgent(agent_context, llm_provider=mock_llm)
        researcher._rag._config.validate_sql = False

        mock_llm.generate_with_tools.side_effect = Exception("fail")
        mock_llm.generate.return_value = json.dumps(
            [
                {
                    "title": "Any SQL",
                    "forward_sql": "ALTER TABLESPACE USERS ADD DATAFILE SIZE 10G",
                    "rollback_sql": "N/A",
                    "confidence": 0.8,
                    "risk_level": "LOW",
                    "reasoning": "test",
                }
            ]
        )

        result = researcher.process(workflow.id)
        assert result["status"] == "success"
        assert result["source"] == "llm"
        assert len(result["options"]) == 1

    def test_all_options_invalid_falls_to_template(self, agent_context, workflow, mock_llm):
        """When all LLM options fail validation, fall back to template."""
        from sentri.rag.manager import RuleViolation, ValidationResult

        researcher = ResearcherAgent(agent_context, llm_provider=mock_llm)
        researcher._rag.validate_sql = MagicMock(
            return_value=ValidationResult(
                is_valid=False,
                violations=[
                    RuleViolation(
                        rule_id="test_rule",
                        severity="CRITICAL",
                        message="Test violation",
                        sql_fragment="ADD DATAFILE",
                        suggested_fix="Use RESIZE",
                    )
                ],
                checked_rules=1,
            )
        )

        mock_llm.generate_with_tools.side_effect = Exception("fail")
        mock_llm.generate.return_value = json.dumps(
            [
                {
                    "title": "Bad SQL",
                    "forward_sql": "ALTER TABLESPACE USERS ADD DATAFILE SIZE 10G",
                    "rollback_sql": "N/A",
                    "confidence": 0.9,
                    "risk_level": "LOW",
                    "reasoning": "Will be rejected",
                }
            ]
        )

        result = researcher.process(workflow.id)

        # All LLM options rejected → template fallback
        assert result["status"] == "success"
        assert result["source"] == "template"

    def test_mixed_valid_and_invalid_options(self, agent_context, workflow, mock_llm):
        """Only invalid options are dropped; valid ones remain."""
        from sentri.rag.manager import RuleViolation, ValidationResult

        researcher = ResearcherAgent(agent_context, llm_provider=mock_llm)
        # First call (bad SQL) → violation; second call (good SQL) → valid
        researcher._rag.validate_sql = MagicMock(
            side_effect=[
                ValidationResult(
                    is_valid=False,
                    violations=[
                        RuleViolation(
                            rule_id="test_rule",
                            severity="CRITICAL",
                            message="Bad",
                            sql_fragment="ADD DATAFILE",
                            suggested_fix="Use RESIZE",
                        )
                    ],
                    checked_rules=1,
                ),
                ValidationResult(is_valid=True, violations=[], checked_rules=1),
            ]
        )

        mock_llm.generate_with_tools.side_effect = Exception("fail")
        mock_llm.generate.return_value = json.dumps(
            [
                {
                    "title": "Bad option",
                    "forward_sql": "ALTER TABLESPACE USERS ADD DATAFILE SIZE 10G",
                    "rollback_sql": "N/A",
                    "confidence": 0.9,
                    "risk_level": "LOW",
                    "reasoning": "bad",
                },
                {
                    "title": "Good option",
                    "forward_sql": "ALTER TABLESPACE USERS RESIZE 20G",
                    "rollback_sql": "N/A",
                    "confidence": 0.8,
                    "risk_level": "LOW",
                    "reasoning": "good",
                },
            ]
        )

        result = researcher.process(workflow.id)

        assert result["status"] == "success"
        assert result["source"] == "llm"
        assert len(result["options"]) == 1
        assert result["options"][0].title == "Good option"

    def test_validation_error_keeps_option(self, agent_context, workflow, mock_llm):
        """If validator throws an exception, keep the option (fail-open)."""
        researcher = ResearcherAgent(agent_context, llm_provider=mock_llm)
        researcher._rag.validate_sql = MagicMock(side_effect=Exception("Validator crash"))

        mock_llm.generate_with_tools.side_effect = Exception("fail")
        mock_llm.generate.return_value = json.dumps(
            [
                {
                    "title": "Keep me",
                    "forward_sql": "SELECT 1 FROM dual",
                    "rollback_sql": "N/A",
                    "confidence": 0.85,
                    "risk_level": "LOW",
                    "reasoning": "test",
                }
            ]
        )

        result = researcher.process(workflow.id)

        assert result["status"] == "success"
        assert result["source"] == "llm"
        assert len(result["options"]) == 1
        assert result["options"][0].title == "Keep me"
