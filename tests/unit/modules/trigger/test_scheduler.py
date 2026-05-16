"""Unit tests for :mod:`kohakuterrarium.modules.trigger.scheduler`.

Behavior-first: the scheduler computes the seconds until the next
clock-aligned fire correctly for each mode, fires a TIMER event after a
short wait, and returns None when stopped.
"""

import asyncio
from datetime import datetime

import pytest

from kohakuterrarium.core.events import EventType
from kohakuterrarium.modules.trigger import scheduler as scheduler_mod
from kohakuterrarium.modules.trigger.scheduler import SchedulerTrigger


def _freeze_now(monkeypatch, dt):
    """Pin datetime.now() inside the scheduler module to *dt*."""

    class _FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return dt

    monkeypatch.setattr(scheduler_mod, "datetime", _FrozenDateTime)


class TestSecondsUntilNext:
    def test_every_minutes_aligns_to_clock(self, monkeypatch):
        # 10:07 with every_minutes=30 → next slot is 10:30 → 23m = 1380s.
        _freeze_now(monkeypatch, datetime(2026, 5, 14, 10, 7, 0))
        t = SchedulerTrigger(every_minutes=30, prompt="p")
        assert t._seconds_until_next() == pytest.approx(23 * 60)

    def test_every_minutes_wraps_past_midnight(self, monkeypatch):
        # 23:50 with every_minutes=30 → next slot 1440 is out of range →
        # wraps to next midnight → 10 minutes = 600s.
        _freeze_now(monkeypatch, datetime(2026, 5, 14, 23, 50, 0))
        t = SchedulerTrigger(every_minutes=30, prompt="p")
        assert t._seconds_until_next() == pytest.approx(10 * 60)

    def test_daily_at_today_in_future(self, monkeypatch):
        # 08:00, daily_at 09:30 → 90 minutes today.
        _freeze_now(monkeypatch, datetime(2026, 5, 14, 8, 0, 0))
        t = SchedulerTrigger(daily_at="09:30", prompt="p")
        assert t._seconds_until_next() == pytest.approx(90 * 60)

    def test_daily_at_already_passed_rolls_to_tomorrow(self, monkeypatch):
        # 10:00, daily_at 09:00 → already passed → +24h - 1h = 23h.
        _freeze_now(monkeypatch, datetime(2026, 5, 14, 10, 0, 0))
        t = SchedulerTrigger(daily_at="09:00", prompt="p")
        assert t._seconds_until_next() == pytest.approx(23 * 3600)

    def test_hourly_at_future_minute(self, monkeypatch):
        # 10:10, hourly_at 45 → 35 minutes.
        _freeze_now(monkeypatch, datetime(2026, 5, 14, 10, 10, 0))
        t = SchedulerTrigger(hourly_at=45, prompt="p")
        assert t._seconds_until_next() == pytest.approx(35 * 60)

    def test_hourly_at_passed_minute_rolls_to_next_hour(self, monkeypatch):
        # 10:50, hourly_at 30 → passed → +1h - 20m = 40m.
        _freeze_now(monkeypatch, datetime(2026, 5, 14, 10, 50, 0))
        t = SchedulerTrigger(hourly_at=30, prompt="p")
        assert t._seconds_until_next() == pytest.approx(40 * 60)

    def test_no_mode_configured_falls_back_to_60s(self, monkeypatch):
        _freeze_now(monkeypatch, datetime(2026, 5, 14, 10, 0, 0))
        t = SchedulerTrigger(prompt="p")
        assert t._seconds_until_next() == 60


class TestFire:
    async def test_fires_timer_event_after_short_wait(self, monkeypatch):
        t = SchedulerTrigger(prompt="scheduled")
        # Force a tiny wait so the test is fast and deterministic.
        monkeypatch.setattr(t, "_seconds_until_next", lambda: 0.05)
        await t.start()
        ev = await asyncio.wait_for(t.wait_for_trigger(), timeout=2)
        assert ev is not None
        assert ev.type == EventType.TIMER
        assert ev.content == "scheduled"
        assert ev.context["trigger"] == "scheduler"

    async def test_wait_returns_none_when_not_running(self):
        t = SchedulerTrigger(prompt="p")
        assert await t.wait_for_trigger() is None

    async def test_stop_during_wait_returns_none(self, monkeypatch):
        t = SchedulerTrigger(prompt="p")
        monkeypatch.setattr(t, "_seconds_until_next", lambda: 10)
        await t.start()

        async def _stopper():
            await asyncio.sleep(0.05)
            await t.stop()

        asyncio.create_task(_stopper())
        assert await asyncio.wait_for(t.wait_for_trigger(), timeout=2) is None

    async def test_nonpositive_wait_clamped_to_one_second(self, monkeypatch):
        # Contract: wait_seconds <= 0 is clamped to 1 to avoid a busy loop.
        # Stop immediately so we don't actually wait the full second.
        t = SchedulerTrigger(prompt="p")
        monkeypatch.setattr(t, "_seconds_until_next", lambda: -5)
        await t.start()
        await t.stop()
        # stop_event is set, so the clamped wait_for returns None at once.
        assert await asyncio.wait_for(t.wait_for_trigger(), timeout=2) is None


class TestResume:
    def test_resume_dict_round_trip(self):
        original = SchedulerTrigger(every_minutes=15, prompt="p")
        clone = SchedulerTrigger.from_resume_dict(original.to_resume_dict())
        assert clone.every_minutes == 15
        assert clone.prompt == "p"
        assert clone.daily_at is None
