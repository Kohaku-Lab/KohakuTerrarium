"""
Define the provider protocol, shared response types, and base implementation.
"""

import asyncio
import copy
import json
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Callable, Protocol, runtime_checkable

from kohakuterrarium.llm.message import Message
from kohakuterrarium.llm.context_repair import CONTENT_REPAIR_LADDER, ContextRepair
from kohakuterrarium.llm.recovery import ErrorClass, RetryPolicy, drop_last_tool_round
from kohakuterrarium.llm.request_budget import (
    MAX_REQUEST_SHRINKS,
    RequestCeiling,
    RequestMeasure,
    effective_target,
    fit_request,
)
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


@dataclass
class LLMConfig:
    """Provider-independent generation settings and provider-specific extras."""

    model: str
    temperature: float = 0.7
    max_tokens: int | None = None
    top_p: float = 1.0
    stop: list[str] | None = None
    extra: dict[str, Any] | None = None
    retry_policy: RetryPolicy | dict[str, Any] | None = None


@dataclass
class ChatChunk:
    """Text and metadata emitted by one streaming response chunk."""

    content: str = ""
    finish_reason: str | None = None
    usage: dict[str, int] | None = None


@dataclass
class ChatResponse:
    """Complete non-streaming response with usage and model metadata."""

    content: str
    finish_reason: str
    usage: dict[str, int]
    model: str


