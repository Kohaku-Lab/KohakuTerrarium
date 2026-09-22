"""Persistent Responses-API WebSocket session with incremental continuation.

One session owns one connection (one in-flight response at a time). Turns
continue from ``previous_response_id`` with delta-only input while the
caller's item list extends what the server already holds; any history edit,
HTTP-path detour, or failed turn falls back to a full resend.
"""

import asyncio
from copy import deepcopy
from typing import Any, AsyncIterator, Callable

from websockets.exceptions import ConnectionClosed

from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


def _retryable_close(error: BaseException) -> bool:
    """Check received and sent close codes through SDK exception wrappers."""
    seen: set[int] = set()
    while id(error) not in seen:
        seen.add(id(error))
        if isinstance(error, ConnectionClosed):
            return all(
                frame is None or frame.code in {1000, 1001, 1011, 1012, 1013, 1014}
                for frame in (error.rcvd, error.sent)
            )
        cause = error.__cause__ or error.__context__
        if cause is None:
            break
        error = cause
    return True


class ResponsesWSError(Exception):
    """Raised when a WebSocket turn cannot complete.

    ``submitted`` records any send attempt across this turn's connection
    attempts, including when the final reconnect fails before sending.
    """

    def __init__(
        self,
        message: str,
        *,
        mid_stream: bool,
        transport: bool = False,
        submitted: bool = True,
    ) -> None:
        super().__init__(message)
        self.mid_stream = mid_stream
        self.submitted = submitted
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
        self._assistant_echo: list[dict[str, Any]] | None = None

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
        self._assistant_echo = None

    def record_assistant_echo(self, items: list[dict[str, Any]]) -> None:
        """Snapshot the provider's exact conversation projection of its output."""
        if self._prev_id is not None:
            self._assistant_echo = deepcopy(items)

    async def close(self) -> None:
        """Close the connection and reset all state."""
        self.invalidate()
        connection = self._connection
        self._connection = None
        self._manager = None
        if connection is not None:
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
    ) -> AsyncIterator[Any]:
        """Run one turn, yielding raw server events until ``response.completed``.

        ``base_event`` carries everything but ``type`` / ``input`` /
        ``previous_response_id``; ``pairing_fix`` is applied only on full
        resends (a delta must never gain synthetic outputs or drop orphans).
        """
        async with self._lock:
            delta = self._compute_delta(items)
            submitted = False
            replayable = not base_event.get("background") and all(
                isinstance(tool, dict) and tool.get("type") == "function"
                for tool in base_event.get("tools") or []
            )
            for attempt in range(2):
                try:
                    async for event in self._run_turn(
                        base_event, items, pairing_fix, delta if attempt == 0 else None
                    ):
                        yield event
                    return
                except (asyncio.CancelledError, GeneratorExit):
                    await self.close()
                    raise
                except ResponsesWSError as exc:
                    submitted = submitted or exc.submitted
                    exc.submitted = submitted
                    if exc.transport:
                        await self.close()
                    if (
                        attempt
                        or exc.mid_stream
                        or not exc.transport
                        or (submitted and not replayable)
                        or not _retryable_close(exc)
                    ):
                        raise
                    logger.warning(
                        "Responses WS transport failed before the first event, reconnecting once",
                        submission_uncertain=submitted,
                        error=str(exc),
                    )

    async def _run_turn(
        self,
        base_event: dict[str, Any],
        items: list[dict[str, Any]],
        pairing_fix: Callable[[list[dict[str, Any]]], list[dict[str, Any]]],
        delta: list[dict[str, Any]] | None,
    ) -> AsyncIterator[Any]:
        try:
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
        try:
            await connection.send(event)
        except Exception as exc:
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
            if etype in ("response.failed", "response.incomplete"):
                self.invalidate()
                response = getattr(server_event, "response", None)
                detail = getattr(response, "error", None) or getattr(
                    response, "incomplete_details", None
                )
                message = getattr(detail, "message", None) or getattr(
                    detail, "reason", ""
                )
                raise ResponsesWSError(f"{etype}: {message}", mid_stream=yielded)
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
