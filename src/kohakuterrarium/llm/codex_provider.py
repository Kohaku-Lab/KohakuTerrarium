"""Provide Responses API access through Codex OAuth or an explicit API key."""

import asyncio
import hashlib
import logging
from contextlib import aclosing
from copy import deepcopy
from typing import Any, AsyncIterator

import httpx

try:
    from openai import AsyncOpenAI

    HAS_OPENAI = True
except ImportError:
    AsyncOpenAI = None  # type: ignore[assignment,misc]
    HAS_OPENAI = False

from kohakuterrarium.llm import responses_ws_options as ws_options
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
    to_responses_input,
)
from kohakuterrarium.llm.codex_image_budget import (
    CODEX_BASE_URL,
    initial_image_limit,
    limit_codex_images,
    reported_image_limit,
)
from kohakuterrarium.llm.codex_image_gen import (
    translate_image_gen_tool,
)
from kohakuterrarium.llm.codex_rate_limits import (
    capture_rate_limit_headers as _capture_rate_limit_headers,
)
from kohakuterrarium.llm.codex_stream import process_codex_event, stream_codex_ws_turn
from kohakuterrarium.llm.codex_ws import CodexWSMixin
from kohakuterrarium.llm.responses_reasoning import ResponsesReasoningCollector
from kohakuterrarium.llm.recovery import (
    RetryPolicy,
    backoff_delay,
    classify_openai_error,
)
from kohakuterrarium.llm.responses_ws import ResponsesWSError, ResponsesWSSession
from kohakuterrarium.llm.stream_replay import ReplayFilter
from kohakuterrarium.llm.responses_ws_recovery import (
    WSRecovery,
    fresh_recovery,
    server_rejected,
)
from kohakuterrarium.llm.responses_tools import prepare_request_tools
from kohakuterrarium.modules.tool.request_replay import tool_request_replay
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


