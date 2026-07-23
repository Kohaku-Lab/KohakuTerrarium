"""Persistent Responses-API WebSocket session with incremental continuation.

One session owns one connection (one in-flight response at a time). Turns
continue from ``previous_response_id`` with delta-only input while the
caller's item list extends what the server already holds; any history edit,
HTTP-path detour, or failed turn falls back to a full resend.
"""

import asyncio
from typing import Any, AsyncIterator, Callable

from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


class ResponsesWSError(Exception):
    """Raised when a WebSocket turn cannot complete.

    ``mid_stream`` distinguishes failures after events already reached the
    caller (must propagate) from failures before any output (safe to fall
    back to the HTTP transport).
    """

    def __init__(
        self, message: str, *, mid_stream: bool, transport: bool = False
    ) -> None:
        super().__init__(message)
        self.mid_stream = mid_stream
        # Transport failures leave the connection unusable; server error
        # events arrive on a healthy connection.
        self.transport = transport


class ResponsesWSSession:
    """Connection + continuation state for Responses WebSocket mode."""

    def __init__(self, connect_factory: Callable[[], Any]) -> None:
        self._connect_factory = connect_factory
        self._manager: Any = None
        self._connection: Any = None
        self._lock = asyncio.Lock()
        self._prev_id: str | None = None
        self._sent_items: list[dict[str, Any]] = []
        self._last_call_ids: set[str] = set()

    @property
    def busy(self) -> bool:
        """Whether a turn is in flight (one response per connection)."""
        return self._lock.locked()

    def invalidate(self) -> None:
        """Drop continuation state so the next turn resends the full input.

        Must be called whenever a turn bypasses this session (HTTP fallback),
        because the connection-local cache then lags the real conversation.
        """
        self._prev_id = None
        self._sent_items = []
        self._last_call_ids = set()

    async def close(self) -> None:
        """Close the connection and reset all state."""
        self.invalidate()
        connection = self._connection
        self._connection = None
        self._manager = None
        if connection is not None:
            try:
                await connection.close()
            except Exception:
                logger.debug("Responses WS close failed", exc_info=True)

    async def stream_turn(
        self,
        base_event: dict[str, Any],
        items: list[dict[str, Any]],
        pairing_fix: Callable[[list[dict[str, Any]]], list[dict[str, Any]]],
    ) -> AsyncIterator[Any]:
        """Run one turn, yielding raw server events until ``response.completed``.

        ``base_event`` carries everything but ``type`` / ``input`` /
        ``previous_response_id``; ``pairing_fix`` is applied only on full
        resends (a delta must never gain synthetic outputs or drop orphans).
        """
        async with self._lock:
            delta = self._compute_delta(items)
            try:
                async for event in self._run_turn(
                    base_event, items, pairing_fix, delta
                ):
                    yield event
                return
            except ResponsesWSError as ws_exc:
                if ws_exc.transport:
                    await self.close()
                # Server error events and mid-stream failures surface to the
                # caller; only a pre-output transport failure earns a retry.
                if ws_exc.mid_stream or not ws_exc.transport:
                    raise
                failure: Exception = ws_exc
            except Exception as exc:
                # Establishment/send failures always happen before output.
                await self.close()
                failure = exc

            logger.warning(
                "Responses WS turn failed, retrying on a fresh connection",
                error=str(failure),
            )
            try:
                async for event in self._run_turn(base_event, items, pairing_fix, None):
                    yield event
            except ResponsesWSError as retry_exc:
                if retry_exc.transport:
                    await self.close()
                raise
            except Exception as retry_exc:
                await self.close()
                raise ResponsesWSError(str(retry_exc), mid_stream=False) from retry_exc

    async def _run_turn(
        self,
        base_event: dict[str, Any],
        items: list[dict[str, Any]],
        pairing_fix: Callable[[list[dict[str, Any]]], list[dict[str, Any]]],
        delta: list[dict[str, Any]] | None,
    ) -> AsyncIterator[Any]:
        connection = await self._ensure_connection()
        event: dict[str, Any] = {"type": "response.create", **base_event}
        if delta is not None:
            event["previous_response_id"] = self._prev_id
            event["input"] = delta
        else:
            event["input"] = pairing_fix(list(items))
        await connection.send(event)

        yielded = False
        iterator = connection.__aiter__()
        while True:
            try:
                server_event = await iterator.__anext__()
            except StopAsyncIteration:
                raise ResponsesWSError(
                    "Responses WS connection closed before completion",
                    mid_stream=yielded,
                    transport=True,
                )
            except Exception as exc:
                # Mid-turn transport failures must not trigger a resend that
                # would duplicate already-yielded output.
                raise ResponsesWSError(
                    str(exc), mid_stream=yielded, transport=True
                ) from exc
            etype = getattr(server_event, "type", "")
            if etype == "error":
                async for retry_event in self._handle_error_event(
                    server_event, base_event, items, pairing_fix, delta, yielded
                ):
                    yield retry_event
                return
            yielded = True
            yield server_event
            if etype == "response.completed":
                self._record_completed(server_event, items)
                return

    async def _handle_error_event(
        self,
        server_event: Any,
        base_event: dict[str, Any],
        items: list[dict[str, Any]],
        pairing_fix: Callable[[list[dict[str, Any]]], list[dict[str, Any]]],
        delta: list[dict[str, Any]] | None,
        yielded: bool,
    ) -> AsyncIterator[Any]:
        error = getattr(server_event, "error", None)
        code = getattr(error, "code", "") or ""
        message = getattr(error, "message", "") or str(server_event)
        # A failed turn evicts the referenced response from the server cache.
        self.invalidate()
        if delta is not None and code == "previous_response_not_found" and not yielded:
            logger.warning("Responses WS cache miss, resending full input")
            async for event in self._run_turn(base_event, items, pairing_fix, None):
                yield event
            return
        raise ResponsesWSError(f"{code}: {message}", mid_stream=yielded)

    async def _ensure_connection(self) -> Any:
        if self._connection is None:
            self._manager = self._connect_factory()
            self._connection = await self._manager.enter()
            self.invalidate()
        return self._connection

    def _compute_delta(
        self, items: list[dict[str, Any]]
    ) -> list[dict[str, Any]] | None:
        """Return the not-yet-server-known suffix, or ``None`` for full resend."""
        sent = self._sent_items
        if not self._prev_id or len(items) <= len(sent):
            return None
        if items[: len(sent)] != sent:
            return None
        delta = list(items[len(sent) :])
        # The server already holds its own generated items: skip the
        # conversation's echo of the last response (assistant messages and
        # the function_call items whose outputs we are about to send).
        while delta:
            head = delta[0]
            if (
                head.get("type") == "function_call"
                and head.get("call_id") in self._last_call_ids
            ):
                delta.pop(0)
                continue
            if head.get("role") == "assistant":
                delta.pop(0)
                continue
            break
        if not delta:
            return None
        return delta

    def _record_completed(self, server_event: Any, items: list[dict[str, Any]]) -> None:
        response = getattr(server_event, "response", None)
        response_id = getattr(response, "id", None)
        if not response_id:
            self.invalidate()
            return
        self._prev_id = response_id
        self._sent_items = list(items)
        call_ids: set[str] = set()
        for output_item in getattr(response, "output", None) or []:
            if getattr(output_item, "type", "") == "function_call":
                call_id = getattr(output_item, "call_id", "")
                if call_id:
                    call_ids.add(call_id)
        self._last_call_ids = call_ids
