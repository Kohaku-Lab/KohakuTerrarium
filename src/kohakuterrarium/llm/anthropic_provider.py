"""Anthropic-compatible Messages API provider using the official SDK.

KohakuTerrarium stores conversation history in an OpenAI-shaped internal
format. This provider is the translation boundary to Anthropic's native
Messages API while preserving Anthropic content blocks for later round-trip.
"""

import asyncio
from typing import Any, AsyncIterator

try:
    from anthropic import AsyncAnthropic, Omit

    HAS_ANTHROPIC = True
except ImportError:  # pragma: no cover - exercised when dependency absent
    AsyncAnthropic = None  # type: ignore[assignment,misc]
    Omit = None  # type: ignore[assignment,misc]
    HAS_ANTHROPIC = False

from kohakuterrarium.llm.anthropic_format import (
    ANTHROPIC_KNOWN_BODY_FIELDS,
    INTERNAL_EXTRA_KEYS,
    KT_CONTENT_KEY,
    anthropic_tools,
    apply_delta,
    block_to_dict,
    is_anthropic_api_endpoint,
    looks_like_bearer_endpoint,
    mark_system_cache,
    mark_tail_cache,
    normalise_started_block,
    ordered_blocks,
    prepare_messages,
    tool_calls_from_blocks,
    merge_usage,
    usage_to_dict,
)
from kohakuterrarium.llm.base import (
    BaseLLMProvider,
    ChatResponse,
    LLMConfig,
    NativeToolCall,
    ToolSchema,
)
from kohakuterrarium.llm.openai_sanitize import log_request_shape, strip_surrogates
from kohakuterrarium.llm.recovery import (
    ErrorClass,
    RetryPolicy,
    backoff_delay,
    classify_openai_error,
    drop_last_tool_round,
)
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

ANTHROPIC_BASE_URL = "https://api.anthropic.com"


