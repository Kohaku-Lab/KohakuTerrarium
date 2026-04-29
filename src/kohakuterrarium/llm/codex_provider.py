"""
Codex OAuth LLM provider - uses ChatGPT subscription for model access.

Uses the OpenAI Python SDK with the Codex backend endpoint. Authenticates
via OAuth PKCE (browser or device code flow). Billing goes to the user's
ChatGPT Plus/Pro subscription, not API credits.
"""

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
from kohakuterrarium.llm.recovery import RetryPolicy
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

CODEX_BASE_URL = "https://chatgpt.com/backend-api/codex"


async def _capture_rate_limit_headers(response: Any) -> None:
    """httpx response hook — capture Codex rate-limit headers.

    The Codex backend delivers ``x-codex-*`` rate-limit / credits /
    promo headers on every response. This hook parses them and stores
    the latest snapshot in the process-level cache for ``/codex-usage``
    to read. Failure is silent — the hook must never break the request.
    """
    try:
        snap = capture_from_headers(response.headers)
        set_cached(snap)
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug(
            "Codex rate-limit header capture failed",
            error=str(exc),
            exc_info=True,
        )


class CodexOAuthProvider(BaseLLMProvider):
    """LLM provider using ChatGPT subscription via Codex OAuth.

    Uses the AsyncOpenAI SDK's Responses API routed through the Codex backend.
    Supports streaming, tool calls, and auto token refresh.

    Usage:
        provider = CodexOAuthProvider(model="gpt-5.4")
        await provider.ensure_authenticated()

        async for chunk in provider.chat(messages, stream=True):
            print(chunk, end="")
    """

    # Provider-native tool compatibility key — matches the
    # ``provider_support`` declaration on ImageGenTool etc.
    provider_name = "codex"
    # Provider-native tools auto-injected into every creature that
    # runs on this provider (opt-out via creature config's
    # ``disable_provider_tools`` list).
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
    ):
        super().__init__(LLMConfig(model=model, retry_policy=retry_policy))
        self.model = model
        self.reasoning_effort = reasoning_effort  # none/minimal/low/medium/high/xhigh
        self.service_tier = service_tier  # None/priority/flex
        self.timeout = timeout
        self.max_retries = max_retries
        self._retry_policy = RetryPolicy.from_value(retry_policy)
        self._tokens: CodexTokens | None = None
        self._client: Any = None  # AsyncOpenAI
        self._last_tool_calls: list[NativeToolCall] = []
        self._last_usage: dict[str, int] = {}
        self._last_assistant_parts: list[Any] = []
        self.prompt_cache_key: str | None = None

    async def ensure_authenticated(self) -> None:
        """Ensure valid tokens exist. Opens browser/device code if needed."""
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
        """Create or recreate the AsyncOpenAI client with current token.

        Installs an httpx response event hook that captures rate-limit
        headers from every Codex response into the process-level cache
        (see ``codex_rate_limits.set_cached``). This replaces the dead
        ``/backend-api/codex/usage`` endpoint — rate limits now ride on
        every real API call's response.
        """
        if not HAS_OPENAI:
            raise ImportError("openai not installed. Install with: pip install openai")
        if not self._tokens:
            return

        # Custom httpx client with a response hook so we can observe
        # rate-limit headers on every response without changing the
        # streaming / non-streaming code paths.
        http_client = httpx.AsyncClient(
            event_hooks={"response": [_capture_rate_limit_headers]},
            timeout=self.timeout,
        )
        self._client = AsyncOpenAI(
            api_key=self._tokens.access_token,
            base_url=CODEX_BASE_URL,
            timeout=self.timeout,
            max_retries=self.max_retries,
            http_client=http_client,
        )

    async def _ensure_valid_token(self) -> None:
        """Refresh token if expired and rebuild client."""
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
        """Structured assistant parts from the most recent turn.

        The Codex provider captures images emitted via the
        ``image_generation`` built-in tool (and may later add other
        structured outputs here). Returns ``None`` when the turn was
        plain text so the controller keeps the zero-overhead fast path.
        """
        return self._last_assistant_parts or None

    def translate_provider_native_tool(self, tool: Any) -> dict | None:
        """Map a KT provider-native tool onto a Codex Responses tool spec.

        Currently supports ``image_gen`` (see
        :mod:`kohakuterrarium.llm.codex_image_gen`). Future Codex
        built-ins plug in here — dispatch by tool name, return
        ``None`` for anything this provider doesn't handle.
        """
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
        )
        clone._tokens = self._tokens
        clone._client = self._client
        clone._retry_policy = self._retry_policy
        clone._emergency_drop_callbacks = list(self._emergency_drop_callbacks)
        clone.prompt_cache_key = self.prompt_cache_key
        clone._profile_max_context = getattr(self, "_profile_max_context", None)
        return clone

    # ------------------------------------------------------------------
    # Chat Completions -> Responses API message conversion
    # ------------------------------------------------------------------

    _to_responses_input = staticmethod(to_responses_input)
    _fix_tool_call_pairing = staticmethod(fix_tool_call_pairing)

    # ------------------------------------------------------------------
    # Streaming (called by BaseLLMProvider.chat)
    # ------------------------------------------------------------------

    async def _stream_chat(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[ToolSchema] | None = None,
        provider_native_tools: list[Any] | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[str]:
        """Stream response from Codex backend using AsyncOpenAI SDK."""
        self._last_tool_calls = []
        self._last_usage = {}
        self._last_assistant_parts = []
        await self._ensure_valid_token()

        if not self._client:
            self._rebuild_client()

        # Extract system message as instructions
        instructions = ""
        input_messages = []
        for msg in messages:
            if msg.get("role") == "system":
                instructions = msg.get("content", "")
            else:
                input_messages.append(msg)

        # Convert Chat Completions format to Responses API flat array
        api_input = to_responses_input(input_messages)

        # Build tools in Responses API format — normal function tools
        # first, provider-native translations appended after.
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

        # Track the output format we requested for each provider-native
        # image tool so we can reconstruct a valid data URL extension
        # when image_generation_call lands in the stream.
        self._image_gen_output_format: str = "png"
        if provider_native_tools:
            for native in provider_native_tools:
                spec = self.translate_provider_native_tool(native)
                if spec is None:
                    continue
                api_tools = (api_tools or []) + [spec]
                if spec.get("type") == "image_generation":
                    self._image_gen_output_format = spec.get("output_format", "png")

        # Validate: function_call must be immediately followed by function_call_output
        # with matching call_id. Reorder, add placeholders, remove orphans.
        api_input = fix_tool_call_pairing(api_input)

        logger.debug(
            "Codex API request",
            model=self.model,
            input_items=len(api_input),
            input_preview=_json.dumps(api_input, ensure_ascii=False)[:500],
        )

        # Build optional params
        extra_params: dict[str, Any] = {}
        if self.reasoning_effort and self.reasoning_effort != "none":
            extra_params["reasoning"] = {"effort": self.reasoning_effort}
        if self.service_tier:
            extra_params["service_tier"] = self.service_tier

        instr_text = instructions or "You are a helpful assistant."
        # Prompt cache key: routes requests to the same backend server,
        # dramatically improving cache hit rates. Falls back to system
        # prompt hash if no session-level key is set.
        cache_key = (
            self.prompt_cache_key
            or hashlib.sha256(instr_text.encode()).hexdigest()[:32]
        )
        extra_headers = {"session_id": cache_key}

        try:
            stream = await self._client.responses.create(
                model=self.model,
                instructions=instr_text,
                input=api_input,
                tools=api_tools,
                store=False,
                stream=True,
                prompt_cache_key=cache_key,
                extra_headers=extra_headers,
                **extra_params,
            )
        except Exception as e:
            logger.error("Codex API request failed", error=str(e))
            raise

        # Process async stream events directly
        collected_tool_calls: list[NativeToolCall] = []

        async for event in stream:
            # Capture inline rate-limit SSE events if the backend emits them.
            # These carry the same data as response headers but arrive
            # during the stream, which can be fresher for long completions.
            # We don't branch on a specific codex event name here because
            # the SDK doesn't know about ``codex.rate_limits``; the
            # payload (if present) rides under a generic event type.
            maybe_capture_stream_rate_limit(
                event, parse_rate_limit_event, UsageSnapshot, set_cached
            )

            match event.type:
                case "response.output_text.delta":
                    yield event.delta
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
                        # Built-in image_generation tool output. Status
                        # at this event is typically "generating" — the
                        # image bytes are already in `result`; don't
                        # gate on status == "completed" (see
                        # plans/codex-provider-image-generation-plan.md).
                        self._handle_image_generation_call(item)
                case "response.completed":
                    # Extract usage from completed response
                    resp = getattr(event, "response", None)
                    if resp:
                        u = getattr(resp, "usage", None)
                        if u:
                            cached = 0
                            # Responses API: input_tokens_details
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

    # ------------------------------------------------------------------
    # Non-streaming
    # ------------------------------------------------------------------

    async def _complete_chat(
        self, messages: list[dict[str, Any]], **kwargs: Any
    ) -> ChatResponse:
        """Non-streaming completion (collects streaming output)."""
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
        """Cleanup."""
        if self._client:
            await self._client.close()
        self._client = None
