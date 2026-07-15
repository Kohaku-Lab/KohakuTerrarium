"""Typed single-turn drivers for agents.

Provides single-turn result and event-stream APIs without consuming output
intended for other sinks. Timed-out turns are interrupted rather than abandoned.
"""

import asyncio
import time
from collections.abc import AsyncIterator
from typing import Any

from kohakuterrarium.errors import AgentNotRunningError, TurnError, TurnTimeoutError
from kohakuterrarium.core.event_inbox import EventEnvelope, TurnOutcome
from kohakuterrarium.core.events import TriggerEvent, create_user_input_event
from kohakuterrarium.core.turn import TurnCapture, TurnEnded, TurnEvent, TurnResult
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

# Grace period for the interrupted turn to unwind after a timeout.
_INTERRUPT_GRACE_S = 10.0


def _event_correlation(event: TriggerEvent) -> str | None:
    """Pull the delivery/correlation id an ingress adapter put in context."""
    context = getattr(event, "context", None) or {}
    value = context.get("delivery_id") or context.get("correlation_id")
    return value if isinstance(value, str) else None


class AgentTurnMixin:
    """Mixin providing :meth:`run` and :meth:`run_stream`."""

    async def run(
        self,
        content: Any,
        *,
        timeout: float | None = None,
        source: str = "programmatic",
        raise_on_error: bool = True,
    ) -> TurnResult:
        """Drive one full turn and return its :class:`TurnResult`.

        Args:
            content: User input (str or multimodal content parts).
            timeout: Seconds before the turn is interrupted and
                cancelled.  ``None`` = no limit.
            source: Recorded as the input's source tag.
            raise_on_error: Raise :class:`TurnError` /
                :class:`TurnTimeoutError` on failure (default).  Pass
                ``False`` to always get the result back and branch on
                ``result.status`` yourself.
        """
        capture = TurnCapture()
        result = await self._drive_turn(
            content, capture, timeout=timeout, source=source
        )
        if raise_on_error:
            if result.status == "timeout":
                raise TurnTimeoutError(
                    f"turn exceeded timeout={timeout}s "
                    f"(captured {len(result.text)} chars before interrupt)"
                )
            if result.status == "error":
                raise TurnError(result.error or "turn failed")
        return result

    async def run_stream(
        self,
        content: Any,
        *,
        timeout: float | None = None,
        source: str = "programmatic",
    ) -> AsyncIterator[TurnEvent]:
        """Drive one turn, yielding typed events live.

        Yields :class:`TextChunk` / :class:`Activity` events as they
        happen and a final ``TurnEnded(result)``.  Errors surface as
        ``Activity(kind="processing_error")`` events and in the final
        result — iteration itself never raises mid-stream.
        """
        queue: "asyncio.Queue[TurnEvent]" = asyncio.Queue()
        capture = TurnCapture(queue=queue)

        async def _runner() -> TurnResult:
            return await self._drive_turn(
                content, capture, timeout=timeout, source=source
            )

        task = asyncio.create_task(_runner())
        try:
            while True:
                getter = asyncio.create_task(queue.get())
                done, _pending = await asyncio.wait(
                    {getter, task}, return_when=asyncio.FIRST_COMPLETED
                )
                if getter in done:
                    yield getter.result()
                    continue
                # Turn finished — drain whatever is left, then close.
                getter.cancel()
                while not queue.empty():
                    yield queue.get_nowait()
                yield TurnEnded(task.result())
                return
        finally:
            if not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass

    async def run_event(
        self,
        event: TriggerEvent,
        *,
        timeout: float | None = None,
        raise_on_error: bool = False,
    ) -> TurnResult:
        """Drive one turn from an existing event.

        The result preserves the event's delivery or correlation ID. Events for
        stopped agents are rejected rather than reported as successful, and
        rejection remains a return value even when ``raise_on_error`` is set.
        """
        correlation = _event_correlation(event)
        if not self._running:
            return TurnResult(status="rejected", correlation_id=correlation)
        # Drive events must wait for the processing lock rather than being
        # dropped or folded into the active turn.
        event.context["await_turn"] = True
        capture = TurnCapture()
        result = await self._drive_turn_event(event, capture, timeout=timeout)
        result.correlation_id = correlation
        result.interrupted_by_user = bool(event.context.get("interrupted_by_user"))
        if raise_on_error:
            if result.status == "timeout":
                raise TurnTimeoutError(
                    f"drive turn exceeded timeout={timeout}s "
                    f"(captured {len(result.text)} chars before interrupt)"
                )
            if result.status == "error":
                raise TurnError(result.error or "drive turn failed")
        return result

    async def _drive_turn(
        self,
        content: Any,
        capture: TurnCapture,
        *,
        timeout: float | None,
        source: str,
    ) -> TurnResult:
        """Build a ``user_input`` event and drive it through the shared body."""
        event = create_user_input_event(content, source=source)
        # Programmatic runs must wait for their own turn instead of becoming
        # feedback for a concurrent turn.
        event.context["await_turn"] = True
        return await self._drive_turn_event(event, capture, timeout=timeout)

    async def _submit_awaiting(
        self, event: TriggerEvent, capture: TurnCapture
    ) -> TurnOutcome:
        """Enqueue an event with its capture and await the consuming turn.

        Capture is scoped to this event's turn. Stopped or warm-paused agents
        reject before enqueueing so callers can retry instead of waiting across
        a pause; strict programmatic input to a stopped agent raises
        :class:`AgentNotRunningError`.
        """
        if not self._running:
            if event.type == "user_input" and getattr(self, "_strict", True):
                raise AgentNotRunningError(
                    f"Agent {self.config.name!r} is not running — "
                    "start() it before injecting input"
                )
            return TurnOutcome(status="rejected", was_primary=False)
        if getattr(self, "_paused", False):
            return TurnOutcome(status="rejected", was_primary=False)
        fut = asyncio.get_running_loop().create_future()
        self._event_inbox.put(EventEnvelope(event, future=fut, capture=capture))
        self._flush_trigger_backlog_stash()
        return await fut

    async def _drive_turn_event(
        self,
        event: TriggerEvent,
        capture: TurnCapture,
        *,
        timeout: float | None,
    ) -> TurnResult:
        """Submit an event with its capture and build the resulting turn.

        The consumer scopes capture to the actual turn so concurrent submissions
        do not record one another's output.
        """
        t0 = time.monotonic()
        status = "ok"
        outcome: TurnOutcome | None = None
        task = asyncio.ensure_future(self._submit_awaiting(event, capture))
        try:
            if timeout is not None:
                try:
                    outcome = await asyncio.wait_for(
                        asyncio.shield(task), timeout=timeout
                    )
                except asyncio.TimeoutError:
                    status = "timeout"
                    # Interrupt the controller loop, then allow bounded cleanup
                    # instead of abandoning work that may keep consuming tokens.
                    self.interrupt()
                    try:
                        outcome = await asyncio.wait_for(
                            task, timeout=_INTERRUPT_GRACE_S
                        )
                    except asyncio.TimeoutError:
                        task.cancel()
                        try:
                            await task
                        except (asyncio.CancelledError, Exception):  # noqa: BLE001
                            pass
                    except Exception as exc:  # noqa: BLE001 - capture as error
                        logger.warning("turn unwind raised", error=str(exc))
            else:
                outcome = await task
        except AgentNotRunningError:
            # Caller misuse, not a turn failure — keep the type.
            raise
        except Exception as exc:
            status = "error"
            if capture.error is None:
                capture.error = str(exc)

        # A rejected outcome means the consumer never ran the event (agent
        # stopped / warm-paused) — surface a rejection rather than a hollow
        # ``ok`` with empty text.
        if outcome is not None and outcome.status == "rejected" and status == "ok":
            status = "rejected"
        return capture.build_result(status, duration_s=time.monotonic() - t0)