@dataclass
class ToolSchema:
    """OpenAI-compatible native function schema."""

    name: str
    description: str
    parameters: dict[str, Any] = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {},
        }
    )

    def to_api_format(self) -> dict[str, Any]:
        """Convert to OpenAI API tools format."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


@dataclass
class NativeToolCall:
    """Native tool call returned by a provider API."""

    id: str
    name: str
    arguments: str  # Providers return arguments as encoded JSON.

    def parsed_arguments(self) -> dict[str, Any]:
        """Parse the JSON arguments string."""
        try:
            return json.loads(self.arguments)
        except json.JSONDecodeError:
            logger.warning(
                "Failed to parse tool call arguments",
                tool_call_id=self.id,
                tool_name=self.name,
            )
            return {"_raw": self.arguments}


@runtime_checkable
class LLMProvider(Protocol):
    """OpenAI-oriented interface implemented by every LLM provider."""

    @property
    def last_tool_calls(self) -> list[NativeToolCall]:
        """
        Tool calls from the last streaming response (native mode only).

        Only populated after a chat() call with tools provided.
        """
        ...

    async def chat(
        self,
        messages: list[Message] | list[dict[str, Any]],
        *,
        stream: bool = True,
        tools: list[ToolSchema] | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[str]:
        """Yield response text, streaming when requested and recording native calls."""
        ...

    async def chat_complete(
        self,
        messages: list[Message] | list[dict[str, Any]],
        **kwargs: Any,
    ) -> ChatResponse:
        """Return a complete response for the supplied conversation."""
        ...

    def reload_credentials(self) -> bool:
        """Re-resolve API credentials + rebuild the SDK client.

        Returns ``True`` when the credential rotated. See
        :meth:`BaseLLMProvider.reload_credentials` for the default
        (no-op) implementation.
        """
        ...


def fork_llm(llm: "LLMProvider") -> "LLMProvider":
    """Give a new conversation its own provider sibling when the provider can fork.

    A fork shares credentials and the HTTP client but owns its transport
    session and per-turn state, so it never contends with its origin.
    """
    fork = getattr(llm, "fork", None)
    return fork() if callable(fork) else llm


@dataclass
class OverflowRecoveryState:
    """Track the bounded stages of overflow, oversize and content recovery for one request."""

    rescue_attempted: bool = False
    drop_attempted: bool = False
    content_stage: int = 0
    shrinks: int = 0
    refused_bytes: int | None = None


# Per-conversation attributes a fork starts empty instead of sharing.
_FORK_FRESH_STATE: dict[str, Callable[[], Any]] = {
    "_last_tool_calls": list,
    "_last_usage": dict,
    "_last_assistant_parts": list,
    "_last_assistant_extra_fields": dict,
    "_emergency_drop_callbacks": list,
    "_context_repair_callbacks": list,
    "_last_request_measure": lambda: None,
}


class BaseLLMProvider:
    """Provide shared chat, normalization, and overflow-recovery behavior."""

    # An empty name deliberately prevents provider-native tool compatibility.
    provider_name: str = ""

    # A fork uses its origin's SDK client; closing the fork leaves it open.
    _borrowed_client: bool = False

    # Configured whole-request byte target for inline images; ``None`` disables it.
    _request_max_bytes: int | None = None
    _last_request_measure: RequestMeasure | None = None

    # Supported native tools auto-register unless the creature explicitly disables them.
    provider_native_tools: frozenset[str] = frozenset()

    def __init__(self, config: LLMConfig | None = None):
        self.config = config or LLMConfig(model="")
        self._last_tool_calls: list[NativeToolCall] = []
        self._emergency_drop_callbacks: list[Callable[[list[dict[str, Any]]], None]] = (
            []
        )
        self._context_repair_callbacks: list[Callable[[ContextRepair], None]] = []
        # Host compaction gets one chance to shrink context before destructive recovery.
        self._overflow_rescue: Callable[[], Any] | None = None
        self._request_ceiling = RequestCeiling()

    def fork(self) -> "BaseLLMProvider":
        """Return a sibling for an independent conversation.

        The fork shares model, credentials and HTTP client, and owns its
        transport session, per-turn result state and host hooks (none until
        its host registers them), so concurrent conversations never see each
        other's tool calls, continuation state or recovery callbacks.
        """
        clone = copy.copy(self)
        for name, factory in _FORK_FRESH_STATE.items():
            setattr(clone, name, factory())
        clone._overflow_rescue = None
        clone._borrowed_client = True
        for name in ("extra_body", "_extra_headers"):
            value = getattr(self, name, None)
            if isinstance(value, dict):
                setattr(clone, name, dict(value))
        clone._reset_fork_state()
        return clone

    def _reset_fork_state(self) -> None:
        """Drop transport state a fork must not share; providers with sessions override."""

    async def _try_overflow_rescue(
        self, current: list[dict[str, Any]] | None = None
    ) -> list[dict[str, Any]] | None:
        """Request compacted context, rejecting rescues that do not shrink it."""
        rescue = getattr(self, "_overflow_rescue", None)
        if rescue is None:
            return None
        try:
            rescued = await rescue()
        except Exception as e:
            logger.warning("overflow rescue hook failed", error=str(e), exc_info=True)
            return None
        if not rescued:
            return None
        if current is not None and len(rescued) >= len(current):
            logger.warning(
                "overflow rescue did not shrink the conversation; ignoring",
                rescued_messages=len(rescued),
                current_messages=len(current),
            )
            return None
        logger.warning("provider_overflow_rescued", messages=len(rescued))
        return list(rescued)

    async def _recover_from_overflow(
        self,
        current: list[dict[str, Any]],
        state: "OverflowRecoveryState",
    ) -> list[dict[str, Any]] | None:
        """Try host compaction, then one emergency tool-round drop."""
        if not state.rescue_attempted:
            state.rescue_attempted = True
            rescued = await self._try_overflow_rescue(current)
            if rescued is not None:
                return rescued
        if not state.drop_attempted:
            state.drop_attempted = True
            dropped, recovered = drop_last_tool_round(current)
            if dropped:
                self._notify_emergency_drop(recovered)
                logger.warning(
                    "provider_emergency_drop",
                    dropped=dropped,
                    recovered_messages=len(recovered),
                )
                return recovered
        return None

    async def _recover_context(
        self,
        cls: ErrorClass,
        exc: BaseException,
        current: list[dict[str, Any]],
        state: OverflowRecoveryState,
    ) -> list[dict[str, Any]] | None:
        """Return a reduced request for an overflow, oversize or content rejection.

        ``None`` means no stage is left and the error should be raised.
        """
        if cls is ErrorClass.OVERFLOW:
            return await self._recover_from_overflow(current, state)
        if cls is ErrorClass.OVERSIZE:
            return await self._recover_from_oversize(current, state, str(exc))
        if cls is ErrorClass.CONTENT:
            return self._recover_from_content_error(current, state, str(exc))
        return None

    def request_ceiling(self) -> RequestCeiling:
        """The learned byte ceiling this provider shares with its forks and siblings."""
        return self.__dict__.setdefault("_request_ceiling", RequestCeiling())

    def share_request_ceiling(self, other: "BaseLLMProvider") -> None:
        """Make ``other`` (a sibling on the same endpoint) use this provider's ceiling."""
        other._request_ceiling = self.request_ceiling()

    def _request_byte_target(self) -> int | None:
        """The byte target the next request's images are compressed toward."""
        return effective_target(
            self._request_max_bytes, self.request_ceiling().max_bytes
        )

    def _fit_request_sync(
        self, body: dict[str, Any], provider: str = ""
    ) -> dict[str, Any]:
        """Fit ``body`` to the byte target and record its size; blocking, CPU only."""
        fitted, measure = fit_request(
            body, self._request_byte_target(), provider=provider
        )
        self._last_request_measure = measure
        return fitted

    async def _fit_request(
        self, body: dict[str, Any], provider: str = ""
    ) -> dict[str, Any]:
        """Fit ``body`` to the byte target off the event loop and record its size."""
        return await asyncio.to_thread(self._fit_request_sync, body, provider)

    def _shrink_request_budget(self, state: OverflowRecoveryState) -> bool:
        """Lower the learned ceiling below the refused request when a refit can help.

        It helps only while the request carries images (or was never scanned)
        and each refit actually came out smaller than the last refused one.
        """
        measure = self._last_request_measure
        if (
            measure is None
            or measure.image_count == 0
            or state.shrinks >= MAX_REQUEST_SHRINKS
            or (
                state.refused_bytes is not None and measure.bytes >= state.refused_bytes
            )
        ):
            return False
        state.refused_bytes = measure.bytes
        ceiling = self.request_ceiling().lower(measure.bytes)
        if ceiling >= measure.bytes:
            return False
        state.shrinks += 1
        logger.warning(
            "provider_request_ceiling",
            refused_bytes=measure.bytes,
            ceiling_bytes=ceiling,
            attempt=state.shrinks,
        )
        return True

    async def _recover_from_oversize(
        self,
        current: list[dict[str, Any]],
        state: OverflowRecoveryState,
        reason: str,
    ) -> list[dict[str, Any]] | None:
        """Refit images under a lower ceiling, then repair content, then drop context."""
        if self._shrink_request_budget(state):
            return list(current)
        repaired = self._recover_from_content_error(current, state, reason)
        if repaired is not None:
            return repaired
        return await self._recover_from_overflow(current, state)

    def _recover_from_content_error(
        self,
        current: list[dict[str, Any]],
        state: OverflowRecoveryState,
        reason: str,
    ) -> list[dict[str, Any]] | None:
        """Walk the repair ladder: newest media, all media, newest tool round.

        Each stage runs at most once per request. The first stage that changes
        the request is reported to the host so its conversation drops the same
        content; the retry then carries the note instead of the refused part.
        """
        while state.content_stage < len(CONTENT_REPAIR_LADDER):
            kind, scope = CONTENT_REPAIR_LADDER[state.content_stage]
            state.content_stage += 1
            repair = ContextRepair(kind=kind, scope=scope, reason=reason)
            changed, repaired = repair.apply(current)
            if not changed:
                continue
            logger.warning(
                "provider_content_repair",
                kind=kind,
                scope=scope,
                changed=changed,
                error=reason[:300],
            )
            self._notify_context_repair(repair)
            return repaired
        return None

    def on_context_repair(self, callback: Callable[[ContextRepair], None]) -> None:
        """Register a callback that replays a content repair on the host conversation."""
        self.__dict__.setdefault("_context_repair_callbacks", []).append(callback)

    def _notify_context_repair(self, repair: ContextRepair) -> None:
        for callback in list(getattr(self, "_context_repair_callbacks", ())):
            try:
                callback(repair)
            except Exception as exc:  # pragma: no cover - callbacks are external
                logger.warning(
                    "Context-repair callback failed", error=str(exc), exc_info=True
                )

    @property
    def last_tool_calls(self) -> list[NativeToolCall]:
        """Tool calls from the last streaming response (native mode only)."""
        return self._last_tool_calls

    @property
    def last_usage(self) -> dict[str, int]:
        """Return provider-specific token usage from the last completion."""
        return getattr(self, "_last_usage", {})

    @property
    def last_assistant_content_parts(self) -> list[Any] | None:
        """Return structured content from the last turn, or ``None`` for text-only."""
        return getattr(self, "_last_assistant_parts", None) or None

    @property
    def last_assistant_extra_fields(self) -> dict[str, Any]:
        """Return provider fields that must round-trip with the assistant message."""
        return getattr(self, "_last_assistant_extra_fields", {}) or {}

    def reload_credentials(self) -> bool:
        """Rebuild provider credentials when supported, reporting whether they changed."""
        return False

    def translate_provider_native_tool(self, tool: Any) -> dict | None:
        """Return a provider-native wire schema, or ``None`` if unsupported."""
        return None

    def on_emergency_drop(
        self, callback: Callable[[list[dict[str, Any]]], None]
    ) -> None:
        """Register a callback invoked with recovered messages after a drop."""
        self._emergency_drop_callbacks.append(callback)

    def _notify_emergency_drop(self, messages: list[dict[str, Any]]) -> None:
        """Notify callbacks that provider-side recovery changed context."""
        for callback in list(self._emergency_drop_callbacks):
            try:
                callback(messages)
            except Exception as exc:  # pragma: no cover - callbacks are external
                logger.warning(
                    "Emergency-drop callback failed", error=str(exc), exc_info=True
                )

    def with_model(self, name: str) -> "BaseLLMProvider":
        """Return a sibling provider configured for ``name``.

        Providers with external clients should override this to preserve
        connection pools. The base implementation only supports no-op reuse.
        """
        if not name or name == self.config.model:
            return self
        raise ValueError(f"Provider {type(self).__name__} cannot switch to {name}")

    def _normalize_messages(
        self,
        messages: list[Message] | list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Convert messages to API format."""
        if not messages:
            return []

        if isinstance(messages[0], dict):
            return messages  # type: ignore

        return [msg.to_dict() for msg in messages]  # type: ignore

    async def chat(
        self,
        messages: list[Message] | list[dict[str, Any]],
        *,
        stream: bool = True,
        tools: list[ToolSchema] | None = None,
        provider_native_tools: list[Any] | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[str]:
        """Normalize messages and delegate streaming or completion to the provider."""
        self._last_tool_calls = []
        normalized = self._normalize_messages(messages)

        if stream:
            provider_stream = self._stream_chat(
                normalized,
                tools=tools,
                provider_native_tools=provider_native_tools,
                **kwargs,
            )
            try:
                async for chunk in provider_stream:
                    yield chunk
            finally:
                close = getattr(provider_stream, "aclose", None)
                if close is not None:
                    await close()
        else:
            response = await self._complete_chat(normalized, **kwargs)
            yield response.content

    async def chat_complete(
        self,
        messages: list[Message] | list[dict[str, Any]],
        **kwargs: Any,
    ) -> ChatResponse:
        """Default complete implementation."""
        normalized = self._normalize_messages(messages)
        return await self._complete_chat(normalized, **kwargs)

    async def _stream_chat(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[ToolSchema] | None = None,
        provider_native_tools: list[Any] | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[str]:
        """Stream provider output; subclasses must implement this method."""
        raise NotImplementedError("Subclass must implement _stream_chat")
        yield  # Preserve the async-generator contract for type checkers.

    async def _complete_chat(
        self,
        messages: list[dict[str, Any]],
        **kwargs: Any,
    ) -> ChatResponse:
        """Return a complete provider response; subclasses must implement this method."""
        raise NotImplementedError("Subclass must implement _complete_chat")
