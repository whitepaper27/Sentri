"""Tests for v2.1 tool-calling dataclasses and LLMProvider base class."""

from sentri.core.llm_interface import (
    GenerateWithToolsResponse,
    NoOpLLMProvider,
    ToolCall,
    ToolDefinition,
    ToolResult,
)

# ---------------------------------------------------------------------------
# ToolDefinition
# ---------------------------------------------------------------------------


class TestToolDefinition:
    def test_creation(self):
        td = ToolDefinition(
            name="get_tablespace_info",
            description="Get tablespace details",
            parameters={"type": "object", "properties": {"db": {"type": "string"}}},
        )
        assert td.name == "get_tablespace_info"
        assert td.description == "Get tablespace details"
        assert td.parameters["type"] == "object"

    def test_parameters_schema(self):
        td = ToolDefinition(
            name="query_database",
            description="Run SQL",
            parameters={
                "type": "object",
                "properties": {
                    "database_id": {"type": "string"},
                    "sql": {"type": "string"},
                },
                "required": ["database_id", "sql"],
            },
        )
        assert "required" in td.parameters
        assert "database_id" in td.parameters["required"]


# ---------------------------------------------------------------------------
# ToolCall
# ---------------------------------------------------------------------------


class TestToolCall:
    def test_creation(self):
        tc = ToolCall(
            tool_call_id="call_123",
            name="get_tablespace_info",
            arguments={"database_id": "dev-01", "tablespace_name": "USERS"},
        )
        assert tc.tool_call_id == "call_123"
        assert tc.name == "get_tablespace_info"
        assert tc.arguments["tablespace_name"] == "USERS"


# ---------------------------------------------------------------------------
# ToolResult
# ---------------------------------------------------------------------------


class TestToolResult:
    def test_success_result(self):
        tr = ToolResult(
            tool_call_id="call_123",
            name="get_tablespace_info",
            content='{"bigfile": "YES"}',
        )
        assert tr.is_error is False
        assert "bigfile" in tr.content

    def test_error_result(self):
        tr = ToolResult(
            tool_call_id="call_456",
            name="query_database",
            content='{"error": "ORA-00942"}',
            is_error=True,
        )
        assert tr.is_error is True


# ---------------------------------------------------------------------------
# GenerateWithToolsResponse
# ---------------------------------------------------------------------------


class TestGenerateWithToolsResponse:
    def test_final_text_response(self):
        resp = GenerateWithToolsResponse(
            text='[{"title": "Resize datafile"}]',
            tool_calls=[],
            is_final=True,
        )
        assert resp.is_final is True
        assert resp.text.startswith("[")
        assert len(resp.tool_calls) == 0

    def test_tool_call_response(self):
        resp = GenerateWithToolsResponse(
            text="Let me check the tablespace.",
            tool_calls=[
                ToolCall("c1", "get_tablespace_info", {"database_id": "dev"}),
            ],
            is_final=False,
        )
        assert resp.is_final is False
        assert len(resp.tool_calls) == 1
        assert resp.tool_calls[0].name == "get_tablespace_info"

    def test_defaults(self):
        resp = GenerateWithToolsResponse()
        assert resp.text == ""
        assert resp.tool_calls == []
        assert resp.is_final is True


# ---------------------------------------------------------------------------
# NoOpLLMProvider — generate_with_tools fallback
# ---------------------------------------------------------------------------


class TestNoOpToolSupport:
    def test_generate_with_tools_returns_empty(self):
        noop = NoOpLLMProvider()
        resp = noop.generate_with_tools(
            messages=[{"role": "user", "content": "Fix tablespace"}],
            tools=[ToolDefinition("test", "test tool", {"type": "object"})],
        )
        assert resp.is_final is True
        assert resp.text == ""
        assert resp.tool_calls == []

    def test_format_tool_results_generic(self):
        noop = NoOpLLMProvider()
        resp = GenerateWithToolsResponse(
            tool_calls=[ToolCall("c1", "test_tool", {"key": "val"})],
            is_final=False,
        )
        results = [ToolResult("c1", "test_tool", '{"data": 1}')]

        msgs = noop.format_tool_results(resp, results)
        assert len(msgs) == 2  # assistant + tool
        assert msgs[0]["role"] == "assistant"
        assert msgs[1]["role"] == "tool"
        assert msgs[1]["content"] == '{"data": 1}'
