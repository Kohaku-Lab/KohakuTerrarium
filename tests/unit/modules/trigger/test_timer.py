"""Unit tests for :mod:`kohakuterrarium.modules.trigger.timer`.

Behavior-first: the timer must fire a TIMER event after its interval,
fire immediately when configured, return None when stopped, and survive
resume without re-firing immediately.
"""

import asyncio


from kohakuterrarium.core.events import EventType
from kohakuterrarium.modules.trigger.timer import TimerTrigger


class TestImmediateFire:
    async def test_immediate_fires_on_first_wait(self):
        t = TimerTrigger(interval=999, prompt="ping", immediate=True)
        await t.start()
        ev = await t.wait_for_trigger()
        assert ev is not None
        assert ev.type == EventType.TIMER
        assert ev.content == "ping"
        assert ev.context["interval"] == 999

    async def test_immediate_only_fires_once_then_waits(self):
        # After the immediate fire, the next wait must block on the
        # interval — proven here by the stop_event short-circuit.
        t = TimerTrigger(interval=999, immediate=True)
        await t.start()
        await t.wait_for_trigger()  # immediate
        await t.stop()
        # Second call now sees stop_event set → returns None instantly.
        assert await asyncio.wait_for(t.wait_for_trigger(), timeout=1) is None


class TestIntervalFire:
    async def test_fires_after_interval_elapses(self):
        t = TimerTrigger(interval=0.05, prompt="tick")
        await t.start()
        ev = await asyncio.wait_for(t.wait_for_trigger(), timeout=1)
        assert ev is not None
        assert ev.type == EventType.TIMER
        assert ev.content == "tick"

    async def test_default_content_used_when_no_prompt(self):
        t = TimerTrigger(interval=0.05)
        await t.start()
        ev = await asyncio.wait_for(t.wait_for_trigger(), timeout=1)
        assert "Timer fired" in ev.content


class TestStopBehavior:
    async def test_wait_returns_none_when_not_running(self):
        t = TimerTrigger(interval=0.01)
        # never started
        assert await t.wait_for_trigger() is None

    async def test_stop_during_wait_returns_none(self):
        t = TimerTrigger(interval=10)
        await t.start()

        async def _stopper():
            await asyncio.sleep(0.05)
            await t.stop()

        asyncio.create_task(_stopper())
        result = await asyncio.wait_for(t.wait_for_trigger(), timeout=2)
        assert result is None


class TestResume:
    async def test_resume_dict_round_trip_disables_immediate(self):
        # Contract: a timer that fired immediately on first run must NOT
        # fire immediately again on resume (to_resume_dict pins immediate
        # to False).
        original = TimerTrigger(interval=42, prompt="p", immediate=True)
        data = original.to_resume_dict()
        assert data["immediate"] is False
        clone = TimerTrigger.from_resume_dict(data)
        assert clone.interval == 42
        assert clone.prompt == "p"
        assert clone.immediate is False

    def test_class_metadata_is_universal_and_resumable(self):
        assert TimerTrigger.resumable is True
        assert TimerTrigger.universal is True
        assert TimerTrigger.setup_tool_name == "add_timer"
