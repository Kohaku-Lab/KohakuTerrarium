"""Unit tests for kt_biome.triggers.cron.CronTrigger.

Covers (per plans/harness/proposal.md §4.10):
    * Valid 5-field cron expressions parse and compute a future next-run.
    * Invalid expressions raise at construction time.
    * start() -> stop() lifecycle cancels the background wait cleanly.
    * `skip_missed` backfill policy does NOT fire for a past slot.
    * `run_once_if_missed` policy fires once before resuming cadence.
"""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest

# Make kt_biome importable without requiring an editable install of the
# sibling package. Unit tests in this repo frequently bootstrap the
# kt-biome package path in this same way — see test_family_guidance_plugin.
_KT_BIOME = Path(__file__).resolve().parents[2] / "kt-biome"
if str(_KT_BIOME) not in sys.path:
    sys.path.insert(0, str(_KT_BIOME))

from kt_biome.triggers.cron import (  # noqa: E402
    CronExpressionError,
    CronTrigger,
    _BuiltinCron,
)


class TestExpressionParsing:
    def test_every_minute_parses_and_is_future(self):
        trigger = CronTrigger(expression="* * * * *", timezone="UTC")
        now = datetime.now(tz=ZoneInfo("UTC"))
        nxt = trigger._compute_next(now)
        assert nxt > now
        # "* * * * *" -> next fire is strictly within the next minute.
        assert (nxt - now) <= timedelta(minutes=1, seconds=5)

    def test_hourly_on_the_hour(self):
        # "0 * * * *" always falls on minute 0.
        trigger = CronTrigger(expression="0 * * * *", timezone="UTC")
        probe = datetime(2025, 1, 1, 12, 30, tzinfo=ZoneInfo("UTC"))
        nxt = trigger._compute_next(probe)
        assert nxt.minute == 0
        assert nxt > probe

    def test_step_list_range_supported_by_builtin(self):
        # Built-in parser must handle the documented common subset.
        parser = _BuiltinCron("*/15 9-17 * * 1-5")
        probe = datetime(2025, 1, 6, 9, 0)  # Monday 09:00
        nxt = parser.next_after(probe)
        assert nxt.minute in {0, 15, 30, 45}
        assert 9 <= nxt.hour <= 17

    def test_invalid_expression_rejected_at_construction(self):
        with pytest.raises(CronExpressionError):
            CronTrigger(expression="not a cron", timezone="UTC")

    def test_out_of_range_rejected(self):
        # Minute 99 is out of range.
        with pytest.raises(CronExpressionError):
            CronTrigger(expression="99 * * * *", timezone="UTC")

    def test_invalid_timezone_rejected(self):
        with pytest.raises(CronExpressionError):
            CronTrigger(expression="* * * * *", timezone="Not/AZone")

    def test_invalid_backfill_rejected(self):
        with pytest.raises(CronExpressionError):
            CronTrigger(
                expression="* * * * *",
                timezone="UTC",
                backfill="nonsense",
            )


class TestLifecycle:
    async def test_start_stop_cancels_cleanly(self):
        trigger = CronTrigger(
            expression="0 0 1 1 *",  # once a year — effectively "never" during the test
            timezone="UTC",
            content="yearly",
        )

        sleep_calls: list[float] = []
        original_wait_for = asyncio.wait_for

        async def recording_wait_for(awaitable, timeout):
            sleep_calls.append(timeout)
            # Hand control back to the event loop so stop() can set the event.
            return await original_wait_for(awaitable, timeout=timeout)

        await trigger.start()
        assert trigger.is_running is True

        with patch("kt_biome.triggers.cron.asyncio.wait_for", recording_wait_for):
            waiter = asyncio.create_task(trigger.wait_for_trigger())
            # Give the task a chance to start waiting.
            await asyncio.sleep(0)
            await trigger.stop()
            result = await asyncio.wait_for(waiter, timeout=1.0)

        assert result is None  # stopped while sleeping
        assert trigger.is_running is False
        assert sleep_calls and sleep_calls[0] > 0

    async def test_stop_without_start_is_safe(self):
        trigger = CronTrigger(expression="* * * * *", timezone="UTC")
        # Should not raise even though start() was never called.
        await trigger.stop()


class TestBackfillPolicy:
    async def test_skip_missed_does_not_fire_immediately(self):
        # With skip_missed (the default), the very first wait must
        # schedule the NEXT slot — it must NOT produce an immediate event.
        trigger = CronTrigger(
            expression="* * * * *",
            timezone="UTC",
            backfill="skip_missed",
        )

        captured: dict[str, float] = {}

        async def fake_wait_for(awaitable, timeout):
            captured["timeout"] = timeout
            # Cancel the inner wait so we don't actually sleep.
            if hasattr(awaitable, "close"):
                awaitable.close()
            raise asyncio.CancelledError

        await trigger.start()
        assert trigger._pending_backfill_fire is False  # key invariant

        with patch("kt_biome.triggers.cron.asyncio.wait_for", fake_wait_for):
            with pytest.raises(asyncio.CancelledError):
                await trigger.wait_for_trigger()

        assert "timeout" in captured
        assert captured["timeout"] > 0  # would have slept instead of firing
        await trigger.stop()

    async def test_run_once_if_missed_fires_on_start(self):
        trigger = CronTrigger(
            expression="*/5 * * * *",
            timezone="UTC",
            content="wake up",
            backfill="run_once_if_missed",
        )

        # wait_for should NOT be called on this path — we fire the
        # backfill event synchronously without sleeping.
        async def unreachable(*_args, **_kwargs):
            pytest.fail("wait_for should not be called on backfill fire")

        await trigger.start()
        assert trigger._pending_backfill_fire is True

        with patch("kt_biome.triggers.cron.asyncio.wait_for", unreachable):
            event = await trigger.wait_for_trigger()

        assert event is not None
        assert event.context["backfill"] is True
        assert event.context["trigger"] == "cron"
        assert event.content == "wake up"
        assert trigger._pending_backfill_fire is False
        await trigger.stop()

    async def test_metadata_merged_into_event_context(self):
        trigger = CronTrigger(
            expression="* * * * *",
            timezone="UTC",
            content="ping",
            metadata={"source": "cron", "channel": "root"},
            backfill="run_once_if_missed",
        )
        await trigger.start()
        event = await trigger.wait_for_trigger()
        await trigger.stop()
        assert event is not None
        assert event.context["source"] == "cron"
        assert event.context["channel"] == "root"
        # Trigger-owned keys must not be shadowed by metadata.
        assert event.context["trigger"] == "cron"


class TestResumePersistence:
    def test_roundtrip(self):
        original = CronTrigger(
            expression="*/10 * * * *",
            timezone="UTC",
            content="poll",
            metadata={"source": "cron"},
            backfill="skip_missed",
        )
        data = original.to_resume_dict()
        restored = CronTrigger.from_resume_dict(data)
        assert restored.expression == original.expression
        assert restored.timezone_name == original.timezone_name
        assert restored.content == original.content
        assert restored.metadata == original.metadata
        assert restored.backfill == original.backfill
        assert restored.enabled is True
