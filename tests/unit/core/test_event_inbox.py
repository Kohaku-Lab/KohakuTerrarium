"""Unit tests for :class:`kohakuterrarium.core.event_inbox.EventInbox`.

The single infinite queue behind the queue-first event system. These pin
the primitive guarantees the consumer relies on: atomic full-queue claim,
foldable-prefix drain, the no-lost-wakeup park, and id-targeted
edit/cancel that win only before the claim.
"""

import asyncio

import pytest

from kohakuterrarium.core.event_inbox import EventEnvelope, EventInbox, TurnOutcome
from kohakuterrarium.core.events import TriggerEvent, create_user_input_event
from kohakuterrarium.core.pending_input import stamp_pending_id


def _env(content: str, *, stackable: bool = True, future=None) -> EventEnvelope:
    return EventEnvelope(
        TriggerEvent(type="user_input", content=content, stackable=stackable),
        future=future,
    )


class TestDrain:
    def test_put_then_drain_all_is_atomic_and_ordered(self):
        inbox = EventInbox()
        assert inbox.empty()
        for c in "abc":
            inbox.put(_env(c))
        assert len(inbox) == 3
        claimed = inbox.drain_all()
        assert [e.event.content for e in claimed] == ["a", "b", "c"]
        # Fully drained in one claim.
        assert inbox.empty()
        assert inbox.drain_all() == []

    def test_drain_foldable_stops_at_non_stackable(self):
        inbox = EventInbox()
        inbox.put(_env("s1"))
        inbox.put(_env("ns", stackable=False))
        inbox.put(_env("s2"))
        foldable = inbox.drain_foldable()
        # Only the leading stackable prefix; the non-stackable event and
        # everything after it stays for its own turn.
        assert [e.event.content for e in foldable] == ["s1"]
        assert len(inbox) == 2

    async def test_drain_foldable_stops_at_awaiting_envelope(self):
        inbox = EventInbox()
        loop = asyncio.get_running_loop()
        inbox.put(_env("ff1"))
        inbox.put(_env("awaiting", future=loop.create_future()))
        inbox.put(_env("ff2"))
        foldable = inbox.drain_foldable()
        # An awaiting (future-bearing) envelope is never swallowed by the
        # mid-turn re-claim — it keeps its own turn.
        assert [e.event.content for e in foldable] == ["ff1"]
        assert len(inbox) == 2


class TestWakeup:
    async def test_wait_returns_immediately_when_non_empty(self):
        inbox = EventInbox()
        inbox.put(_env("x"))
        # Must not block — the queue already has an item.
        await asyncio.wait_for(inbox.wait_nonempty(), timeout=1.0)

    async def test_put_after_park_wakes_the_consumer(self):
        inbox = EventInbox()
        woke = asyncio.Event()

        async def consumer():
            await inbox.wait_nonempty()
            woke.set()

        task = asyncio.create_task(consumer())
        await asyncio.sleep(0)  # let it park
        assert not woke.is_set()
        inbox.put(_env("late"))  # enqueue IS the wake
        await asyncio.wait_for(woke.wait(), timeout=1.0)
        await task

    async def test_put_before_park_is_not_lost(self):
        # The classic no-lost-wakeup race: an item landing exactly when
        # the consumer is about to park must be seen, not lost.
        inbox = EventInbox()
        inbox.put(_env("early"))
        # wait_nonempty re-checks the deque before creating a latch.
        await asyncio.wait_for(inbox.wait_nonempty(), timeout=1.0)
        assert not inbox.empty()


class TestEditCancelRemove:
    def test_edit_wins_before_claim_and_noops_after(self):
        inbox = EventInbox()
        evt = create_user_input_event("original")
        pid = stamp_pending_id(evt)
        inbox.put(EventEnvelope(evt))
        assert inbox.edit(pid, "corrected") is True
        # After the claim the envelope is gone — edit is a plain no-op.
        claimed = inbox.drain_all()
        assert claimed[0].event.content == "corrected"
        assert inbox.edit(pid, "too late") is False

    async def test_cancel_drops_and_resolves_future(self):
        inbox = EventInbox()
        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        evt = create_user_input_event("cancel me")
        pid = stamp_pending_id(evt)
        inbox.put(EventEnvelope(evt, future=fut))
        assert inbox.cancel(pid) is True
        assert inbox.empty()
        # A cancelled awaiting caller must not hang — its future resolves.
        assert fut.done()
        assert isinstance(fut.result(), TurnOutcome)
        assert fut.result().status == "rejected"
        # Already gone — cancel again is a no-op.
        assert inbox.cancel(pid) is False

    def test_remove_where_returns_matches_and_keeps_rest(self):
        inbox = EventInbox()
        inbox.put(EventEnvelope(TriggerEvent(type="drive_ready", content="d1")))
        inbox.put(EventEnvelope(TriggerEvent(type="user_input", content="keep")))
        inbox.put(EventEnvelope(TriggerEvent(type="drive_ready", content="d2")))
        removed = inbox.remove_where(lambda ev: ev.type.startswith("drive_"))
        assert [e.event.content for e in removed] == ["d1", "d2"]
        assert [e.event.type for e in inbox.drain_all()] == ["user_input"]


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
