"""
Provide streaming and complete chat access to OpenAI-compatible endpoints.
"""

import asyncio
from typing import Any, AsyncIterator

from openai import AsyncOpenAI

from kohakuterrarium.llm.anthropic_cache import (
    apply_anthropic_cache_markers,
    is_anthropic_endpoint,
)
from kohakuterrarium.llm.api_keys import get_api_key
from kohakuterrarium.llm.base import (
    BaseLLMProvider,
    ChatResponse,
    LLMConfig,
    OverflowRecoveryState,
    ToolSchema,
)
from kohakuterrarium.llm.artifact_resolve import resolve_message_image_urls
from kohakuterrarium.llm.openai_helpers import (
    delta_field,
    delta_field_present,
    extract_usage,
    log_token_usage,
    merge_reasoning_detail_stream,
    normalize_stateful_assistant_fields,
    pack_reasoning_fields,
    tool_call_from_pending,
    tool_calls_from_message,
)
from kohakuterrarium.llm.openai_sanitize import (
    log_request_shape,
    strip_kt_extras,
    strip_surrogates,
)
from kohakuterrarium.llm.recovery import (
    ErrorClass,
    RetryPolicy,
    backoff_delay,
    classify_openai_error,
)
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

_delta_field = delta_field
_pack_reasoning_fields = pack_reasoning_fields

# Canonical endpoints used by built-in profiles.
OPENAI_BASE_URL = "https://api.openai.com/v1"
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


