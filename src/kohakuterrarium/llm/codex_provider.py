"""
Provide Responses API access through Codex OAuth or an explicit API key.
"""

import asyncio
import hashlib
import json as _json
from typing import Any, AsyncIterator

import httpx

try:
    from openai import AsyncOpenAI

    HAS_OPENAI = True
except ImportError:
    AsyncOpenAI = None  # type: ignore[assignment,misc]
    HAS_OPENAI = False

from kohakuterrarium.llm.base import (
    BaseLLMProvider,
    ChatResponse,
    LLMConfig,
    NativeToolCall,
    OverflowRecoveryState,
    ToolSchema,
)
from kohakuterrarium.llm.codex_auth import CodexTokens, oauth_login, refresh_tokens
from kohakuterrarium.llm.codex_format import (
    fix_tool_call_pairing,
    maybe_capture_stream_rate_limit,
    to_responses_input,
)
from kohakuterrarium.llm.codex_image_gen import (
    build_image_part,
    translate_image_gen_tool,
)
from kohakuterrarium.llm.codex_rate_limits import (
    capture_from_headers,
    parse_rate_limit_event,
    UsageSnapshot,
    set_cached,
)
from kohakuterrarium.llm.openai_sanitize import strip_surrogates
from kohakuterrarium.llm.recovery import (
    ErrorClass,
    RetryPolicy,
    backoff_delay,
    classify_openai_error,
)
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

CODEX_BASE_URL = "https://chatgpt.com/backend-api/codex"


async def _capture_rate_limit_headers(response: Any) -> None:
    """Cache rate-limit headers without allowing telemetry failures to break requests."""
    try:
        snap = capture_from_headers(response.headers)
        set_cached(snap)
    except Exception as exc:  # pragma: no cover - response hooks must be isolated
        logger.warning(
            "Codex rate-limit header capture failed",
            error=str(exc),
            exc_info=True,
        )