class CodexOAuthProvider(CodexWSMixin, BaseLLMProvider):
    """Stream Codex Responses API output with tools, retries, and token refresh."""

    # Native-tool compatibility key; image generation is enabled unless opted out.
    provider_name = "codex"
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
        self._api_key = api_key
        self._base_url = base_url
        self._request_image_limit = initial_image_limit(
            api_key, base_url or CODEX_BASE_URL
        )
        self.extra_body = dict(extra_body or {})
        self._ws_connection_options = ws_options.build_websocket_connection_options(
            self.extra_body.get("websocket_connection_options"), timeout=timeout
        )
        self._ws_session_options = ws_options.build_ws_session_options(self.extra_body)
        self._request_max_bytes = ws_options.request_max_bytes(self.extra_body)
        if websocket_mode is None:
            websocket_mode = bool(self.extra_body.get("websocket_mode"))
        self._websocket_mode = bool(websocket_mode)
        self._ws_session: ResponsesWSSession | None = None
        self._tokens: CodexTokens | None = None
        self._token_lock = asyncio.Lock()
        self._client: Any = None  # AsyncOpenAI
        self._last_tool_calls: list[NativeToolCall] = []
        self._last_usage: dict[str, int] = {}
        self._last_assistant_parts: list[Any] = []
        self._last_assistant_extra_fields: dict[str, Any] = {}
        self._reasoning = ResponsesReasoningCollector()
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
        self._borrowed_client = False

    async def _ensure_valid_token(self) -> None:
        """Adopt a newer on-disk login or refresh the expired access token."""
        if self._api_key:
            if not self._client:
                self._rebuild_client()
            return
        async with self._token_lock:
            if not self._tokens:
                await self.ensure_authenticated()
                await self._reset_ws_session()
                return
            if not self._tokens.is_expired():
                return
            reloaded = CodexTokens.load()
            if reloaded is not None and not reloaded.is_expired():
                self._tokens = reloaded
            else:
                try:
                    self._tokens = await refresh_tokens(self._tokens)
                except Exception:
                    reloaded = CodexTokens.load()
                    if reloaded is None or reloaded.is_expired():
                        raise
                    self._tokens = reloaded
            await self._reset_ws_session()
            self._rebuild_client()

    @staticmethod
    def _is_unauthorized_error(exc: BaseException) -> bool:
        """Return whether the server rejected the cached access token."""
        return getattr(exc, "status_code", None) == 401

    async def _recover_unauthorized(self) -> bool:
        """Reload or refresh credentials after a server 401, then rebuild the client."""
        async with self._token_lock:
            if self._tokens is None:
                return False
            previous_access = self._tokens.access_token
            reloaded = CodexTokens.load()
            if reloaded is not None:
                self._tokens = reloaded
            if (
                self._tokens.is_expired()
                or self._tokens.access_token == previous_access
            ):
                try:
                    self._tokens = await refresh_tokens(self._tokens)
                except Exception:
                    recovered = CodexTokens.load()
                    if recovered is None or recovered.access_token == previous_access:
                        return False
                    self._tokens = recovered
            if self._tokens.access_token == previous_access:
                return False
            await self._reset_ws_session()
            self._rebuild_client()
            return True

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
            extra_body={
                **self.extra_body,
                "websocket_connection_options": self._ws_connection_options,
            },
            websocket_mode=self._websocket_mode,
        )
        clone._tokens = self._tokens
        self.share_request_ceiling(clone)
        clone.extra_body = dict(self.extra_body)
        clone._token_lock = self._token_lock
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
        """Stream with classified retries plus overflow and content recovery."""
        current = deepcopy(messages) if self._websocket_mode else messages
        if self._websocket_mode:
            kwargs = deepcopy(kwargs)
            kwargs["_ws_recovery"] = WSRecovery(self._retry_policy)
        recovery = kwargs.get("_ws_recovery")
        attempt = 0
        auth_retry = False
        image_retry = False
        overflow_state = OverflowRecoveryState()
        replay = ReplayFilter()
        try:
            while True:
                emitted = False
                replay.begin_attempt()
                try:
                    async with aclosing(
                        self._raw_stream_chat(
                            current,
                            tools=tools,
                            provider_native_tools=provider_native_tools,
                            **kwargs,
                        )
                    ) as stream:
                        async for chunk in stream:
                            emitted = True
                            if recovery is not None and chunk:
                                recovery.delivered = True
                            if text := replay.feed(chunk):
                                yield text
                    return
                except Exception as exc:
                    limit = reported_image_limit(exc, self._request_image_limit)
                    if (
                        limit is not None
                        and not image_retry
                        and not emitted
                        and not (recovery is not None and recovery.delivered)
                    ):
                        self._request_image_limit = limit
                        image_retry = True
                        if recovery is None or recovery.has_budget:
                            if self._ws_session is not None:
                                self._ws_session.invalidate()
                            continue
                    if recovery is not None and recovery.delivered:
                        raise
                    cls = classify_openai_error(exc)
                    if not emitted and server_rejected(exc):
                        replacement = await self._recover_context(
                            cls, exc, current, overflow_state
                        )
                        if replacement is not None:
                            current = replacement
                            recovery = fresh_recovery(recovery, self._retry_policy)
                            if recovery is not None:
                                kwargs["_ws_recovery"] = recovery
                            continue
                    if recovery is not None and not recovery.has_budget:
                        raise
                    if isinstance(exc, ResponsesWSError) and (
                        exc.submitted or exc.mid_stream
                    ):
                        raise
                    if (
                        not auth_retry
                        and not emitted
                        and not self._api_key
                        and self._is_unauthorized_error(exc)
                    ):
                        auth_retry = True
                        if await self._recover_unauthorized():
                            logger.warning(
                                "Codex credential rejected; retrying with refreshed token",
                                error_class=cls.value,
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
                        if recovery is not None:
                            await recovery.status(
                                "waiting" if delay else "reconnecting"
                            )
                        await asyncio.sleep(delay)
                        continue
                    raise
        finally:
            if recovery is not None:
                await recovery.status(None)

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
        self._reasoning = ResponsesReasoningCollector()
        await self._ensure_valid_token()

        if not self._client:
            self._rebuild_client()

        instructions = ""
        input_messages = []
        for msg in messages:
            if msg.get("role") == "system":
                instructions = msg.get("content", "")
            else:
                input_messages.append(msg)

        echo_options = dict(model=self.model, extra_body=deepcopy(self.extra_body))
        api_input = to_responses_input(
            limit_codex_images(input_messages, self._request_image_limit),
            model=self.model,
            replay_reasoning=self.extra_body.get("responses_reasoning_replay"),
        )
        api_input = (await self._fit_request({"input": api_input}, "codex"))["input"]

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
                spec = {**spec, "request_replay": tool_request_replay(native)}
                api_tools = (api_tools or []) + [spec]
                if spec.get("type") == "image_generation":
                    self._image_gen_output_format = spec.get("output_format", "png")

        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(
                "Codex API request",
                model=self.model,
                input_items=len(api_input),
                input_types=[item.get("type", "message") for item in api_input[:10]],
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

        recovery = kwargs.get("_ws_recovery")
        if self._websocket_mode and not (recovery and recovery.http):
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
                    async with aclosing(
                        stream_codex_ws_turn(
                            self,
                            session,
                            base_event,
                            api_input,
                            echo_options,
                            kwargs.get("_ws_recovery")
                            or WSRecovery(self._retry_policy),
                        )
                    ) as stream:
                        async for piece in stream:
                            yield piece
                    return
                except ResponsesWSError as exc:
                    if exc.mid_stream or exc.submitted:
                        raise
                    if not exc.oversize:
                        session.note_http_fallback()
                    logger.warning(
                        "Codex WebSocket turn unavailable, using HTTP",
                        error=str(exc),
                        oversize=exc.oversize,
                    )
        if recovery is not None:
            recovery.http = True
        # An HTTP turn advances the conversation past the WS-side cache.
        if self._ws_session is not None:
            self._ws_session.invalidate()

        if session_headers:
            extra_params["extra_headers"] = session_headers
        if wire_extra:
            extra_params["extra_body"] = prepare_request_tools(wire_extra)[0]
        api_tools = prepare_request_tools({"tools": api_tools})[0]["tools"]

        client = self._client
        if recovery is not None:
            recovery.record_submission()
            client = client.with_options(max_retries=0)
        try:
            stream = await client.responses.create(
                model=self.model,
                instructions=instr_text,
                # Keep parallel calls together with their matching outputs.
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
            if recovery is not None:
                await recovery.status(None)
            piece = self._process_stream_event(event, collected_tool_calls)
            if piece is not None:
                yield piece

        self._last_assistant_extra_fields = self._reasoning.fields()
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
        knobs = ws_options.FRAMEWORK_KNOBS | {"reasoning"}
        return {k: v for k, v in self.extra_body.items() if k not in knobs}

    def _process_stream_event(
        self,
        event: Any,
        collected_tool_calls: list[NativeToolCall],
        image_parts: list | None = None,
    ) -> str | None:
        """Fold one HTTP or WebSocket event into the current attempt."""
        return process_codex_event(self, event, collected_tool_calls, image_parts)
