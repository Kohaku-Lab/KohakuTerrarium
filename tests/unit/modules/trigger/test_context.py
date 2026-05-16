"""Unit tests for :mod:`kohakuterrarium.modules.trigger.context`.

Behavior-first: the context-update trigger fires only on a real change,
debounces, returns None when stopped, and supports manual trigger_now.
"""

import asyncio


from kohakuterrarium.core.events import EventType
from kohakuterrarium.modules.trigger.context import ContextUpdateTrigger


class TestContextChangeDetection:
    async def test_fires_when_context_changes(self):
        t = ContextUpdateTrigger(prompt="ctx", debounce_ms=0)
        await t.start()
        t.set_context({"input": "hello"})
        ev = await asyncio.wait_for(t.wait_for_trigger(), timeout=1)
        assert ev is not None
        assert ev.type == EventType.CONTEXT_UPDATE
        assert ev.context == {"input": "hello"}

    async def test_identical_context_does_not_set_pending(self):
        # Contract: _on_context_update only flips the pending event when
        # the context actually differs from the last seen one.
        t = ContextUpdateTrigger(debounce_ms=0)
        await t.start()
        t.set_context({"a": 1})
        await asyncio.wait_for(t.wait_for_trigger(), timeout=1)
        # Re-push the SAME context — pending must stay clear.
        t.set_context({"a": 1})
        assert t._pending_event.is_set() is False

    async def test_debounce_delays_event(self):
        t = ContextUpdateTrigger(debounce_ms=80, prompt="p")
        await t.start()
        t.set_context({"k": "v"})
        loop = asyncio.get_event_loop()
        start = loop.time()
        await asyncio.wait_for(t.wait_for_trigger(), timeout=1)
        # The debounce sleep must have been observed.
        assert loop.time() - start >= 0.07


class TestStopBehavior:
    async def test_wait_returns_none_when_not_running(self):
        t = ContextUpdateTrigger()
        assert await t.wait_for_trigger() is None

    async def test_stop_wakes_waiting_and_returns_none(self):
        t = ContextUpdateTrigger(debounce_ms=0)
        await t.start()

        async def _stopper():
            await asyncio.sleep(0.05)
            await t.stop()

        asyncio.create_task(_stopper())
        result = await asyncio.wait_for(t.wait_for_trigger(), timeout=2)
        assert result is None


class TestTriggerNow:
    async def test_trigger_now_with_context_fires_event(self):
        t = ContextUpdateTrigger(debounce_ms=0)
        await t.start()
        t.trigger_now({"manual": True})
        ev = await asyncio.wait_for(t.wait_for_trigger(), timeout=1)
        assert ev is not None
        assert ev.context.get("manual") is True

    async def test_trigger_now_without_context_still_wakes_waiter(self):
        t = ContextUpdateTrigger(debounce_ms=0)
        await t.start()
        t.set_context({"seed": 1})
        await asyncio.wait_for(t.wait_for_trigger(), timeout=1)
        # Now trigger with no new context — the waiter still resumes.
        t.trigger_now()
        ev = await asyncio.wait_for(t.wait_for_trigger(), timeout=1)
        assert ev is not None