class AnthropicProvider(BaseLLMProvider):
    """Anthropic Messages API provider using ``anthropic.AsyncAnthropic``."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "",
        base_url: str | None = ANTHROPIC_BASE_URL,
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        timeout: float = 300.0,
        extra_headers: dict[str, str] | None = None,
        extra_body: dict[str, Any] | None = None,
        max_retries: int = 3,
        service_tier: str | None = None,
        retry_policy: RetryPolicy | dict[str, Any] | None = None,
        auth_as_bearer: bool | None = None,
    ):
        super().__init__(
            LLMConfig(
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
                retry_policy=retry_policy,
            )
        )
        if not HAS_ANTHROPIC:
            raise ImportError(
                "anthropic not installed. Install with: pip install anthropic"
            )
        if not api_key:
            raise ValueError(
                "API key is required. Set ANTHROPIC_API_KEY or configure a "
                "provider key with 'kt login <provider>'."
            )

        self.extra_body = dict(extra_body or {})
        self._retry_policy = RetryPolicy.from_value(retry_policy)
        self._api_key = api_key
        self.base_url = base_url or ANTHROPIC_BASE_URL
        self._timeout = timeout
        self._extra_headers = dict(extra_headers or {})
        self._max_retries = max_retries
        self._service_tier = service_tier
        self._last_usage: dict[str, int] = {}
        self._last_tool_calls: list[NativeToolCall] = []
        self._last_assistant_extra_fields: dict[str, Any] = {}
        self.prompt_cache_key: str | None = None

        inferred_bearer = self.extra_body.get("auth_as_bearer")
        self.auth_as_bearer = (
            bool(inferred_bearer) if auth_as_bearer is None else bool(auth_as_bearer)
        )
        if auth_as_bearer is None and looks_like_bearer_endpoint(self.base_url):
            self.auth_as_bearer = True

        default_headers = dict(self._extra_headers)
        if self.auth_as_bearer:
            default_headers.setdefault("X-Api-Key", Omit())
        self._client = AsyncAnthropic(
            api_key=None if self.auth_as_bearer else api_key,
            auth_token=api_key if self.auth_as_bearer else None,
            base_url=self.base_url,
            timeout=timeout,
            max_retries=max_retries,
            default_headers=default_headers,
        )
        logger.debug(
            "AnthropicProvider initialized",
            model=model,
            base_url=self.base_url,
            auth_as_bearer=self.auth_as_bearer,
        )

    async def close(self) -> None:
        await self._client.close()

    def with_model(self, name: str) -> "AnthropicProvider":
        """Return a sibling provider using the same SDK client."""
        if not name or name == self.config.model:
            return self
        clone = object.__new__(AnthropicProvider)
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
        clone._retry_policy = self._retry_policy
        clone._api_key = self._api_key
        clone.base_url = self.base_url
        clone._timeout = self._timeout
        clone._extra_headers = dict(self._extra_headers)
        clone._max_retries = self._max_retries
        clone._service_tier = self._service_tier
        clone._last_usage = {}
        clone._last_tool_calls = []
        clone._last_assistant_extra_fields = {}
        clone._emergency_drop_callbacks = list(self._emergency_drop_callbacks)
        clone.prompt_cache_key = self.prompt_cache_key
        clone.auth_as_bearer = self.auth_as_bearer
        clone._client = self._client
        clone.provider_name = getattr(self, "provider_name", clone.provider_name)
        clone.provider_native_tools = getattr(
            self, "provider_native_tools", clone.provider_native_tools
        )
        if hasattr(self, "_profile_max_context"):
            clone._profile_max_context = self._profile_max_context
        return clone

    async def _stream_chat(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[ToolSchema] | None = None,
        provider_native_tools: list[Any] | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[str]:
        """Stream a native Anthropic Messages response with KT retries."""
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
        self._last_tool_calls = []
        self._last_usage = {}
        self._last_assistant_extra_fields = {}

        create_kwargs = self._build_create_kwargs(
            messages, tools=tools, stream=True, **kwargs
        )
        logger.debug(
            "Anthropic API request",
            model=create_kwargs["model"],
            messages=len(create_kwargs["messages"]),
            has_system=bool(create_kwargs.get("system")),
            tools=len(create_kwargs.get("tools") or []),
        )
        log_request_shape(
            "Starting Anthropic streaming request",
            create_kwargs["model"],
            create_kwargs["messages"],
        )

        stream = await self._client.messages.create(**create_kwargs)
        blocks: dict[int, dict[str, Any]] = {}
        stop_reason = ""

        async for event in stream:
            event_type = getattr(event, "type", "")
            if event_type == "message_start":
                msg = getattr(event, "message", None)
                self._last_usage = merge_usage(
                    self._last_usage, getattr(msg, "usage", None)
                )
                continue
            if event_type == "content_block_start":
                idx = int(getattr(event, "index", len(blocks)))
                blocks[idx] = normalise_started_block(
                    block_to_dict(getattr(event, "content_block", None))
                )
                continue
            if event_type == "content_block_delta":
                idx = int(getattr(event, "index", len(blocks)))
                piece = apply_delta(
                    blocks.setdefault(idx, {}), getattr(event, "delta", None)
                )
                if piece:
                    yield strip_surrogates(piece)
                continue
            if event_type == "message_delta":
                delta = getattr(event, "delta", None)
                stop_reason = getattr(delta, "stop_reason", "") or stop_reason
                usage = getattr(event, "usage", None)
                if usage is not None:
                    self._last_usage = merge_usage(self._last_usage, usage)
                continue

        content_blocks = ordered_blocks(blocks)
        self._last_tool_calls = tool_calls_from_blocks(content_blocks)
        if content_blocks:
            self._last_assistant_extra_fields = {KT_CONTENT_KEY: content_blocks}
        self._log_token_usage()
        if stop_reason:
            logger.debug("Anthropic stream completed", stop_reason=stop_reason)
        logger.debug(
            "Anthropic native tool calls received",
            count=len(self._last_tool_calls),
            tools=[tc.name for tc in self._last_tool_calls],
        )

    async def _complete_chat(
        self,
        messages: list[dict[str, Any]],
        **kwargs: Any,
    ) -> ChatResponse:
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
        self._last_tool_calls = []
        self._last_usage = {}
        self._last_assistant_extra_fields = {}

        create_kwargs = self._build_create_kwargs(messages, stream=False, **kwargs)
        log_request_shape(
            "Starting Anthropic non-streaming request",
            create_kwargs["model"],
            create_kwargs["messages"],
        )
        response = await self._client.messages.create(**create_kwargs)
        content_blocks = [
            block_to_dict(block) for block in getattr(response, "content", [])
        ]
        self._last_tool_calls = tool_calls_from_blocks(content_blocks)
        if content_blocks:
            self._last_assistant_extra_fields = {KT_CONTENT_KEY: content_blocks}
        self._last_usage = usage_to_dict(getattr(response, "usage", None))
        self._log_token_usage()
        text = "".join(
            str(block.get("text", ""))
            for block in content_blocks
            if block.get("type") == "text"
        )
        return ChatResponse(
            content=strip_surrogates(text),
            finish_reason=getattr(response, "stop_reason", None) or "unknown",
            usage=self._last_usage,
            model=getattr(response, "model", None) or create_kwargs["model"],
        )

    def _log_token_usage(self) -> None:
        if not self._last_usage:
            return
        logger.debug(
            "Anthropic request completed",
            tokens_in=self._last_usage.get("prompt_tokens"),
            tokens_out=self._last_usage.get("completion_tokens"),
            cache_creation=self._last_usage.get("cache_creation_input_tokens"),
            cache_read=self._last_usage.get("cache_read_input_tokens"),
        )

    def _with_prompt_cache_markers(
        self, create_kwargs: dict[str, Any]
    ) -> dict[str, Any]:
        updated = dict(create_kwargs)
        used = 0
        if "system" in updated:
            updated["system"] = mark_system_cache(updated["system"])
            used += 1
        messages = updated.get("messages")
        if isinstance(messages, list):
            updated["messages"] = mark_tail_cache(messages, max(0, 4 - used))
        return updated

    def _build_create_kwargs(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[ToolSchema] | None = None,
        stream: bool,
        **kwargs: Any,
    ) -> dict[str, Any]:
        system, anthropic_messages = prepare_messages(messages)
        create_kwargs: dict[str, Any] = {
            "model": kwargs.get("model", self.config.model),
            "messages": anthropic_messages,
            "max_tokens": kwargs.get("max_tokens", self.config.max_tokens) or 4096,
            "stream": stream,
        }
        if system:
            create_kwargs["system"] = system
        temp = kwargs.get("temperature", self.config.temperature)
        if temp is not None:
            create_kwargs["temperature"] = temp
        stop = kwargs.get("stop")
        if stop:
            create_kwargs["stop_sequences"] = stop
        if tools:
            create_kwargs["tools"] = anthropic_tools(tools)

        merged_extra = {**self.extra_body}
        merged_extra.update(kwargs.get("extra_body") or {})
        disable_cache = bool(merged_extra.get("disable_prompt_caching"))
        for key in list(INTERNAL_EXTRA_KEYS):
            merged_extra.pop(key, None)
        for key in list(ANTHROPIC_KNOWN_BODY_FIELDS):
            if key in merged_extra:
                create_kwargs[key] = merged_extra.pop(key)
        extra_headers = merged_extra.pop("extra_headers", None)
        if extra_headers:
            create_kwargs["extra_headers"] = dict(extra_headers)
        if self._service_tier and "service_tier" not in create_kwargs:
            create_kwargs["service_tier"] = self._service_tier
        if not disable_cache and is_anthropic_api_endpoint(self.base_url):
            create_kwargs = self._with_prompt_cache_markers(create_kwargs)
        if merged_extra:
            create_kwargs["extra_body"] = merged_extra
        return create_kwargs
