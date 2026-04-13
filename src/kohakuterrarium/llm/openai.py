"""
OpenAI-compatible LLM provider using the OpenAI Python SDK.

Supports OpenAI API and compatible services like OpenRouter, Together AI, etc.
Uses AsyncOpenAI for all API calls (streaming + non-streaming).
"""

from typing import Any, AsyncIterator

from openai import AsyncOpenAI

from kohakuterrarium.llm.base import (
    BaseLLMProvider,
    ChatResponse,
    LLMConfig,
    NativeToolCall,
    ToolSchema,
)
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

# Default API endpoints
OPENAI_BASE_URL = "https://api.openai.com/v1"
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


class OpenAIProvider(BaseLLMProvider):
    """OpenAI API-compatible LLM provider using the official SDK.

    Works with:
    - OpenAI API (default)
    - OpenRouter (set base_url to OPENROUTER_BASE_URL)
    - Any OpenAI-compatible endpoint

    Usage::

        provider = OpenAIProvider(api_key="sk-...", model="gpt-4o")

        # OpenRouter
        provider = OpenAIProvider(
            api_key="sk-or-...",
            base_url=OPENROUTER_BASE_URL,
            model="anthropic/claude-3-opus",
        )

        async for chunk in provider.chat(messages):
            print(chunk, end="")
    """

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "",
        base_url: str = OPENAI_BASE_URL,
        *,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        timeout: float = 120.0,
        extra_headers: dict[str, str] | None = None,
        extra_body: dict[str, Any] | None = None,
        max_retries: int = 3,
    ):
        """Initialize the OpenAI provider.

        Args:
            api_key: API key for authentication
            model: Model identifier
            base_url: API base URL (change for OpenRouter, etc.)
            temperature: Sampling temperature
            max_tokens: Maximum tokens to generate
            timeout: Request timeout in seconds
            extra_headers: Additional headers (e.g., for OpenRouter HTTP-Referer)
            extra_body: Additional fields merged into every API request body
                (e.g., {"reasoning": {"enabled": True}})
            max_retries: Maximum retry attempts for transient errors
        """
        super().__init__(
            LLMConfig(
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
            )
        )

        if not api_key:
            raise ValueError(
                "API key is required. "
                "Set OPENROUTER_API_KEY or OPENAI_API_KEY environment variable."
            )

        self.extra_body = extra_body or {}
        self._last_usage: dict[str, int] = {}
        self.prompt_cache_key: str | None = None

        self._client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
            max_retries=max_retries,
            default_headers=extra_headers or {},
        )

        logger.debug(
            "OpenAIProvider initialized (SDK)",
            model=model,
            base_url=base_url,
        )

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._client.close()

    # ------------------------------------------------------------------
    # Streaming
    # ------------------------------------------------------------------

    async def _stream_chat(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[ToolSchema] | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[str]:
        """Stream chat completion via the OpenAI SDK."""
        self._last_tool_calls = []

        api_tools = [t.to_api_format() for t in tools] if tools else None

        create_kwargs: dict[str, Any] = {
            "model": kwargs.get("model", self.config.model),
            "messages": messages,
            "stream": True,
            "stream_options": {"include_usage": True},
        }

        # Optional parameters
        temp = kwargs.get("temperature", self.config.temperature)
        if temp is not None:
            create_kwargs["temperature"] = temp

        max_tok = kwargs.get("max_tokens", self.config.max_tokens)
        if max_tok is not None:
            create_kwargs["max_tokens"] = max_tok

        if "top_p" in kwargs:
            create_kwargs["top_p"] = kwargs["top_p"]
        if "stop" in kwargs:
            create_kwargs["stop"] = kwargs["stop"]
        if api_tools:
            create_kwargs["tools"] = api_tools

        # extra_body: merged into the request body by the SDK
        merged_extra = {**self.extra_body}
        if "extra_body" in kwargs:
            merged_extra.update(kwargs["extra_body"])
        if merged_extra:
            create_kwargs["extra_body"] = merged_extra

        # Prompt cache key: first-class SDK parameter for routing stickiness
        if self.prompt_cache_key:
            create_kwargs["prompt_cache_key"] = self.prompt_cache_key

        logger.debug("Starting streaming request", model=create_kwargs["model"])

        self._last_usage = {}
        pending_calls: dict[int, dict[str, str]] = {}

        stream = await self._client.chat.completions.create(**create_kwargs)

        async for chunk in stream:
            # Usage (usually in the final chunk)
            if chunk.usage:
                cached = 0
                cache_write = 0
                details = getattr(chunk.usage, "prompt_tokens_details", None)
                if details:
                    cached = getattr(details, "cached_tokens", 0) or 0
                    cache_write = getattr(details, "cache_write_tokens", 0) or 0
                self._last_usage = {
                    "prompt_tokens": chunk.usage.prompt_tokens or 0,
                    "completion_tokens": chunk.usage.completion_tokens or 0,
                    "total_tokens": chunk.usage.total_tokens or 0,
                    "cached_tokens": cached,
                    "cache_write_tokens": cache_write,
                }

            if not chunk.choices:
                continue

            delta = chunk.choices[0].delta

            # Accumulate native tool call deltas
            if delta.tool_calls:
                for tc_delta in delta.tool_calls:
                    idx = tc_delta.index
                    if idx not in pending_calls:
                        pending_calls[idx] = {"id": "", "name": "", "arguments": ""}
                    if tc_delta.id:
                        pending_calls[idx]["id"] = tc_delta.id
                    if tc_delta.function:
                        if tc_delta.function.name:
                            pending_calls[idx]["name"] = tc_delta.function.name
                        if tc_delta.function.arguments:
                            pending_calls[idx][
                                "arguments"
                            ] += tc_delta.function.arguments

            # Yield text content
            if delta.content:
                yield delta.content

        # Finalize tool calls
        if pending_calls:
            self._last_tool_calls = [
                NativeToolCall(
                    id=call["id"],
                    name=call["name"],
                    arguments=call["arguments"],
                )
                for _, call in sorted(pending_calls.items())
            ]
            logger.debug(
                "Native tool calls received",
                count=len(self._last_tool_calls),
                tools=[tc.name for tc in self._last_tool_calls],
            )

        if self._last_usage:
            logger.info(
                "Token usage",
                prompt_tokens=self._last_usage.get("prompt_tokens", 0),
                completion_tokens=self._last_usage.get("completion_tokens", 0),
            )

    # ------------------------------------------------------------------
    # Non-streaming
    # ------------------------------------------------------------------

    async def _complete_chat(
        self,
        messages: list[dict[str, Any]],
        **kwargs: Any,
    ) -> ChatResponse:
        """Non-streaming chat completion via the OpenAI SDK."""
        self._last_tool_calls = []

        create_kwargs: dict[str, Any] = {
            "model": kwargs.get("model", self.config.model),
            "messages": messages,
        }

        temp = kwargs.get("temperature", self.config.temperature)
        if temp is not None:
            create_kwargs["temperature"] = temp

        max_tok = kwargs.get("max_tokens", self.config.max_tokens)
        if max_tok is not None:
            create_kwargs["max_tokens"] = max_tok

        merged_extra = {**self.extra_body}
        if "extra_body" in kwargs:
            merged_extra.update(kwargs["extra_body"])
        if merged_extra:
            create_kwargs["extra_body"] = merged_extra

        if self.prompt_cache_key:
            create_kwargs["prompt_cache_key"] = self.prompt_cache_key

        logger.debug("Starting non-streaming request", model=create_kwargs["model"])

        response = await self._client.chat.completions.create(**create_kwargs)

        choice = response.choices[0]
        message = choice.message

        # Extract native tool calls
        if message.tool_calls:
            self._last_tool_calls = [
                NativeToolCall(
                    id=tc.id,
                    name=tc.function.name,
                    arguments=tc.function.arguments,
                )
                for tc in message.tool_calls
            ]
            logger.debug(
                "Native tool calls received (non-streaming)",
                count=len(self._last_tool_calls),
                tools=[tc.name for tc in self._last_tool_calls],
            )

        if response.usage:
            cached = 0
            cache_write = 0
            details = getattr(response.usage, "prompt_tokens_details", None)
            if details:
                cached = getattr(details, "cached_tokens", 0) or 0
                cache_write = getattr(details, "cache_write_tokens", 0) or 0
            self._last_usage = {
                "prompt_tokens": response.usage.prompt_tokens or 0,
                "completion_tokens": response.usage.completion_tokens or 0,
                "total_tokens": response.usage.total_tokens or 0,
                "cached_tokens": cached,
                "cache_write_tokens": cache_write,
            }
            logger.debug(
                "Request completed",
                tokens_in=self._last_usage.get("prompt_tokens"),
                tokens_out=self._last_usage.get("completion_tokens"),
            )

        return ChatResponse(
            content=message.content or "",
            finish_reason=choice.finish_reason or "unknown",
            usage=self._last_usage,
            model=response.model,
        )

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    async def __aenter__(self) -> "OpenAIProvider":
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close()
