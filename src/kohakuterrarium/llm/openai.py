"""
OpenAI-compatible LLM provider using the OpenAI Python SDK.

Supports OpenAI API and compatible services like OpenRouter, Together AI, etc.
Uses AsyncOpenAI for all API calls (streaming + non-streaming).
"""

import asyncio
from typing import Any, AsyncIterator

from openai import AsyncOpenAI

from kohakuterrarium.llm.anthropic_cache import (
    apply_anthropic_cache_markers,
    is_anthropic_endpoint,
)
from kohakuterrarium.llm.base import (
    BaseLLMProvider,
    ChatResponse,
    LLMConfig,
    ToolSchema,
)
from kohakuterrarium.llm.openai_helpers import (
    delta_field,
    extract_usage,
    log_token_usage,
    pack_reasoning_fields,
    tool_call_from_pending,
    tool_calls_from_message,
)
from kohakuterrarium.llm.openai_sanitize import log_request_shape, strip_kt_extras
from kohakuterrarium.llm.recovery import (
    ErrorClass,
    RetryPolicy,
    backoff_delay,
    classify_openai_error,
    drop_last_tool_round,
)
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

_delta_field = delta_field
_pack_reasoning_fields = pack_reasoning_fields

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
        echo_reasoning: bool = True,
        retry_policy: RetryPolicy | dict[str, Any] | None = None,
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
            echo_reasoning: When ``True`` (default) capture provider-
                emitted reasoning fields (``reasoning_content``,
                ``reasoning_details``, ``reasoning``) and echo them back
                on the next turn via :attr:`last_assistant_extra_fields`.
                Required for stateful-chain reasoning on DeepSeek V4,
                MiMo V2.5 (OpenRouter), Qwen, Grok, and similar. Turn
                off for providers that 400 on unknown fields (e.g.
                older DeepSeek V3) — the agent stores nothing.
        """
        super().__init__(
            LLMConfig(
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
                retry_policy=retry_policy,
            )
        )

        self.extra_body = extra_body or {}
        self.echo_reasoning = bool(echo_reasoning)
        self._retry_policy = RetryPolicy.from_value(retry_policy)
        self._api_key = api_key
        self._base_url_input = base_url
        self._timeout = timeout
        self._extra_headers = extra_headers or {}
        self._max_retries = max_retries
        self._last_usage: dict[str, int] = {}
        self._last_assistant_extra_fields: dict[str, Any] = {}
        self.prompt_cache_key: str | None = None
        # Retained so :mod:`anthropic_cache` can sniff whether caching
        # applies — the SDK client stores a trailing-slash-normalised URL
        # which is fine for ``"anthropic.com" in ...`` matching.
        self.base_url: str = base_url or ""

        if not api_key:
            raise ValueError(
                "API key is required. "
                "Set OPENROUTER_API_KEY or OPENAI_API_KEY environment variable."
            )

        self._client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
            max_retries=max_retries,
            default_headers=extra_headers or {},
        )

        # Log whether auto-caching will be engaged for this provider. One
        # line per construction (typically once per agent / model switch)
        # is enough — the actual per-turn caching path stays silent.
        anthropic = is_anthropic_endpoint(self.base_url, None)
        disabled = bool(self.extra_body.get("disable_prompt_caching"))
        if anthropic and not disabled:
            logger.info("Anthropic prompt caching auto-enabled", base_url=self.base_url)
        elif anthropic and disabled:
            logger.info(
                "Anthropic prompt caching disabled via extra_body flag",
                base_url=self.base_url,
            )

        logger.debug(
            "OpenAIProvider initialized (SDK)",
            model=model,
            base_url=base_url,
        )

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._client.close()

    def with_model(self, name: str) -> "OpenAIProvider":
        """Return a sibling provider using the same SDK client."""
        if not name or name == self.config.model:
            return self
        clone = object.__new__(OpenAIProvider)
        BaseLLMProvider.__init__(
            clone,
            LLMConfig(
                model=name,
                temperature=self.config.temperature,
                max_tokens=self.config.max_tokens,
                retry_policy=self._retry_policy,
            ),
        )
        clone.extra_body = dict(self.extra_body)
        clone.echo_reasoning = self.echo_reasoning
        clone._retry_policy = self._retry_policy
        clone._api_key = self._api_key
        clone._base_url_input = self._base_url_input
        clone._timeout = self._timeout
        clone._extra_headers = dict(self._extra_headers)
        clone._max_retries = self._max_retries
        clone._last_usage = {}
        clone._last_tool_calls = []
        clone._last_assistant_extra_fields = {}
        clone._emergency_drop_callbacks = list(self._emergency_drop_callbacks)
        clone.prompt_cache_key = self.prompt_cache_key
        clone.base_url = self.base_url
        clone._client = self._client
        clone.provider_name = getattr(self, "provider_name", clone.provider_name)
        clone.provider_native_tools = getattr(
            self, "provider_native_tools", clone.provider_native_tools
        )
        if hasattr(self, "_profile_max_context"):
            clone._profile_max_context = self._profile_max_context
        return clone

    # ------------------------------------------------------------------
    # Streaming
    # ------------------------------------------------------------------

    def _prepare_messages(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Sanitize content parts, then apply Anthropic cache markers.

        Step 1: strip KT-internal fields (e.g. ``ImagePart.meta``
        carrying chat-panel badge metadata) from content parts. Strict
        OpenAI-compatible providers — vLLM-hosted vision models, SGLang,
        MiMo, and similar — drop or ignore content parts with unknown
        top-level keys, producing the failure mode "the model says it
        sees no image." OpenAI proper tolerates the extras but every
        custom OpenAI-compat backend is its own parser. See
        :func:`strip_kt_extras`.

        Step 2: for Anthropic endpoints (and unless the user opts out
        via ``disable_prompt_caching``), tag system + the last three
        non-tool messages with cache_control markers.
        """
        messages = strip_kt_extras(messages)
        if not is_anthropic_endpoint(self.base_url, None):
            return messages
        if self.extra_body.get("disable_prompt_caching"):
            return messages
        return apply_anthropic_cache_markers(messages)

    def _sanitize_extra_body(self, extra: dict[str, Any]) -> dict[str, Any]:
        """Strip KT-internal knobs before sending to the provider.

        ``disable_prompt_caching`` is a KohakuTerrarium-level flag (user
        opt-out). Anthropic would reject it as an unknown field, and
        other providers would pass it through verbatim into logs. Drop
        it here — the caching branch already read it.
        """
        if "disable_prompt_caching" not in extra:
            return extra
        cleaned = {k: v for k, v in extra.items() if k != "disable_prompt_caching"}
        return cleaned

    async def _stream_chat(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[ToolSchema] | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[str]:
        """Stream chat completion with KT-side retry and overflow recovery."""
        current = messages
        attempt = 0
        overflow_recovered = False
        while True:
            try:
                async for chunk in self._raw_stream_chat(
                    current, tools=tools, **kwargs
                ):
                    yield chunk
                return
            except Exception as exc:
                cls = classify_openai_error(exc)
                if cls is ErrorClass.OVERFLOW and not overflow_recovered:
                    dropped, recovered = drop_last_tool_round(current)
                    if dropped:
                        overflow_recovered = True
                        current = recovered
                        self._notify_emergency_drop(recovered)
                        logger.warning(
                            "provider_emergency_drop",
                            dropped=dropped,
                            recovered_messages=len(recovered),
                        )
                        continue
                if (
                    cls in self._retry_policy.retry_classes
                    and attempt < self._retry_policy.max_retries
                ):
                    attempt += 1
                    delay = backoff_delay(attempt, self._retry_policy)
                    logger.warning(
                        "provider_retry",
                        attempt=attempt,
                        error_class=cls.value,
                        delay=delay,
                        error=str(exc),
                    )
                    await asyncio.sleep(delay)
                    continue
                raise

    async def _raw_stream_chat(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[ToolSchema] | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[str]:
        """Stream chat completion via the OpenAI SDK."""
        self._last_tool_calls = []
        self._last_assistant_extra_fields = {}

        api_tools = [t.to_api_format() for t in tools] if tools else None

        create_kwargs: dict[str, Any] = {
            "model": kwargs.get("model", self.config.model),
            "messages": self._prepare_messages(messages),
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
        merged_extra = self._sanitize_extra_body(merged_extra)
        if merged_extra:
            create_kwargs["extra_body"] = merged_extra

        # Prompt cache key: first-class SDK parameter for routing stickiness
        if self.prompt_cache_key:
            create_kwargs["prompt_cache_key"] = self.prompt_cache_key

        log_request_shape(
            "Starting streaming request",
            create_kwargs["model"],
            create_kwargs["messages"],
        )

        self._last_usage = {}
        pending_calls: dict[int, dict[str, str]] = {}
        reasoning_text = ""
        reasoning_details: list[Any] = []
        reasoning_extra: dict[str, Any] = {}

        stream = await self._client.chat.completions.create(**create_kwargs)

        async for chunk in stream:
            # Usage (usually in the final chunk)
            if chunk.usage:
                self._last_usage = extract_usage(chunk.usage)

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

            # Capture provider-specific reasoning deltas when enabled.
            # These aren't on the typed OpenAI SDK delta surface; the
            # SDK exposes unknown response fields via ``model_extra``.
            if self.echo_reasoning:
                rc_piece = delta_field(delta, "reasoning_content")
                if isinstance(rc_piece, str):
                    reasoning_text += rc_piece
                rd_piece = delta_field(delta, "reasoning_details")
                if isinstance(rd_piece, list) and rd_piece:
                    reasoning_details.extend(rd_piece)
                # OpenRouter also occasionally emits a plain "reasoning"
                # string alongside ``reasoning_details`` — keep the last
                # value; the details array is the canonical source.
                r_piece = delta_field(delta, "reasoning")
                if isinstance(r_piece, str) and r_piece:
                    reasoning_extra["reasoning"] = (
                        reasoning_extra.get("reasoning", "") + r_piece
                    )

            # Yield text content (sanitize surrogates from LLM output)
            if delta.content:
                # Remove surrogate characters that some APIs emit
                # which cause UnicodeEncodeError when encoded to UTF-8
                clean = delta.content.encode('utf-8', errors='ignore').decode('utf-8')
                yield clean

        # Finalize tool calls
        if pending_calls:
            self._last_tool_calls = [
                tool_call_from_pending(call)
                for _, call in sorted(pending_calls.items())
            ]
            logger.debug(
                "Native tool calls received",
                count=len(self._last_tool_calls),
                tools=[tc.name for tc in self._last_tool_calls],
            )

        if reasoning_text or reasoning_details or reasoning_extra:
            self._last_assistant_extra_fields = pack_reasoning_fields(
                reasoning_text, reasoning_details, reasoning_extra
            )
            logger.debug(
                "Reasoning fields captured",
                has_content=bool(reasoning_text),
                details_count=len(reasoning_details),
            )

        log_token_usage(self._last_usage)

    # ------------------------------------------------------------------
    # Non-streaming
    # ------------------------------------------------------------------

    async def _complete_chat(
        self,
        messages: list[dict[str, Any]],
        **kwargs: Any,
    ) -> ChatResponse:
        """Non-streaming chat completion with retry and overflow recovery."""
        current = messages
        attempt = 0
        overflow_recovered = False
        while True:
            try:
                return await self._raw_complete_chat(current, **kwargs)
            except Exception as exc:
                cls = classify_openai_error(exc)
                if cls is ErrorClass.OVERFLOW and not overflow_recovered:
                    dropped, recovered = drop_last_tool_round(current)
                    if dropped:
                        overflow_recovered = True
                        current = recovered
                        self._notify_emergency_drop(recovered)
                        logger.warning(
                            "provider_emergency_drop",
                            dropped=dropped,
                            recovered_messages=len(recovered),
                        )
                        continue
                if (
                    cls in self._retry_policy.retry_classes
                    and attempt < self._retry_policy.max_retries
                ):
                    attempt += 1
                    delay = backoff_delay(attempt, self._retry_policy)
                    logger.warning(
                        "provider_retry",
                        attempt=attempt,
                        error_class=cls.value,
                        delay=delay,
                        error=str(exc),
                    )
                    await asyncio.sleep(delay)
                    continue
                raise

    async def _raw_complete_chat(
        self,
        messages: list[dict[str, Any]],
        **kwargs: Any,
    ) -> ChatResponse:
        """Non-streaming chat completion via the OpenAI SDK."""
        self._last_tool_calls = []
        self._last_assistant_extra_fields = {}

        create_kwargs: dict[str, Any] = {
            "model": kwargs.get("model", self.config.model),
            "messages": self._prepare_messages(messages),
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
        merged_extra = self._sanitize_extra_body(merged_extra)
        if merged_extra:
            create_kwargs["extra_body"] = merged_extra

        if self.prompt_cache_key:
            create_kwargs["prompt_cache_key"] = self.prompt_cache_key

        log_request_shape(
            "Starting non-streaming request",
            create_kwargs["model"],
            create_kwargs["messages"],
        )

        response = await self._client.chat.completions.create(**create_kwargs)

        choice = response.choices[0]
        message = choice.message

        # Extract native tool calls
        if message.tool_calls:
            self._last_tool_calls = tool_calls_from_message(message.tool_calls)
            logger.debug(
                "Native tool calls received (non-streaming)",
                count=len(self._last_tool_calls),
                tools=[tc.name for tc in self._last_tool_calls],
            )

        # Capture reasoning fields off the complete assistant message.
        if self.echo_reasoning:
            rc = delta_field(message, "reasoning_content")
            rd = delta_field(message, "reasoning_details")
            r = delta_field(message, "reasoning")
            extras = {}
            if isinstance(rc, str) and rc:
                extras["reasoning_content"] = rc
            if isinstance(rd, list) and rd:
                extras["reasoning_details"] = rd
            if isinstance(r, str) and r:
                extras["reasoning"] = r
            if extras:
                self._last_assistant_extra_fields = extras

        if response.usage:
            self._last_usage = extract_usage(response.usage)
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
