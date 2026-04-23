"""Tests for kt_biome.triggers.skill_nudge — SkillNudgeTrigger."""

from __future__ import annotations

import asyncio

from kt_biome.tools import _skill_activity
from kt_biome.triggers.skill_nudge import NUDGE_EVENT_TYPE, SkillNudgeTrigger


async def _drain_event(trigger: SkillNudgeTrigger, timeout: float = 0.2):
    """Return the pending event if one is armed, else None (on timeout)."""
    try:
        return await asyncio.wait_for(trigger.wait_for_trigger(), timeout=timeout)
    except asyncio.TimeoutError:
        return None


class TestSkillNudgeCadence:
    async def test_fires_every_interval(self):
        _skill_activity.clear()
        trigger = SkillNudgeTrigger(
            options={
                "interval_iterations": 3,
                "cooldown_iterations": 0,
                "agent_name": "agent_a",
            }
        )
        await trigger.start()

        try:
            # Iterations 1 and 2 should NOT fire.
            trigger.set_context({})
            assert await _drain_event(trigger) is None
            trigger.set_context({})
            assert await _drain_event(trigger) is None

            # Iteration 3 should fire.
            trigger.set_context({})
            event = await _drain_event(trigger)
            assert event is not None
            assert event.type == NUDGE_EVENT_TYPE
            assert "skill_manage" in event.get_text_content()
            assert event.context.get("iteration") == 3

            # Iterations 4 and 5 should NOT fire (cooldown=0 but interval=3).
            trigger.set_context({})
            assert await _drain_event(trigger) is None
            trigger.set_context({})
            assert await _drain_event(trigger) is None

            # Iteration 6 should fire again.
            trigger.set_context({})
            event_2 = await _drain_event(trigger)
            assert event_2 is not None
            assert event_2.context.get("iteration") == 6
        finally:
            await trigger.stop()

    async def test_disabled_trigger_never_fires(self):
        _skill_activity.clear()
        trigger = SkillNudgeTrigger(
            options={
                "interval_iterations": 1,
                "cooldown_iterations": 0,
                "enabled": False,
                "agent_name": "agent_disabled",
            }
        )
        await trigger.start()
        try:
            for _ in range(5):
                trigger.set_context({})
                assert await _drain_event(trigger) is None
        finally:
            await trigger.stop()


class TestSkillNudgeCooldown:
    async def test_skill_manage_silences_trigger(self):
        _skill_activity.clear()
        trigger = SkillNudgeTrigger(
            options={
                "interval_iterations": 2,
                "cooldown_iterations": 3,
                "agent_name": "agent_b",
            }
        )
        await trigger.start()
        try:
            # Simulate skill_manage being used just before iteration 1.
            _skill_activity.mark_used("agent_b")

            # Iteration 1: picks up the skill_manage signal, silences for
            # cooldown_iterations turns.
            trigger.set_context({})
            assert await _drain_event(trigger) is None

            state = trigger._debug_state()
            assert state["silence_until"] >= state["iterations"] + 3 - 1

            # Next iterations stay silent for cooldown_iterations turns:
            # silence_until = 1 + 3 = 4, so iter 2 and 3 are silent.
            trigger.set_context({})  # iter 2
            assert await _drain_event(trigger) is None
            trigger.set_context({})  # iter 3
            assert await _drain_event(trigger) is None

            # Iter 4 is the first iteration past the silence window AND
            # a multiple of the interval (2), so it fires.
            trigger.set_context({})  # iter 4
            event = await _drain_event(trigger)
            assert event is not None
            assert event.type == NUDGE_EVENT_TYPE
            assert event.context["iteration"] == 4
        finally:
            await trigger.stop()

    async def test_cooldown_after_fire(self):
        _skill_activity.clear()
        trigger = SkillNudgeTrigger(
            options={
                "interval_iterations": 2,
                "cooldown_iterations": 3,
                "agent_name": "agent_c",
            }
        )
        await trigger.start()
        try:
            # First fire at iteration 2.
            trigger.set_context({})  # 1
            assert await _drain_event(trigger) is None
            trigger.set_context({})  # 2
            event = await _drain_event(trigger)
            assert event is not None

            # Cooldown is 3 → next eligible iteration is >= 2 + 3 = 5,
            # and iter must be multiple of interval (2). So first refire
            # at iter 6.
            for _ in range(3):
                trigger.set_context({})
                assert await _drain_event(trigger) is None
            # iter is now 5 — silent.
            trigger.set_context({})
            event_2 = await _drain_event(trigger)
            assert event_2 is not None
            assert event_2.context["iteration"] == 6
        finally:
            await trigger.stop()
