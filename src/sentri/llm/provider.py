"""Concrete LLM providers and factory function.

Supports Claude, OpenAI, and Gemini — including tool calling (v2.1).
Falls back to NoOpLLMProvider when no API key is configured.
"""

from __future__ import annotations

import json
import logging
import uuid

from sentri.core.exceptions import LLMError
from sentri.core.llm_interface import (
    GenerateWithToolsResponse,
    LLMProvider,
    NoOpLLMProvider,
    ToolCall,
    ToolDefinition,
    ToolResult,
)

logger = logging.getLogger("sentri.llm.provider")

# Default models per provider
_DEFAULT_MODELS = {
    "claude": "claude-sonnet-4-5-20250929",
    "openai": "gpt-4o",
    "gemini": "gemini-2.0-flash",
}


class ClaudeProvider(LLMProvider):
    """Anthropic Claude API provider."""

    def __init__(self, api_key: str, model: str = ""):
        self._api_key = api_key
        self._model = model or _DEFAULT_MODELS["claude"]
        self._client = None

    def _get_client(self):
        if self._client is None:
            try:
                import anthropic

                self._client = anthropic.Anthropic(api_key=self._api_key)
            except TypeError as e:
                if "proxies" in str(e):
                    raise LLMError(
                        "anthropic/httpx version mismatch — upgrade with: "
                        "pip install 'anthropic>=0.39'"
                    ) from e
                raise
            except ImportError:
                raise LLMError(
                    "anthropic package not installed. Install with: pip install sentri[llm]"
                )
        return self._client

    def generate(
        self,
        prompt: str,
        system_prompt: str = "",
        temperature: float = 0.3,
        max_tokens: int = 2048,
    ) -> str:
        client = self._get_client()
        try:
            kwargs = {
                "model": self._model,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "messages": [{"role": "user", "content": prompt}],
            }
            if system_prompt:
                kwargs["system"] = system_prompt

            response = client.messages.create(**kwargs)
            return response.content[0].text
        except Exception as e:
            raise LLMError(f"Claude API error: {e}") from e

    def generate_with_tools(
        self,
        messages: list[dict],
        tools: list[ToolDefinition],
        system_prompt: str = "",
        temperature: float = 0.3,
        max_tokens: int = 2048,
    ) -> GenerateWithToolsResponse:
        client = self._get_client()
        try:
            # Convert ToolDefinition to Claude's format
            claude_tools = [
                {
                    "name": t.name,
                    "description": t.description,
                    "input_schema": t.parameters,
                }
                for t in tools
            ]

            kwargs = {
                "model": self._model,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "messages": messages,
                "tools": claude_tools,
            }
            if system_prompt:
                kwargs["system"] = system_prompt

            response = client.messages.create(**kwargs)

            # Parse response: Claude returns content blocks
            text_parts = []
            tool_calls = []
            for block in response.content:
                if block.type == "text":
                    text_parts.append(block.text)
                elif block.type == "tool_use":
                    tool_calls.append(
                        ToolCall(
                            tool_call_id=block.id,
                            name=block.name,
                            arguments=block.input,
                        )
                    )

            is_final = response.stop_reason == "end_turn"
            return GenerateWithToolsResponse(
                text="\n".join(text_parts),
                tool_calls=tool_calls,
                is_final=is_final,
            )
        except Exception as e:
            raise LLMError(f"Claude API error: {e}") from e

    def format_tool_results(
        self,
        response: GenerateWithToolsResponse,
        results: list[ToolResult],
    ) -> list[dict]:
        # Claude format: assistant message with content blocks, then user message with tool_result blocks
        assistant_content = []
        if response.text:
            assistant_content.append({"type": "text", "text": response.text})
        for tc in response.tool_calls:
            assistant_content.append(
                {
                    "type": "tool_use",
                    "id": tc.tool_call_id,
                    "name": tc.name,
                    "input": tc.arguments,
                }
            )

        user_content = []
        for result in results:
            user_content.append(
                {
                    "type": "tool_result",
                    "tool_use_id": result.tool_call_id,
                    "content": result.content,
                    "is_error": result.is_error,
                }
            )

        return [
            {"role": "assistant", "content": assistant_content},
            {"role": "user", "content": user_content},
        ]

    def is_available(self) -> bool:
        return bool(self._api_key)

    @property
    def name(self) -> str:
        return "Claude"