class OpenAIProvider(BaseLLMProvider):
    """OpenAI-compatible provider with retries, tools, caching, and reasoning echo."""

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
        """Configure an OpenAI-compatible client and optional stateful reasoning echo."""
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
        # Retain the endpoint string because cache detection cannot rely on SDK internals.
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

        # Report caching once at construction rather than on every request.
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
        credential_provider = getattr(self, "_credential_provider", "")
        if credential_provider:
            clone._credential_provider = credential_provider
        if hasattr(self, "_profile_max_context"):
            clone._profile_max_context = self._profile_max_context
        return clone

    def reload_credentials(self) -> bool:
        """Rotate profile-backed credentials and rebuild the SDK client in place."""
        # Profile identity is authoritative; inline providers have no reload source.
        lookup_key = getattr(self, "_credential_provider", "") or self.provider_name
        if not lookup_key:
            return False
        new_key = get_api_key(lookup_key)
        if not new_key or new_key == self._api_key:
            return False
        old = self._client
        self._api_key = new_key
        self._client = AsyncOpenAI(
            api_key=new_key,
            base_url=self._base_url_input,
            timeout=self._timeout,
            max_retries=self._max_retries,
            default_headers=self._extra_headers,
        )
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(old.close())
        except RuntimeError:
            # A temporary loop can corrupt anyio state, so defer cleanup to GC.
            pass
        logger.info(
            "OpenAIProvider credentials reloaded",
            provider=lookup_key,
        )
        return True

    def _prepare_messages(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Resolve local images, strip internal fields, and add eligible cache markers."""
        messages = resolve_message_image_urls(messages)
        messages = strip_kt_extras(messages)
        messages = normalize_stateful_assistant_fields(messages)
        if not is_anthropic_endpoint(self.base_url, None):
            return messages
        if self.extra_body.get("disable_prompt_caching"):
            return messages
        return apply_anthropic_cache_markers(messages)

    def _sanitize_extra_body(self, extra: dict[str, Any]) -> dict[str, Any]:
        """Remove framework-only request knobs before provider submission."""
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
        overflow_state = OverflowRecoveryState()
        while True:
            try:
                async for chunk in self._raw_stream_chat(
                    current, tools=tools, **kwargs
                ):
                    yield chunk
                return
            except Exception as exc:
                cls = classify_openai_error(exc)
                if cls is ErrorClass.OVERFLOW:
                    replacement = await self._recover_from_overflow(
                        current, overflow_state
                    )
                    if replacement is not None:
                        current = replacement
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

        merged_extra = {**self.extra_body}
        if "extra_body" in kwargs:
            merged_extra.update(kwargs["extra_body"])
        merged_extra = self._sanitize_extra_body(merged_extra)
        if merged_extra:
            create_kwargs["extra_body"] = merged_extra

        # Stable routing allows compatible backends to reuse cached prompt prefixes.
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
        reasoning_text_seen = False
        reasoning_details_seen = False

        stream = await self._client.chat.completions.create(**create_kwargs)

        async for chunk in stream:
            if chunk.usage:
                self._last_usage = extract_usage(chunk.usage)

            if not chunk.choices:
                continue

            delta = chunk.choices[0].delta

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

            # Stateful reasoning fields arrive through the SDK's untyped extra surface.
            if self.echo_reasoning:
                if delta_field_present(delta, "reasoning_content"):
                    reasoning_text_seen = True
                rc_piece = delta_field(delta, "reasoning_content")
                if isinstance(rc_piece, str):
                    reasoning_text += rc_piece
                if delta_field_present(delta, "reasoning_details"):
                    reasoning_details_seen = True
                rd_piece = delta_field(delta, "reasoning_details")
                if isinstance(rd_piece, list):
                    # Merge by identity so streamed fragments round-trip as one block.
                    for entry in rd_piece:
                        if isinstance(entry, dict):
                            merge_reasoning_detail_stream(reasoning_details, entry)
                # Preserve the plain reasoning field independently of structured details.
                if delta_field_present(delta, "reasoning"):
                    r_piece = delta_field(delta, "reasoning")
                    if isinstance(r_piece, str):
                        reasoning_extra["reasoning"] = (
                            reasoning_extra.get("reasoning", "") + r_piece
                        )

            if delta.content:
                yield strip_surrogates(delta.content)

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

        if (
            reasoning_text_seen
            or reasoning_details_seen
            or reasoning_text
            or reasoning_details
            or reasoning_extra
        ):
            self._last_assistant_extra_fields = pack_reasoning_fields(
                reasoning_text,
                reasoning_details,
                reasoning_extra,
                include_text=reasoning_text_seen,
                include_details=reasoning_details_seen,
            )
            logger.debug(
                "Reasoning fields captured",
                has_content=bool(reasoning_text),
                details_count=len(reasoning_details),
            )

        log_token_usage(self._last_usage)

    async def _complete_chat(
        self,
        messages: list[dict[str, Any]],
        **kwargs: Any,
    ) -> ChatResponse:
        """Non-streaming chat completion with retry and overflow recovery."""
        current = messages
        attempt = 0
        overflow_state = OverflowRecoveryState()
        while True:
            try:
                return await self._raw_complete_chat(current, **kwargs)
            except Exception as exc:
                cls = classify_openai_error(exc)
                if cls is ErrorClass.OVERFLOW:
                    replacement = await self._recover_from_overflow(
                        current, overflow_state
                    )
                    if replacement is not None:
                        current = replacement
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

        if message.tool_calls:
            self._last_tool_calls = tool_calls_from_message(message.tool_calls)
            logger.debug(
                "Native tool calls received (non-streaming)",
                count=len(self._last_tool_calls),
                tools=[tc.name for tc in self._last_tool_calls],
            )

        if self.echo_reasoning:
            rc = delta_field(message, "reasoning_content")
            rd = delta_field(message, "reasoning_details")
            r = delta_field(message, "reasoning")
            extras = {}
            if delta_field_present(message, "reasoning_content") and isinstance(
                rc, str
            ):
                extras["reasoning_content"] = rc
            if delta_field_present(message, "reasoning_details") and isinstance(
                rd, list
            ):
                extras["reasoning_details"] = rd
            if delta_field_present(message, "reasoning") and isinstance(r, str):
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

    async def __aenter__(self) -> "OpenAIProvider":
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close()
