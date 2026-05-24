"""LLM client wrapper - encapsulates OpenAI API calls.

Supports OpenAI-compatible APIs (OpenAI, Azure OpenAI, Claude via proxy, etc.)
using the openai Python SDK.
"""

import logging
from collections.abc import AsyncGenerator
from typing import Any

from openai import AsyncOpenAI

logger = logging.getLogger(__name__)


class LLMClient:
    """Encapsulates OpenAI-compatible LLM API calls."""

    def __init__(
        self,
        api_key: str,
        model: str = "gpt-4o",
        base_url: str | None = None,
    ) -> None:
        """Initialize the LLM client.

        Args:
            api_key: API key for the LLM service.
            model: Model identifier (e.g., "gpt-4o", "claude-3-opus-20240229").
            base_url: Optional base URL for the API (for proxies/compatible services).
        """
        self._model = model
        self._client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
        )
        logger.info("LLM client initialized: model=%s, base_url=%s", model, base_url)

    @property
    def model(self) -> str:
        """Current model identifier."""
        return self._model

    async def chat_completion(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        *,
        tool_choice: str = "auto",
    ) -> dict[str, Any]:
        """Call the LLM API (non-streaming).

        Args:
            messages: List of message dicts (role, content, tool_calls, etc.).
            tools: Optional list of tool definitions (OpenAI function calling format).
            tool_choice: Tool choice strategy ("auto", "none", or specific tool).

        Returns:
            The complete response dict from the API.
        """
        kwargs: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = tool_choice

        response = await self._client.chat.completions.create(**kwargs)

        # Convert to dict for easier handling
        choice = response.choices[0]
        message = choice.message

        result: dict[str, Any] = {
            "role": message.role,
            "content": message.content,
            "finish_reason": choice.finish_reason,
        }

        if message.tool_calls:
            result["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": tc.type,
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
                for tc in message.tool_calls
            ]

        return result

    async def chat_completion_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        *,
        tool_choice: str = "auto",
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Stream the LLM API response.

        Yields incremental content deltas for the assistant's text response.
        When tool_calls are present, they are accumulated and yielded as a
        single complete message once the stream ends.

        Args:
            messages: List of message dicts.
            tools: Optional list of tool definitions.
            tool_choice: Tool choice strategy.

        Yields:
            Deltas with either {"type": "content", "text": ...} for text
            or {"type": "tool_calls_complete", "tool_calls": [...]} when done.
        """
        kwargs: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "stream": True,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = tool_choice

        stream = await self._client.chat.completions.create(**kwargs)

        # Accumulate tool calls across stream chunks
        tool_calls_acc: dict[int, dict[str, Any]] = {}
        finish_reason = None

        async for chunk in stream:
            if not chunk.choices:
                continue

            delta = chunk.choices[0].delta
            finish_reason = chunk.choices[0].finish_reason

            # Yield text content deltas
            if delta.content:
                yield {"type": "content", "text": delta.content}

            # Accumulate tool call deltas
            if delta.tool_calls:
                for tc_delta in delta.tool_calls:
                    idx = tc_delta.index
                    if idx not in tool_calls_acc:
                        tool_calls_acc[idx] = {
                            "id": tc_delta.id or "",
                            "type": tc_delta.type or "function",
                            "function": {"name": "", "arguments": ""},
                        }
                    if tc_delta.id:
                        tool_calls_acc[idx]["id"] = tc_delta.id
                    if tc_delta.function:
                        if tc_delta.function.name:
                            tool_calls_acc[idx]["function"]["name"] += tc_delta.function.name
                        if tc_delta.function.arguments:
                            tool_calls_acc[idx]["function"]["arguments"] += tc_delta.function.arguments

        # If tool calls were accumulated, yield them as a complete message
        if tool_calls_acc:
            sorted_tool_calls = [tool_calls_acc[i] for i in sorted(tool_calls_acc.keys())]
            yield {"type": "tool_calls_complete", "tool_calls": sorted_tool_calls, "finish_reason": finish_reason}