class OpenAIProvider(LLMProvider):
    """OpenAI API provider."""

    def __init__(self, api_key: str, model: str = ""):
        self._api_key = api_key
        self._model = model or _DEFAULT_MODELS["openai"]
        self._client = None

    def _get_client(self):
        if self._client is None:
            try:
                import openai

                self._client = openai.OpenAI(api_key=self._api_key)
            except ImportError:
                raise LLMError(
                    "openai package not installed. Install with: pip install sentri[llm]"
                )
        return self._client

    def generate(
        self,
        prompt: str,
        system_prompt: str = "",
        temperature: float = 0.3,
        max_tokens: int = 2048,
    ) -> str:
        client = self._get_client()
        try:
            messages = []
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})
            messages.append({"role": "user", "content": prompt})

            response = client.chat.completions.create(
                model=self._model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            return response.choices[0].message.content or ""
        except Exception as e:
            raise LLMError(f"OpenAI API error: {e}") from e

    def generate_with_tools(
        self,
        messages: list[dict],
        tools: list[ToolDefinition],
        system_prompt: str = "",
        temperature: float = 0.3,
        max_tokens: int = 2048,
    ) -> GenerateWithToolsResponse:
        client = self._get_client()
        try:
            # Convert ToolDefinition to OpenAI's format
            oai_tools = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.parameters,
                    },
                }
                for t in tools
            ]

            all_messages = []
            if system_prompt:
                all_messages.append({"role": "system", "content": system_prompt})
            all_messages.extend(messages)

            response = client.chat.completions.create(
                model=self._model,
                messages=all_messages,
                tools=oai_tools,
                temperature=temperature,
                max_tokens=max_tokens,
            )

            msg = response.choices[0].message
            text = msg.content or ""
            tool_calls = []

            if msg.tool_calls:
                for tc in msg.tool_calls:
                    tool_calls.append(
                        ToolCall(
                            tool_call_id=tc.id,
                            name=tc.function.name,
                            arguments=json.loads(tc.function.arguments),
                        )
                    )

            is_final = response.choices[0].finish_reason == "stop"
            return GenerateWithToolsResponse(
                text=text,
                tool_calls=tool_calls,
                is_final=is_final,
            )
        except Exception as e:
            raise LLMError(f"OpenAI API error: {e}") from e

    def format_tool_results(
        self,
        response: GenerateWithToolsResponse,
        results: list[ToolResult],
    ) -> list[dict]:
        # OpenAI format: assistant message with tool_calls, then role="tool" messages
        assistant_msg = {"role": "assistant", "content": response.text or None}
        if response.tool_calls:
            assistant_msg["tool_calls"] = [
                {
                    "id": tc.tool_call_id,
                    "type": "function",
                    "function": {
                        "name": tc.name,
                        "arguments": json.dumps(tc.arguments),
                    },
                }
                for tc in response.tool_calls
            ]

        msgs = [assistant_msg]
        for result in results:
            msgs.append(
                {
                    "role": "tool",
                    "tool_call_id": result.tool_call_id,
                    "content": result.content,
                }
            )
        return msgs

    def is_available(self) -> bool:
        return bool(self._api_key)

    @property
    def name(self) -> str:
        return "OpenAI"


