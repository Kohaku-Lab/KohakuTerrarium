"""Unbounded single-consumer ingress queue for agent events.

Queue claims are synchronous and therefore atomic on the event-loop thread.
Enqueueing also wakes the parked consumer, while a pre-park recheck prevents
lost wakeups. Awaited envelopes resolve when their consuming turn ends.
"""

import asyncio
from collections import deque
from dataclasses import dataclass
from typing import Any, Callable

from kohakuterrarium.core.events import TriggerEvent
from kohakuterrarium.core.pending_input import pending_id_of


@dataclass
class TurnOutcome:
    """Describe how an awaited envelope participated in its consuming turn."""

    status: str = "ok"
    was_primary: bool = True
    interrupted_by_user: bool = False


@dataclass
class EventEnvelope:
    """Pair a queued event with optional completion and output capture state."""

    event: TriggerEvent
    future: "asyncio.Future | None" = None
    capture: Any = None


class EventInbox:
    """The single infinite queue drained by the agent's one consumer."""

    def __init__(self) -> None:
        self._dq: deque[EventEnvelope] = deque()
        self._waiter: asyncio.Future | None = None
        self._put_waiter: asyncio.Future | None = None

    def __bool__(self) -> bool:
        return bool(self._dq)

    def __len__(self) -> int:
        return len(self._dq)

    def empty(self) -> bool:
        return not self._dq

    def put(self, env: EventEnvelope) -> None:
        """Append (never blocks) and wake a parked consumer."""
        self._dq.append(env)
        self.wake()

    def put_front(self, envs: list[EventEnvelope]) -> None:
        """Return claimed envelopes to the front without changing their order."""
        for env in reversed(envs):
            self._dq.appendleft(env)
        self.wake()

    def wake(self) -> None:
        """Resolve the consumer's wake latch if it is parked."""
        for waiter in (self._waiter, self._put_waiter):
            if waiter is not None and not waiter.done():
                waiter.set_result(None)

    def drain_all(self) -> list[EventEnvelope]:
        """Atomically claim every queued envelope on the event-loop thread."""
        items = list(self._dq)
        self._dq.clear()
        return items

    def drain_foldable(self) -> list[EventEnvelope]:
        """Claim the leading fire-and-forget stackable envelopes.

        Awaited or non-stackable envelopes form a FIFO boundary and retain their
        own turn rather than being folded into the live one.
        """
        out: list[EventEnvelope] = []
        while self._dq:
            env = self._dq[0]
            if env.future is not None or not env.event.stackable:
                break
            out.append(self._dq.popleft())
        return out

    async def wait_nonempty(self) -> None:
        """Park until enqueueing wakes the consumer.

        Rechecking before latch creation closes the race between the caller's
        emptiness check and parking.
        """
        if self._dq:
            return
        loop = asyncio.get_running_loop()
        self._waiter = loop.create_future()
        try:
            await self._waiter
        finally:
            self._waiter = None

    def has_event(self, pred: Callable[[TriggerEvent], bool]) -> bool:
        """Return whether any queued event matches ``pred``."""
        return any(pred(env.event) for env in self._dq)

    async def wait_put(self) -> None:
        """Park until the NEXT enqueue; queued residents do not satisfy it.

        ``wait_nonempty`` observes residency (the consumer's idle park);
        this observes arrival — a mid-turn waiter can react to new events
        without spinning on ones already queued behind the active turn.
        """
        loop = asyncio.get_running_loop()
        self._put_waiter = loop.create_future()
        try:
            await self._put_waiter
        finally:
            self._put_waiter = None

    def edit(self, pending_id: str, content: Any) -> bool:
        """Rewrite a still-unclaimed event's content by pending id."""
        for env in self._dq:
            if pending_id_of(env.event) == pending_id:
                env.event.content = content
                return True
        return False

    def cancel(self, pending_id: str) -> bool:
        """Drop a still-queued event by id; resolve its future if any."""
        for idx, env in enumerate(self._dq):
            if pending_id_of(env.event) == pending_id:
                del self._dq[idx]
                _reject_future(env, status="rejected")
                return True
        return False

    def remove_where(self, pred: Callable[[TriggerEvent], bool]) -> list[EventEnvelope]:
        """Remove and return envelopes whose events match ``pred``.

        The caller must resolve futures because removal may represent cancellation,
        interruption, or another domain-specific outcome.
        """
        removed = [env for env in self._dq if pred(env.event)]
        if removed:
            self._dq = deque(env for env in self._dq if not pred(env.event))
        return removed


def _reject_future(env: EventEnvelope, *, status: str) -> None:
    """Resolve an awaiting envelope's future so its caller never hangs."""
    fut = env.future
    if fut is not None and not fut.done():
        fut.set_result(TurnOutcome(status=status, was_primary=False))