class CodexOAuthProvider(BaseLLMProvider):
    """Stream Codex Responses API output with tools, retries, and token refresh."""

    # Native tools use this key to declare provider compatibility.
    provider_name = "codex"
    # Image generation is available by default unless the creature opts out.
    provider_native_tools = frozenset({"image_gen"})

    def __init__(
        self,
        model: str = "gpt-5.4",
        *,
        reasoning_effort: str = "medium",
        service_tier: str | None = None,
        timeout: float = 300.0,
        max_retries: int = 2,
        retry_policy: RetryPolicy | dict[str, Any] | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
    ):
        super().__init__(LLMConfig(model=model, retry_policy=retry_policy))
        self.model = model
        self.reasoning_effort = reasoning_effort  # Codex effort wire value.
        self.service_tier = service_tier  # Optional Responses API service tier.
        self.timeout = timeout
        self.max_retries = max_retries
        self._retry_policy = RetryPolicy.from_value(retry_policy)
        # An explicit key bypasses OAuth and targets the configured Responses endpoint.
        self._api_key = api_key
        self._base_url = base_url
        self._tokens: CodexTokens | None = None
        self._client: Any = None  # AsyncOpenAI
        self._last_tool_calls: list[NativeToolCall] = []
        self._last_usage: dict[str, int] = {}
        self._last_assistant_parts: list[Any] = []
        self.prompt_cache_key: str | None = None

    async def ensure_authenticated(self) -> None:
        """Build the client from an API key or a valid OAuth token set."""
        if self._api_key:
            self._rebuild_client()
            return

        self._tokens = CodexTokens.load()

        if self._tokens and self._tokens.is_expired():
            try:
                self._tokens = await refresh_tokens(self._tokens)
            except Exception as e:
                logger.warning("Token refresh failed", error=str(e))
                self._tokens = None

        if not self._tokens:
            self._tokens = await oauth_login()

        self._rebuild_client()

    def _rebuild_client(self) -> None:
        """Recreate the SDK client and attach passive rate-limit capture."""
        if not HAS_OPENAI:
            raise ImportError("openai not installed. Install with: pip install openai")
        # Explicit credentials must take precedence over cached OAuth state.
        key = self._api_key or (self._tokens.access_token if self._tokens else None)
        if not key:
            return

        # A response hook keeps rate-limit capture identical across request modes.
        http_client = httpx.AsyncClient(
            event_hooks={"response": [_capture_rate_limit_headers]},
            timeout=self.timeout,
        )
        self._client = AsyncOpenAI(
            api_key=key,
            base_url=self._base_url or CODEX_BASE_URL,
            timeout=self.timeout,
            max_retries=self.max_retries,
            http_client=http_client,
        )

    async def _ensure_valid_token(self) -> None:
        """Refresh token if expired and rebuild client (OAuth mode only)."""
        if self._api_key:
            if not self._client:
                self._rebuild_client()
            return
        if not self._tokens:
            await self.ensure_authenticated()
            return
        if self._tokens.is_expired():
            self._tokens = await refresh_tokens(self._tokens)
            self._rebuild_client()

    @property
    def last_tool_calls(self) -> list[NativeToolCall]:
        return self._last_tool_calls

    @property
    def last_assistant_content_parts(self) -> list[Any] | None:
        """Return generated images and other structured parts from the last turn."""
        return self._last_assistant_parts or None

    def translate_provider_native_tool(self, tool: Any) -> dict | None:
        """Translate supported native tools into Codex Responses schemas."""
        return translate_image_gen_tool(tool)

    def with_model(self, name: str) -> "CodexOAuthProvider":
        """Return a sibling Codex provider preserving tokens/client."""
        if not name or name == self.model:
            return self
        clone = CodexOAuthProvider(
            model=name,
            reasoning_effort=self.reasoning_effort,
            service_tier=self.service_tier,
            timeout=self.timeout,
            max_retries=self.max_retries,
            retry_policy=self._retry_policy,
            api_key=self._api_key,
            base_url=self._base_url,
        )
        clone._tokens = self._tokens
        clone._client = self._client
        clone._retry_policy = self._retry_policy
        clone._emergency_drop_callbacks = list(self._emergency_drop_callbacks)
        clone.prompt_cache_key = self.prompt_cache_key
        clone._profile_max_context = getattr(self, "_profile_max_context", None)
        return clone

    _to_responses_input = staticmethod(to_responses_input)
    _fix_tool_call_pairing = staticmethod(fix_tool_call_pairing)

    async def _stream_chat(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[ToolSchema] | None = None,
        provider_native_tools: list[Any] | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[str]:
        """Stream with classified retries and two-stage overflow recovery."""
        current = messages
        attempt = 0
        overflow_state = OverflowRecoveryState()
        while True:
            try:
                async for chunk in self._raw_stream_chat(
                    current,
                    tools=tools,
                    provider_native_tools=provider_native_tools,
                    **kwargs,
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
        provider_native_tools: list[Any] | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[str]:
        """Perform one streaming request without retry orchestration."""
        self._last_tool_calls = []
        self._last_usage = {}
        self._last_assistant_parts = []
        await self._ensure_valid_token()

        if not self._client:
            self._rebuild_client()

        # Responses API carries system content separately as instructions.
        instructions = ""
        input_messages = []
        for msg in messages:
            if msg.get("role") == "system":
                instructions = msg.get("content", "")
            else:
                input_messages.append(msg)

        api_input = to_responses_input(input_messages)

        # Function tools precede provider-native tools in the outbound list.
        api_tools: list[dict[str, Any]] | None = None
        if tools:
            api_tools = [
                {
                    "type": "function",
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.parameters,
                }
                for t in tools
            ]

        # The requested format determines the data URL media extension on output.
        self._image_gen_output_format: str = "png"
        if provider_native_tools:
            for native in provider_native_tools:
                spec = self.translate_provider_native_tool(native)
                if spec is None:
                    continue
                api_tools = (api_tools or []) + [spec]
                if spec.get("type") == "image_generation":
                    self._image_gen_output_format = spec.get("output_format", "png")

        # Codex requires each function call to be adjacent to its matching output.
        api_input = fix_tool_call_pairing(api_input)

        logger.debug(
            "Codex API request",
            model=self.model,
            input_items=len(api_input),
            input_preview=_json.dumps(api_input, ensure_ascii=False)[:500],
        )

        extra_params: dict[str, Any] = {}
        if self.reasoning_effort and self.reasoning_effort != "none":
            extra_params["reasoning"] = {"effort": self.reasoning_effort}
        if self.service_tier:
            extra_params["service_tier"] = self.service_tier

        instr_text = instructions or "You are a helpful assistant."
        # Stable routing improves prompt-cache reuse; the prompt hash is the fallback.
        cache_key = (
            self.prompt_cache_key
            or hashlib.sha256(instr_text.encode()).hexdigest()[:32]
        )
        # Third-party Responses endpoints may reject Codex's internal session header.
        if not self._api_key:
            extra_params["extra_headers"] = {"session_id": cache_key}

        try:
            stream = await self._client.responses.create(
                model=self.model,
                instructions=instr_text,
                input=api_input,
                tools=api_tools,
                store=False,
                stream=True,
                prompt_cache_key=cache_key,
                **extra_params,
            )
        except Exception as e:
            logger.error("Codex API request failed", error=str(e))
            raise

        collected_tool_calls: list[NativeToolCall] = []

        async for event in stream:
            # Generic SDK events may carry fresher inline rate-limit payloads.
            maybe_capture_stream_rate_limit(
                event, parse_rate_limit_event, UsageSnapshot, set_cached
            )

            match event.type:
                case "response.output_text.delta":
                    yield strip_surrogates(event.delta)
                case "response.output_item.done":
                    item = event.item
                    itype = getattr(item, "type", "")
                    if itype == "function_call":
                        collected_tool_calls.append(
                            NativeToolCall(
                                id=getattr(item, "call_id", ""),
                                name=getattr(item, "name", "") or "",
                                arguments=getattr(item, "arguments", ""),
                            )
                        )
                    elif itype == "image_generation_call":
                        # Image bytes are available before the item status becomes completed.
                        self._handle_image_generation_call(item)
                case "response.completed":
                    resp = getattr(event, "response", None)
                    if resp:
                        u = getattr(resp, "usage", None)
                        if u:
                            cached = 0
                            details = getattr(u, "input_tokens_details", None)
                            if details:
                                cached = getattr(details, "cached_tokens", 0) or 0
                            self._last_usage = {
                                "prompt_tokens": getattr(u, "input_tokens", 0),
                                "completion_tokens": getattr(u, "output_tokens", 0),
                                "total_tokens": getattr(u, "total_tokens", 0),
                                "cached_tokens": cached,
                            }

        self._last_tool_calls = collected_tool_calls

    async def _complete_chat(
        self, messages: list[dict[str, Any]], **kwargs: Any
    ) -> ChatResponse:
        """Collect the streaming implementation into one complete response."""
        parts: list[str] = []
        async for chunk in self._stream_chat(messages, **kwargs):
            parts.append(chunk)
        return ChatResponse(
            content="".join(parts),
            finish_reason="stop",
            usage={"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            model=self.model,
        )

    def _handle_image_generation_call(self, item: Any) -> None:
        """Append an ImagePart for an ``image_generation_call`` item."""
        part = build_image_part(item, self._image_gen_output_format)
        if part is not None:
            self._last_assistant_parts.append(part)

    async def close(self) -> None:
        """Close the underlying SDK client."""
        if self._client:
            await self._client.close()
        self._client = None