class GeminiProvider(LLMProvider):
    """Google Gemini API provider."""

    def __init__(self, api_key: str, model: str = ""):
        self._api_key = api_key
        self._model = model or _DEFAULT_MODELS["gemini"]
        self._client = None
        self._genai = None

    def _get_client(self):
        if self._client is None:
            try:
                import google.generativeai as genai

                genai.configure(api_key=self._api_key)
                self._genai = genai
                self._client = genai.GenerativeModel(self._model)
            except ImportError:
                raise LLMError(
                    "google-generativeai package not installed. "
                    "Install with: pip install sentri[llm]"
                )
        return self._client

    def generate(
        self,
        prompt: str,
        system_prompt: str = "",
        temperature: float = 0.3,
        max_tokens: int = 2048,
    ) -> str:
        client = self._get_client()
        try:
            full_prompt = prompt
            if system_prompt:
                full_prompt = f"{system_prompt}\n\n---\n\n{prompt}"

            response = client.generate_content(
                full_prompt,
                generation_config={
                    "temperature": temperature,
                    "max_output_tokens": max_tokens,
                },
            )
            return response.text or ""
        except Exception as e:
            raise LLMError(f"Gemini API error: {e}") from e

    def generate_with_tools(
        self,
        messages: list[dict],
        tools: list[ToolDefinition],
        system_prompt: str = "",
        temperature: float = 0.3,
        max_tokens: int = 2048,
    ) -> GenerateWithToolsResponse:
        # Ensure genai.configure(api_key=...) has been called
        self._get_client()

        try:
            import google.generativeai as genai
        except ImportError:
            raise LLMError("google-generativeai package not installed.")

        try:
            # Convert ToolDefinition to Gemini's format
            function_declarations = []
            for t in tools:
                function_declarations.append(
                    genai.protos.FunctionDeclaration(
                        name=t.name,
                        description=t.description,
                        parameters=_json_schema_to_gemini_schema(t.parameters),
                    )
                )

            gemini_tools = [genai.protos.Tool(function_declarations=function_declarations)]

            # Create model with system instruction and tools
            model = genai.GenerativeModel(
                self._model,
                system_instruction=system_prompt if system_prompt else None,
                tools=gemini_tools,
            )

            # Convert messages to Gemini format
            gemini_history = _messages_to_gemini_history(messages[:-1], genai)
            last_msg = messages[-1] if messages else {"role": "user", "content": ""}

            chat = model.start_chat(history=gemini_history)

            # Send the last message
            last_content = last_msg.get("content", "")
            if isinstance(last_content, list):
                # Tool result parts
                parts = []
                for item in last_content:
                    if isinstance(item, dict) and item.get("function_response"):
                        parts.append(
                            genai.protos.Part(
                                function_response=genai.protos.FunctionResponse(
                                    name=item["function_response"]["name"],
                                    response=item["function_response"]["response"],
                                )
                            )
                        )
                    else:
                        parts.append(genai.protos.Part(text=str(item)))
                response = chat.send_message(
                    parts,
                    generation_config={
                        "temperature": temperature,
                        "max_output_tokens": max_tokens,
                    },
                )
            else:
                response = chat.send_message(
                    str(last_content),
                    generation_config={
                        "temperature": temperature,
                        "max_output_tokens": max_tokens,
                    },
                )

            # Parse response
            text_parts = []
            tool_calls = []

            for part in response.parts:
                if part.text:
                    text_parts.append(part.text)
                if part.function_call:
                    fc = part.function_call
                    tool_calls.append(
                        ToolCall(
                            tool_call_id=str(uuid.uuid4()),
                            name=fc.name,
                            arguments=dict(fc.args),
                        )
                    )

            is_final = len(tool_calls) == 0
            return GenerateWithToolsResponse(
                text="\n".join(text_parts),
                tool_calls=tool_calls,
                is_final=is_final,
            )
        except Exception as e:
            raise LLMError(f"Gemini API error: {e}") from e

    def format_tool_results(
        self,
        response: GenerateWithToolsResponse,
        results: list[ToolResult],
    ) -> list[dict]:
        # Gemini format: model message with function_call, then user message with function_response
        model_parts = []
        if response.text:
            model_parts.append({"text": response.text})
        for tc in response.tool_calls:
            model_parts.append({"function_call": {"name": tc.name, "args": tc.arguments}})

        user_parts = []
        for result in results:
            try:
                response_data = json.loads(result.content)
            except json.JSONDecodeError:
                response_data = {"result": result.content}
            user_parts.append(
                {
                    "function_response": {
                        "name": result.name,
                        "response": response_data,
                    }
                }
            )

        return [
            {"role": "model", "content": model_parts},
            {"role": "user", "content": user_parts},
        ]

    def is_available(self) -> bool:
        return bool(self._api_key)

    @property
    def name(self) -> str:
        return "Gemini"


