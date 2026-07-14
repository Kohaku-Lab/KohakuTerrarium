"""Infinite per-agent event inbox — the queue-first event system.

One structure the agent's single consumer drains. It replaces both the
old ``_pending_mid_turn_inputs`` fold-buffer and the ``_processing_lock``
waiter list (which used to act as a hidden one-event-per-turn FIFO).

Guarantees:

- **Infinite capacity** — :meth:`put` is a synchronous ``deque.append``;
  a producer never blocks (invariant 1).
- **Full-queue claim** — :meth:`drain_all` pops everything in one
  synchronous span with no ``await`` between the read and the clear, so a
  claim is atomic against a concurrent :meth:`put` (invariant 3).
- **Enqueue is the wake** — :meth:`put` resolves the consumer's parked
  wake latch; :meth:`wait_nonempty` re-checks the deque before parking so
  a put landing exactly at park time is never lost (invariant 4).

Each queued item is an :class:`EventEnvelope`; awaiting ingress
(``run`` / ``run_event`` / a primary ``_process_event``) carries a
``future`` the consumer resolves with a :class:`TurnOutcome` when the
turn that consumes the event ends.
"""

import asyncio
from collections import deque
from dataclasses import dataclass
from typing import Any, Callable

from kohakuterrarium.core.events import TriggerEvent
from kohakuterrarium.core.pending_input import pending_id_of


@dataclass
class TurnOutcome:
    """Resolution handed to an awaiting envelope's future at turn end.

    ``status`` is the turn's coarse status (``ok`` / ``interrupted`` /
    ``rejected``); capture-based callers (``run`` / ``run_event``) build
    their :class:`TurnResult` from their own capture and only read
    ``status`` + ``interrupted_by_user`` here. ``was_primary`` is the
    ``inject_input`` bool: ``True`` when the event started a fresh turn,
    ``False`` when it folded into a live one.
    """

    status: str = "ok"
    was_primary: bool = True
    interrupted_by_user: bool = False


@dataclass
class EventEnvelope:
    """One queued event plus its optional awaiting machinery.

    ``future`` is set for callers that await the turn consuming this
    event; it resolves with a :class:`TurnOutcome`. ``capture`` is the
    per-turn output sink for ``run`` / ``run_stream`` / ``run_event`` — the
    consumer attaches it before the turn starts and detaches it after.
    Fire-and-forget ingress (folded events) leaves both ``None``.
    """

    event: TriggerEvent
    future: "asyncio.Future | None" = None
    capture: Any = None


class EventInbox:
    """The single infinite queue drained by the agent's one consumer."""

    def __init__(self) -> None:
        self._dq: deque[EventEnvelope] = deque()
        self._waiter: asyncio.Future | None = None

    def __bool__(self) -> bool:
        return bool(self._dq)

    def __len__(self) -> int:
        return len(self._dq)

    def empty(self) -> bool:
        return not self._dq

    # -- producer side ---------------------------------------------------

    def put(self, env: EventEnvelope) -> None:
        """Append (never blocks) and wake a parked consumer."""
        self._dq.append(env)
        self.wake()

    def put_front(self, envs: list[EventEnvelope]) -> None:
        """Return claimed envelopes to the FRONT, preserving their order."""
        for env in reversed(envs):
            self._dq.appendleft(env)
        self.wake()

    def wake(self) -> None:
        """Resolve the consumer's wake latch if it is parked."""
        waiter = self._waiter
        if waiter is not None and not waiter.done():
            waiter.set_result(None)

    # -- consumer side ---------------------------------------------------

    def drain_all(self) -> list[EventEnvelope]:
        """Pop EVERYTHING synchronously — the atomic full-queue claim.

        Atomicity holds because ``list()`` + ``clear()`` run with no ``await``
        between them AND every producer calls :meth:`put` on the same event
        loop thread (agents never enqueue from a worker thread) — so no
        concurrent mutation can interleave the read and the clear."""
        items = list(self._dq)
        self._dq.clear()
        return items

    def drain_foldable(self) -> list[EventEnvelope]:
        """Pop the leading run of fire-and-forget stackable envelopes.

        Stops at the first non-stackable OR awaiting (future-bearing)
        envelope so those keep their own turn (FIFO preserved). Used by
        the mid-turn re-claim, which must not swallow an ``await_turn``
        caller's event nor fold into a non-stackable event."""
        out: list[EventEnvelope] = []
        while self._dq:
            env = self._dq[0]
            if env.future is not None or not env.event.stackable:
                break
            out.append(self._dq.popleft())
        return out

    async def wait_nonempty(self) -> None:
        """Park until a producer wakes us — the ONLY park in the consumer.

        Re-checks the deque before creating the latch so a ``put`` that
        landed between the caller's emptiness check and here is seen
        immediately (no lost wakeup)."""
        if self._dq:
            return
        loop = asyncio.get_running_loop()
        self._waiter = loop.create_future()
        try:
            await self._waiter
        finally:
            self._waiter = None

    # -- id-targeted edit / cancel / remove ------------------------------

    def edit(self, pending_id: str, content: Any) -> bool:
        """Rewrite a still-queued event's content by id (UXI-08a).

        Wins iff it runs before :meth:`drain_all` / :meth:`drain_foldable`
        claims the envelope."""
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
        """Remove every envelope whose event matches ``pred``; return them.

        The caller is responsible for resolving any futures on the removed
        envelopes (interrupt drops in-flight Drive deliveries this way)."""
        removed = [env for env in self._dq if pred(env.event)]
        if removed:
            self._dq = deque(env for env in self._dq if not pred(env.event))
        return removed


def _reject_future(env: EventEnvelope, *, status: str) -> None:
    """Resolve an awaiting envelope's future so its caller never hangs."""
    fut = env.future
    if fut is not None and not fut.done():
        fut.set_result(TurnOutcome(status=status, was_primary=False))
