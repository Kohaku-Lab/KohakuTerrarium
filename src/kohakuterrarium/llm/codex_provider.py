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
from kohakuterrarium.llm.responses_ws import ResponsesWSError, ResponsesWSSession
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


def _reasoning_parts_text(parts: Any) -> str:
    """Join Responses reasoning content/summary parts into plain text."""
    if parts is None:
        return ""
    if isinstance(parts, str):
        return parts
    if isinstance(parts, dict):
        parts = [parts]
    if not isinstance(parts, list):
        return ""
    pieces: list[str] = []
    for part in parts:
        if isinstance(part, dict):
            value = part.get("text") or part.get("content") or ""
        else:
            value = getattr(part, "text", None) or getattr(part, "content", None) or ""
        if isinstance(value, str) and value:
            pieces.append(value)
    return "\n".join(pieces)


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
        extra_body: dict[str, Any] | None = None,
        websocket_mode: bool | None = None,
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
        self.extra_body = dict(extra_body or {})
        if websocket_mode is None:
            websocket_mode = bool(self.extra_body.get("websocket_mode"))
        self._websocket_mode = bool(websocket_mode)
        self._ws_session: ResponsesWSSession | None = None
        self._tokens: CodexTokens | None = None
        self._client: Any = None  # AsyncOpenAI
        self._last_tool_calls: list[NativeToolCall] = []
        self._last_usage: dict[str, int] = {}
        self._last_assistant_parts: list[Any] = []
        self._last_assistant_extra_fields: dict[str, Any] = {}
        self._reasoning_text = ""
        self._reasoning_summary = ""
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
            extra_body=dict(self.extra_body),
            websocket_mode=self._websocket_mode,
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
        self._last_assistant_extra_fields = {}
        self._reasoning_text = ""
        self._reasoning_summary = ""
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

        logger.debug(
            "Codex API request",
            model=self.model,
            input_items=len(api_input),
            input_preview=_json.dumps(api_input, ensure_ascii=False)[:500],
        )

        extra_params: dict[str, Any] = {}
        reasoning = self._merged_reasoning()
        if reasoning:
            extra_params["reasoning"] = reasoning
        if self.service_tier:
            extra_params["service_tier"] = self.service_tier
        wire_extra = self._wire_extra_body()

        instr_text = instructions or "You are a helpful assistant."
        # Stable routing improves prompt-cache reuse; the prompt hash is the fallback.
        cache_key = (
            self.prompt_cache_key
            or hashlib.sha256(instr_text.encode()).hexdigest()[:32]
        )
        # Third-party Responses endpoints may reject Codex's internal session header.
        session_headers = {} if self._api_key else {"session_id": cache_key}

        collected_tool_calls: list[NativeToolCall] = []

        if self._websocket_mode:
            session = self._ws_session_for_turn(session_headers)
            if session is not None:
                base_event: dict[str, Any] = {
                    "model": self.model,
                    "instructions": instr_text,
                    "store": False,
                    "prompt_cache_key": cache_key,
                    **extra_params,
                    **wire_extra,
                }
                if api_tools:
                    base_event["tools"] = api_tools
                try:
                    async for event in session.stream_turn(
                        base_event, api_input, fix_tool_call_pairing
                    ):
                        piece = self._process_stream_event(event, collected_tool_calls)
                        if piece is not None:
                            yield piece
                    self._pack_reasoning_extra_fields()
                    self._last_tool_calls = collected_tool_calls
                    return
                except ResponsesWSError as exc:
                    if exc.mid_stream:
                        raise
                    logger.warning(
                        "Codex WebSocket turn unavailable, using HTTP",
                        error=str(exc),
                    )
        # An HTTP turn advances the conversation past the WS-side cache.
        if self._ws_session is not None:
            self._ws_session.invalidate()

        if session_headers:
            extra_params["extra_headers"] = session_headers
        if wire_extra:
            extra_params["extra_body"] = wire_extra

        try:
            stream = await self._client.responses.create(
                model=self.model,
                instructions=instr_text,
                # Codex requires each function call adjacent to its matching output.
                input=fix_tool_call_pairing(api_input),
                tools=api_tools,
                store=False,
                stream=True,
                prompt_cache_key=cache_key,
                **extra_params,
            )
        except Exception as e:
            logger.error("Codex API request failed", error=str(e))
            raise

        async for event in stream:
            piece = self._process_stream_event(event, collected_tool_calls)
            if piece is not None:
                yield piece

        self._pack_reasoning_extra_fields()
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

    def _merged_reasoning(self) -> dict[str, Any]:
        """Combine the effort field with reasoning overrides from extra_body."""
        reasoning: dict[str, Any] = {}
        if self.reasoning_effort and self.reasoning_effort != "none":
            reasoning["effort"] = self.reasoning_effort
        override = self.extra_body.get("reasoning")
        if isinstance(override, dict):
            reasoning.update(override)
        return reasoning

    def _wire_extra_body(self) -> dict[str, Any]:
        """Return extra_body wire fields (framework knobs and reasoning removed)."""
        return {
            k: v
            for k, v in self.extra_body.items()
            if k not in ("reasoning", "websocket_mode", "disable_prompt_caching")
        }

    def _ws_session_for_turn(
        self, session_headers: dict[str, str]
    ) -> ResponsesWSSession | None:
        """Return the WS session, or ``None`` when a turn is already in flight."""
        self._ws_headers = dict(session_headers)
        if self._ws_session is None:

            def _factory() -> Any:
                # Late-bound so credential reloads and header updates apply.
                return self._client.responses.connect(
                    max_retries=0, extra_headers=dict(self._ws_headers)
                )

            self._ws_session = ResponsesWSSession(_factory)
        if self._ws_session.busy:
            return None
        return self._ws_session

    def _pack_reasoning_extra_fields(self) -> None:
        """Store captured Responses reasoning text for the conversation snapshot."""
        packed: dict[str, Any] = {}
        if self._reasoning_text:
            packed["reasoning_content"] = self._reasoning_text
        if self._reasoning_summary:
            packed["reasoning_summary"] = self._reasoning_summary
        self._last_assistant_extra_fields = packed

    def _capture_reasoning_item(self, item: Any) -> None:
        """Fold a completed ``reasoning`` output item into provider state."""
        summary = _reasoning_parts_text(getattr(item, "summary", None))
        content = _reasoning_parts_text(getattr(item, "content", None))
        if summary:
            self._reasoning_summary = "\n".join(
                part for part in (self._reasoning_summary, summary) if part
            )
        if content:
            self._reasoning_text = "\n".join(
                part for part in (self._reasoning_text, content) if part
            )

    def _process_stream_event(
        self, event: Any, collected_tool_calls: list[NativeToolCall]
    ) -> str | None:
        """Fold one Responses stream event into provider state; return text."""
        # Generic SDK events may carry fresher inline rate-limit payloads.
        maybe_capture_stream_rate_limit(
            event, parse_rate_limit_event, UsageSnapshot, set_cached
        )

        match getattr(event, "type", ""):
            case "response.output_text.delta":
                return strip_surrogates(event.delta)
            case "response.reasoning_text.delta":
                piece = getattr(event, "delta", None)
                if isinstance(piece, str):
                    self._reasoning_text += piece
            case "response.reasoning_summary_text.delta":
                piece = getattr(event, "delta", None)
                if isinstance(piece, str):
                    self._reasoning_summary += piece
            case "response.reasoning_text.done":
                piece = getattr(event, "text", None)
                if isinstance(piece, str) and piece:
                    self._reasoning_text = piece
            case "response.reasoning_summary_text.done":
                piece = getattr(event, "text", None)
                if isinstance(piece, str) and piece:
                    self._reasoning_summary = piece
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
                    # Image bytes are available before the item status completes.
                    self._handle_image_generation_call(item)
                elif itype == "reasoning":
                    self._capture_reasoning_item(item)
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
        return None

    def _handle_image_generation_call(self, item: Any) -> None:
        """Append an ImagePart for an ``image_generation_call`` item."""
        part = build_image_part(item, self._image_gen_output_format)
        if part is not None:
            self._last_assistant_parts.append(part)

    async def close(self) -> None:
        """Close the WebSocket session and the underlying SDK client."""
        if self._ws_session is not None:
            await self._ws_session.close()
            self._ws_session = None
        if self._client:
            await self._client.close()
        self._client = None