def _json_schema_to_gemini_schema(schema: dict) -> dict:
    """Convert JSON Schema to Gemini's Schema proto format."""
    import google.generativeai as genai

    type_map = {
        "string": genai.protos.Type.STRING,
        "number": genai.protos.Type.NUMBER,
        "integer": genai.protos.Type.INTEGER,
        "boolean": genai.protos.Type.BOOLEAN,
        "array": genai.protos.Type.ARRAY,
        "object": genai.protos.Type.OBJECT,
    }

    result = {}
    result["type_"] = type_map.get(schema.get("type", "object"), genai.protos.Type.OBJECT)

    if "properties" in schema:
        result["properties"] = {}
        for name, prop in schema["properties"].items():
            prop_schema = {
                "type_": type_map.get(prop.get("type", "string"), genai.protos.Type.STRING)
            }
            if "description" in prop:
                prop_schema["description"] = prop["description"]
            if prop.get("type") == "array" and "items" in prop:
                item_type = prop["items"].get("type", "string")
                prop_schema["items"] = genai.protos.Schema(
                    type_=type_map.get(item_type, genai.protos.Type.STRING)
                )
            result["properties"][name] = genai.protos.Schema(**prop_schema)

    if "required" in schema:
        result["required"] = schema["required"]

    return genai.protos.Schema(**result)


def _messages_to_gemini_history(messages: list[dict], genai) -> list:
    """Convert message dicts to Gemini Content objects for chat history."""
    history = []
    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")

        # Map roles
        gemini_role = "model" if role in ("assistant", "model") else "user"

        if isinstance(content, str):
            history.append(
                genai.protos.Content(
                    role=gemini_role,
                    parts=[genai.protos.Part(text=content)],
                )
            )
        elif isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, dict):
                    if "text" in item:
                        parts.append(genai.protos.Part(text=item["text"]))
                    elif "function_call" in item:
                        fc = item["function_call"]
                        parts.append(
                            genai.protos.Part(
                                function_call=genai.protos.FunctionCall(
                                    name=fc["name"], args=fc["args"]
                                )
                            )
                        )
                    elif "function_response" in item:
                        fr = item["function_response"]
                        parts.append(
                            genai.protos.Part(
                                function_response=genai.protos.FunctionResponse(
                                    name=fr["name"], response=fr["response"]
                                )
                            )
                        )
                else:
                    parts.append(genai.protos.Part(text=str(item)))
            if parts:
                history.append(genai.protos.Content(role=gemini_role, parts=parts))
    return history


def create_llm_provider(
    provider_name: str,
    api_key: str,
    model: str = "",
) -> LLMProvider:
    """Factory function: create an LLM provider by name.

    Returns NoOpLLMProvider if provider_name is empty or api_key is missing.
    """
    if not provider_name or not api_key:
        logger.info("No LLM provider configured — using template fallback")
        return NoOpLLMProvider()

    provider_name = provider_name.lower().strip()

    if provider_name == "claude":
        return ClaudeProvider(api_key, model)
    elif provider_name == "openai":
        return OpenAIProvider(api_key, model)
    elif provider_name == "gemini":
        return GeminiProvider(api_key, model)
    else:
        logger.warning("Unknown LLM provider '%s' — using NoOp", provider_name)
        return NoOpLLMProvider()
