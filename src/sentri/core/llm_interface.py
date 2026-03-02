"""Abstract LLM provider interface for Sentri v2.1.

Provides a base class that concrete providers (Claude, OpenAI, Gemini)
implement.  v2.1 adds tool-calling support so the LLM can investigate
the target database before generating remediation SQL.

Ships with ``NoOpLLMProvider`` so that the system falls back to .md
template behavior when no API key is configured.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

logger = logging.getLogger("sentri.llm")


# ---------------------------------------------------------------------------
# Tool-calling dataclasses (provider-agnostic)
# ---------------------------------------------------------------------------


@dataclass
class ToolDefinition:
    """A tool the LLM can call during agentic research."""

    name: str
    description: str
    parameters: dict  # JSON Schema for the tool's arguments


@dataclass
class ToolCall:
    """A single tool invocation requested by the LLM."""

    tool_call_id: str
    name: str
    arguments: dict


@dataclass
class ToolResult:
    """The result of executing a tool call."""

    tool_call_id: str
    name: str
    content: str  # JSON string
    is_error: bool = False


@dataclass
class GenerateWithToolsResponse:
    """Response from an LLM call that may include tool calls."""

    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    is_final: bool = True  # True = text response, no more tool calls


# ---------------------------------------------------------------------------
# LLM Provider base class
# ---------------------------------------------------------------------------


class LLMProvider(ABC):
    """Base class for all LLM providers."""

    @abstractmethod
    def generate(
        self,
        prompt: str,
        system_prompt: str = "",
        temperature: float = 0.3,
        max_tokens: int = 2048,
    ) -> str:
        """Generate a text completion.

        Args:
            prompt: The user/task prompt.
            system_prompt: Optional system-level instructions.
            temperature: Sampling temperature (0.0 = deterministic).
            max_tokens: Maximum tokens in the response.

        Returns:
            The generated text.
        """

    def generate_with_tools(
        self,
        messages: list[dict],
        tools: list[ToolDefinition],
        system_prompt: str = "",
        temperature: float = 0.3,
        max_tokens: int = 2048,
    ) -> GenerateWithToolsResponse:
        """Generate a response that may include tool calls.

        Default implementation falls back to plain generate() for
        providers that don't support tool calling.

        Args:
            messages: Conversation history as list of dicts.
            tools: Available tool definitions.
            system_prompt: System-level instructions.
            temperature: Sampling temperature.
            max_tokens: Max output tokens.

        Returns:
            Response with text and/or tool calls.
        """
        # Fallback: extract last user message and call plain generate()
        user_msg = ""
        for msg in reversed(messages):
            if msg.get("role") == "user":
                user_msg = msg.get("content", "")
                break
        text = self.generate(user_msg, system_prompt, temperature, max_tokens)
        return GenerateWithToolsResponse(text=text, tool_calls=[], is_final=True)

    def format_tool_results(
        self,
        response: GenerateWithToolsResponse,
        results: list[ToolResult],
    ) -> list[dict]:
        """Format tool results as conversation messages for the next turn.

        Each provider overrides this to match its native message format.
        Default returns a generic format.
        """
        msgs: list[dict] = []
        # Add assistant message with tool calls
        msgs.append(
            {
                "role": "assistant",
                "tool_calls": [
                    {"id": tc.tool_call_id, "name": tc.name, "arguments": tc.arguments}
                    for tc in response.tool_calls
                ],
            }
        )
        # Add tool results
        for result in results:
            msgs.append(
                {
                    "role": "tool",
                    "tool_call_id": result.tool_call_id,
                    "name": result.name,
                    "content": result.content,
                }
            )
        return msgs

    @abstractmethod
    def is_available(self) -> bool:
        """Return True if the provider is configured and reachable."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable provider name (e.g. 'Claude', 'OpenAI')."""


class NoOpLLMProvider(LLMProvider):
    """Stub provider used when no API key is configured.

    Always returns empty strings and reports as unavailable.
    This allows the system to fall back to .md template behavior.
    """

    def generate(
        self,
        prompt: str,
        system_prompt: str = "",
        temperature: float = 0.3,
        max_tokens: int = 2048,
    ) -> str:
        logger.debug("NoOpLLMProvider.generate() called — returning empty string")
        return ""

    def is_available(self) -> bool:
        return False

    @property
    def name(self) -> str:
        return "NoOp"
