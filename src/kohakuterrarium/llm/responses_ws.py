"""Persistent Responses-API WebSocket session with incremental continuation.

One session owns one connection (one in-flight response at a time). Turns
continue from ``previous_response_id`` with delta-only input while the
caller's item list extends what the server already holds; any history edit,
HTTP-path detour, or failed turn falls back to a full resend.
"""

import asyncio
import json
from contextlib import aclosing
from copy import deepcopy
from typing import Any, AsyncIterator, Callable

from kohakuterrarium.llm.recovery import RetryPolicy
from kohakuterrarium.llm.responses_ws_recovery import (
    ResponsesWSError,
    WSRecovery,
    event_field,
    oversize_close,
)
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

# Consecutive WS turns abandoned for HTTP before WS is disabled for a conversation.
DEFAULT_FALLBACK_AFTER = 3


def event_bytes(event: dict[str, Any]) -> int:
    """Encoded size of a client event as the SDK serializes it."""
    return len(json.dumps(event).encode())


# Longest a waiting close() blocks on handshakes; unfinished ones stay owned.
SHUTDOWN_CLOSE_TIMEOUT = 10.0

# Hosted tools accepted only on a connection whose first request declared them.
CONNECTION_GATED_TOOLS = frozenset({"image_generation"})


def gated_tool_types(base_event: dict[str, Any]) -> frozenset[str]:
    """Return the connection-gated hosted tool types a request declares."""
    return frozenset(
        tool.get("type")
        for tool in base_event.get("tools") or ()
        if isinstance(tool, dict) and tool.get("type") in CONNECTION_GATED_TOOLS
    )


def rejects_hosted_tool(code: str, message: str) -> bool:
    """Whether an error says the connection cannot serve a declared hosted tool."""
    return code == "unsupported_parameter" and "hosted tool" in message.lower()


