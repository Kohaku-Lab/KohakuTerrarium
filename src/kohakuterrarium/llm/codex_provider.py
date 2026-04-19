"""
Codex OAuth LLM provider - uses ChatGPT subscription for model access.

Uses the OpenAI Python SDK with the Codex backend endpoint. Authenticates
via OAuth PKCE (browser or device code flow). Billing goes to the user's
ChatGPT Plus/Pro subscription, not API credits.
"""

import hashlib
import json as _json
from typing import Any, AsyncIterator

try:
    from openai import AsyncOpenAI

    HAS_OPENAI = True
except ImportError:
    AsyncOpenAI = None  # type: ignore[assignment,misc]
    HAS_OPENAI = False

from kohakuterrarium.llm.base import (
    BaseLLMProvider,
    ChatResponse,
    NativeToolCall,
    ToolSchema,
)
from kohakuterrarium.llm.codex_auth import (
    CodexTokens,
    oauth_login,
    refresh_tokens,
)
from kohakuterrarium.llm.codex_rate_limits import (
    capture_from_headers,
    parse_rate_limit_event,
    set_cached,
)
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


def _maybe_capture_stream_rate_limit(event: Any) -> None:
    """Capture a ``codex.rate_limits`` event if the SDK surfaces one.

    The Codex backend can emit in-stream rate-limit updates. The OpenAI
    SDK exposes unknown events with a type outside the ``response.*``
    family; we sniff the event for a dict-shaped payload carrying
    ``codex.rate_limits`` and feed it to the parser. Silent on failure.
    """
    try:
        # The event may carry a raw dict under ``event.data``,
        # ``event.event``, or the full model dict; try each.
        payload: Any = (
            getattr(event, "data", None)
            or getattr(event, "event", None)
            or getattr(event, "raw", None)
        )
        if payload is None:
            return
        if hasattr(payload, "model_dump"):
            payload_dict: Any = payload.model_dump()
        elif isinstance(payload, dict):
            payload_dict = payload
        else:
            return
        if not isinstance(payload_dict, dict):
            return
        if payload_dict.get("type") != "codex.rate_limits":
            return
        snap = parse_rate_limit_event(_json.dumps(payload_dict))
        if snap is not None and snap.has_data():
            from kohakuterrarium.llm.codex_rate_limits import UsageSnapshot

            set_cached(UsageSnapshot(snapshots=[snap]))
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug(
            "Codex rate-limit event capture failed",
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

    def __init__(
        self,
        model: str = "gpt-5.4",
        *,
        reasoning_effort: str = "medium",
        service_tier: str | None = None,
        timeout: float = 300.0,
        max_retries: int = 2,
    ):
        self.model = model
        self.reasoning_effort = reasoning_effort  # none/minimal/low/medium/high/xhigh
        self.service_tier = service_tier  # None/priority/flex
        self.timeout = timeout
        self.max_retries = max_retries
        self._tokens: CodexTokens | None = None
        self._client: Any = None  # AsyncOpenAI
        self._last_tool_calls: list[NativeToolCall] = []
        self._last_usage: dict[str, int] = {}
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
        import httpx

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

    # ------------------------------------------------------------------
    # Chat Completions -> Responses API message conversion
    # ------------------------------------------------------------------

    @staticmethod
    def _to_responses_input(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Convert Chat Completions messages to Responses API flat array.

        The Responses API uses a flat list of typed items instead of the
        nested ``role / tool_calls`` structure used by Chat Completions.
        System messages are skipped here because they are extracted
        separately as ``instructions``.
        """
        items: list[dict[str, Any]] = []
        for msg in messages:
            role = msg.get("role")
            content = msg.get("content", "")

            if role == "user":
                if isinstance(content, str):
                    items.append(
                        {
                            "role": "user",
                            "content": [{"type": "input_text", "text": content}],
                        }
                    )
                elif isinstance(content, list):
                    # Multimodal content parts
                    input_content: list[dict[str, Any]] = []
                    for part in content:
                        if isinstance(part, dict):
                            if part.get("type") == "text":
                                input_content.append(
                                    {
                                        "type": "input_text",
                                        "text": part.get("text", ""),
                                    }
                                )
                            elif part.get("type") == "image_url":
                                input_content.append(
                                    {
                                        "type": "input_image",
                                        "image_url": part["image_url"]["url"],
                                    }
                                )
                    if input_content:
                        items.append({"role": "user", "content": input_content})

            elif role == "assistant":
                # Text part (if any)
                if content:
                    if isinstance(content, str):
                        text = content
                    else:
                        text_parts = []
                        image_count = 0
                        for part in content:
                            if not isinstance(part, dict):
                                continue
                            if part.get("type") == "text":
                                text_parts.append(part.get("text", ""))
                            elif part.get("type") == "image_url":
                                image_count += 1
                        text = "\n".join(text_parts)
                        if image_count and not text:
                            text = f"[assistant multimodal content: {image_count} image(s)]"
                    if text:
                        items.append(
                            {
                                "role": "assistant",
                                "content": [{"type": "output_text", "text": text}],
                            }
                        )

                # Tool calls become separate top-level function_call items
                for tc in msg.get("tool_calls", []):
                    func = tc.get("function", {})
                    items.append(
                        {
                            "type": "function_call",
                            "call_id": tc.get("id", ""),
                            "name": func.get("name", ""),
                            "arguments": func.get("arguments", "{}"),
                        }
                    )

            elif role == "tool":
                if isinstance(content, str):
                    output = content
                else:
                    text_parts = []
                    image_count = 0
                    for part in content:
                        if not isinstance(part, dict):
                            continue
                        if part.get("type") == "text":
                            text_parts.append(part.get("text", ""))
                        elif part.get("type") == "image_url":
                            image_count += 1
                    output = "\n".join(text_parts)
                    if image_count and not output:
                        output = f"[tool multimodal output: {image_count} image(s)]"
                items.append(
                    {
                        "type": "function_call_output",
                        "call_id": msg.get("tool_call_id", ""),
                        "output": output,
                    }
                )

            # Skip system messages (already extracted as instructions)

        return items

    @staticmethod
    def _fix_tool_call_pairing(
        api_input: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Ensure every function_call is immediately followed by its function_call_output.

        The Responses API requires strict pairing: each ``function_call`` item
        must be directly followed by a ``function_call_output`` with the same
        ``call_id``.  Conversation truncation or compaction can break these
        pairs.

        This single-pass rebuild:
        1. Pulls all ``function_call_output`` items into a lookup dict.
        2. Walks the remaining items in order.
        3. After each ``function_call``, inserts the matching output (or a
           placeholder if the real output was lost).
        4. Orphan outputs (no matching call) are silently dropped.
        """
        # Index outputs by call_id (pop them out of the stream)
        output_by_id: dict[str, dict[str, Any]] = {}
        other_items: list[dict[str, Any]] = []
        for item in api_input:
            if item.get("type") == "function_call_output":
                output_by_id[item["call_id"]] = item
            else:
                other_items.append(item)

        # Rebuild: insert output right after its function_call
        result: list[dict[str, Any]] = []
        used_ids: set[str] = set()
        for item in other_items:
            result.append(item)
            if item.get("type") == "function_call":
                call_id = item["call_id"]
                if call_id in output_by_id:
                    result.append(output_by_id[call_id])
                else:
                    # Lost output — add placeholder so API doesn't reject
                    result.append(
                        {
                            "type": "function_call_output",
                            "call_id": call_id,
                            "output": f"[{item.get('name', '')}] Result unavailable "
                            "(removed by context compaction).",
                        }
                    )
                    logger.warning(
                        "Added missing function_call_output", call_id=call_id
                    )
                used_ids.add(call_id)

        # Log orphans (outputs whose call was compacted away — already dropped)
        orphan_ids = set(output_by_id) - used_ids
        if orphan_ids:
            logger.warning(
                "Dropped orphan function_call_outputs",
                orphan_count=len(orphan_ids),
            )

        return result

    # ------------------------------------------------------------------
    # Streaming (called by BaseLLMProvider.chat)
    # ------------------------------------------------------------------

    async def _stream_chat(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[ToolSchema] | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[str]:
        """Stream response from Codex backend using AsyncOpenAI SDK."""
        self._last_tool_calls = []
        self._last_usage = {}
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
        api_input = self._to_responses_input(input_messages)

        # Build tools in Responses API format
        api_tools = None
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

        # Validate: function_call must be immediately followed by function_call_output
        # with matching call_id. Reorder, add placeholders, remove orphans.
        api_input = self._fix_tool_call_pairing(api_input)

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
            _maybe_capture_stream_rate_limit(event)

            match event.type:
                case "response.output_text.delta":
                    yield event.delta
                case "response.output_item.done":
                    item = event.item
                    if getattr(item, "type", "") == "function_call":
                        collected_tool_calls.append(
                            NativeToolCall(
                                id=getattr(item, "call_id", ""),
                                name=getattr(item, "name", "") or "",
                                arguments=getattr(item, "arguments", ""),
                            )
                        )
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

    async def close(self) -> None:
        """Cleanup."""
        if self._client:
            await self._client.close()
        self._client = None