class ResponsesWSSession:
    """Connection + continuation state for Responses WebSocket mode."""

    def __init__(
        self,
        connect_factory: Callable[[], Any],
        *,
        max_message_bytes: int | None = None,
        fallback_after: int = DEFAULT_FALLBACK_AFTER,
    ) -> None:
        self._connect_factory = connect_factory
        # Largest event the server accepts; set by config or learned from a 1009.
        self.max_message_bytes = max_message_bytes
        self._fallback_after = fallback_after
        self._fallbacks = 0
        self._manager: Any = None
        self._connection: Any = None
        self._lock = asyncio.Lock()
        self._prev_id: str | None = None
        self._sent_items: list[dict[str, Any]] = []
        self._assistant_echo: list[dict[str, Any]] | None = None
        # Gated tools the current connection's first request declared; None before it.
        self._connection_tools: frozenset[str] | None = None
        # Close handshakes still running, kept alive past a cancelled caller.
        self._closing: set[asyncio.Task] = set()

    @property
    def busy(self) -> bool:
        """Whether a turn is in flight (one response per connection)."""
        return self._lock.locked()

    @property
    def disabled(self) -> bool:
        """Whether repeated transport failures moved this conversation to HTTP."""
        return 0 < self._fallback_after <= self._fallbacks

    def note_http_fallback(self) -> None:
        """Count a WS turn abandoned for HTTP; enough in a row disable WS here."""
        self._fallbacks += 1
        if self.disabled:
            logger.warning(
                "Responses WS disabled for this conversation; using HTTP",
                consecutive_failures=self._fallbacks,
            )

    def invalidate(self) -> None:
        """Drop continuation state so the next turn resends the full input.

        Must be called whenever a turn bypasses this session (HTTP fallback),
        because the connection-local cache then lags the real conversation.
        """
        self._prev_id = None
        self._sent_items = []
        self._assistant_echo = None

    def record_assistant_echo(self, items: list[dict[str, Any]]) -> None:
        """Snapshot the provider's exact conversation projection of its output."""
        if self._prev_id is not None:
            self._assistant_echo = deepcopy(items)

    async def close(self, *, grace: float | None = None) -> None:
        """Close the connection, reset all state, and await pending handshakes.

        Handshakes run as session-owned tasks, so a caller that stops waiting
        (``grace`` elapsed, or cancellation) never orphans one; the wait is
        capped at ``grace`` seconds, or ``SHUTDOWN_CLOSE_TIMEOUT`` by default.
        """
        self.invalidate()
        connection = self._connection
        self._connection = None
        self._manager = None
        self._connection_tools = None
        if connection is not None:
            task = asyncio.create_task(self._close_connection(connection))
            self._closing.add(task)
            task.add_done_callback(self._closing.discard)
        if self._closing:
            limit = SHUTDOWN_CLOSE_TIMEOUT if grace is None else grace
            _, pending = await asyncio.shield(
                asyncio.wait(set(self._closing), timeout=limit)
            )
            if pending:
                logger.debug("Responses WS close still pending", pending=len(pending))

    async def _close_connection(self, connection: Any) -> None:
        """Run one close handshake while draining the receive queue."""
        receive = getattr(connection, "recv_bytes", None)
        drain = (
            asyncio.create_task(self._discard_during_close(receive))
            if callable(receive)
            else None
        )
        try:
            await connection.close()
        except Exception:
            logger.debug("Responses WS close failed", exc_info=True)
        finally:
            if drain is not None:
                drain.cancel()
                await asyncio.gather(drain, return_exceptions=True)

    @staticmethod
    async def _discard_during_close(receive: Callable[[], Any]) -> None:
        """Keep bounded SDK receive queues moving until the close handshake ends."""
        try:
            while True:
                await receive()
        except Exception:
            # EOF or another active reader: the closing task owns the outcome.
            pass

    async def stream_turn(
        self,
        base_event: dict[str, Any],
        items: list[dict[str, Any]],
        pairing_fix: Callable[[list[dict[str, Any]]], list[dict[str, Any]]],
        *,
        recovery: WSRecovery | None = None,
        reset_attempt: Callable[[], None] | None = None,
    ) -> AsyncIterator[Any]:
        """Run a request with isolated attempts and exclusive continuation state."""
        recovery = recovery or WSRecovery(
            RetryPolicy(max_retries=1, base_delay=0), raw_delivery=True
        )
        async with self._lock:
            async with aclosing(
                recovery.run(self, base_event, items, pairing_fix, reset_attempt)
            ) as stream:
                async for event in stream:
                    yield event

    async def _run_turn(
        self,
        base_event: dict[str, Any],
        items: list[dict[str, Any]],
        pairing_fix: Callable[[list[dict[str, Any]]], list[dict[str, Any]]],
        delta: list[dict[str, Any]] | None,
        recovery: WSRecovery,
    ) -> AsyncIterator[Any]:
        required = gated_tool_types(base_event)
        try:
            connection = await self._ensure_connection()
            if self._connection_tools is not None and not (
                required <= self._connection_tools
            ):
                logger.info(
                    "Responses WS reconnecting for hosted tools",
                    required=sorted(required),
                    connection_tools=sorted(self._connection_tools),
                )
                await self.close()
                connection = await self._ensure_connection()
        except Exception as exc:
            raise ResponsesWSError(
                str(exc), mid_stream=False, transport=True, submitted=False
            ) from exc
        if self._prev_id is None:
            delta = None
        event: dict[str, Any] = {"type": "response.create", **base_event}
        if delta is not None:
            event["previous_response_id"] = self._prev_id
            event["input"] = delta
        else:
            event["input"] = pairing_fix(list(items))
        if self.max_message_bytes is not None:
            size = event_bytes(event)
            if size > self.max_message_bytes:
                raise ResponsesWSError(
                    f"Responses WS event is {size} bytes, over the "
                    f"{self.max_message_bytes}-byte frame ceiling",
                    mid_stream=False,
                    submitted=False,
                    oversize=True,
                )
        recovery.record_submission()
        try:
            await connection.send(event)
        except Exception as exc:
            self._raise_if_oversize(exc, event, yielded=False)
            detail = exc
            if type(exc).__name__ == "WebSocketQueueFullError":
                cause = exc.__cause__
                if cause is None:
                    cause = exc.__context__
                if cause is not None:
                    detail = cause
            raise ResponsesWSError(
                str(detail) or type(detail).__name__, mid_stream=False, transport=True
            ) from exc
        if self._connection_tools is None:
            self._connection_tools = required

        yielded = False
        last_event_type = ""
        iterator = connection.__aiter__()
        while True:
            try:
                server_event = await iterator.__anext__()
            except StopAsyncIteration:
                raise ResponsesWSError(
                    "Responses WS connection closed before completion",
                    mid_stream=yielded,
                    transport=True,
                    last_event_type=last_event_type,
                )
            except Exception as exc:
                self._raise_if_oversize(exc, event, yielded=yielded)
                # Mid-turn transport failures must not trigger a resend that
                # would duplicate already-yielded output.
                raise ResponsesWSError(
                    str(exc),
                    mid_stream=yielded,
                    transport=True,
                    last_event_type=last_event_type,
                ) from exc
            etype = event_field(server_event, "type") or ""
            last_event_type = etype
            if etype in ("response.failed", "response.incomplete"):
                self.invalidate()
                response = event_field(server_event, "response")
                detail = event_field(response, "error") or event_field(
                    response, "incomplete_details"
                )
                code = (
                    event_field(detail, "code") or event_field(detail, "reason") or ""
                )
                message = event_field(detail, "message") or code
                raise ResponsesWSError(
                    f"{etype}: {message}",
                    mid_stream=yielded,
                    code=code,
                    status_code=event_field(detail, "status")
                    or event_field(server_event, "status"),
                    raw_event=server_event,
                    last_event_type=etype,
                    retire_connection=rejects_hosted_tool(code, message),
                    capability_mismatch=rejects_hosted_tool(code, message),
                )
            if etype == "error":
                self._handle_error_event(server_event, delta, yielded)
            yielded = True
            yield server_event
            if etype == "response.completed":
                self._record_completed(server_event, items)
                return

    def _raise_if_oversize(
        self, exc: BaseException, event: dict[str, Any], *, yielded: bool
    ) -> None:
        """Turn a 1009 close into an oversize error and lower the frame ceiling."""
        if not oversize_close(exc):
            return
        size = event_bytes(event)
        ceiling = size - 1
        if self.max_message_bytes is not None:
            ceiling = min(ceiling, self.max_message_bytes)
        self.max_message_bytes = ceiling
        self.invalidate()
        raise ResponsesWSError(
            f"Responses WS server refused a {size}-byte event (1009 message too big)",
            mid_stream=yielded,
            transport=True,
            submitted=False,
            oversize=True,
        ) from exc

    def _handle_error_event(
        self,
        server_event: Any,
        delta: list[dict[str, Any]] | None,
        yielded: bool,
    ) -> None:
        error = event_field(server_event, "error")
        fields = {
            key: event_field(error, key) or event_field(server_event, key)
            for key in ("code", "message", "status")
        }
        code = fields["code"] or ""
        message = fields["message"] or "Responses WebSocket request failed"
        self.invalidate()
        raise ResponsesWSError(
            f"{code}: {message}",
            mid_stream=yielded,
            code=code,
            status_code=fields["status"],
            raw_event=server_event,
            last_event_type="error",
            retire_connection=code == "websocket_connection_limit_reached"
            or rejects_hosted_tool(code, message),
            cache_miss=delta is not None and code == "previous_response_not_found",
            capability_mismatch=rejects_hosted_tool(code, message),
        )

    async def _ensure_connection(self) -> Any:
        if self._connection is not None:
            socket = getattr(self._connection, "_connection", self._connection)
            state = getattr(socket, "state", None)
            if getattr(state, "name", None) in ("CLOSING", "CLOSED") or (
                getattr(socket, "closed", False) is True
            ):
                await self.close()
        if self._connection is None:
            self._manager = self._connect_factory()
            self._connection = await self._manager.enter()
            self._connection_tools = None
            self.invalidate()
        return self._connection

    def _compute_delta(
        self, items: list[dict[str, Any]]
    ) -> list[dict[str, Any]] | None:
        """Return the not-yet-server-known suffix, or ``None`` for full resend."""
        sent = self._sent_items
        echo = self._assistant_echo
        if not self._prev_id or echo is None or len(items) <= len(sent) + len(echo):
            return None
        if items[: len(sent)] != sent:
            return None
        if items[len(sent) : len(sent) + len(echo)] != echo:
            return None
        return list(items[len(sent) + len(echo) :])

    def _record_completed(self, server_event: Any, items: list[dict[str, Any]]) -> None:
        response = getattr(server_event, "response", None)
        response_id = getattr(response, "id", None)
        if not response_id:
            self.invalidate()
            return
        self._prev_id = response_id
        self._sent_items = deepcopy(items)
        self._assistant_echo = None
        self._fallbacks = 0
