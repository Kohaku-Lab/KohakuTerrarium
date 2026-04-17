"""
Scheduler trigger: fires at specific clock times.

Supports cron-like scheduling: specific times, daily, hourly, etc.
"""

import asyncio
from datetime import datetime, timedelta
from typing import Any

from kohakuterrarium.core.events import EventType, TriggerEvent
from kohakuterrarium.modules.trigger.base import BaseTrigger
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


class SchedulerTrigger(BaseTrigger):
    """Trigger that fires at specific clock times.

    Modes:
    - every_minutes: fire every N minutes (aligned to clock)
    - daily_at: fire once per day at HH:MM
    - hourly_at: fire once per hour at :MM

    Usage:
        trigger = SchedulerTrigger(
            every_minutes=30,
            prompt="Half-hour check",
        )
    """

    resumable = True
    universal = True

    setup_tool_name = "add_schedule"
    setup_description = (
        "Install a clock-aligned schedule: fire every N minutes, daily at HH:MM, "
        "or hourly at minute :MM."
    )
    setup_param_schema = {
        "type": "object",
        "properties": {
            "every_minutes": {
                "type": "integer",
                "description": "Fire every N minutes, aligned to midnight (1-1440).",
            },
            "daily_at": {
                "type": "string",
                "description": "Fire daily at HH:MM (24h clock).",
            },
            "hourly_at": {
                "type": "integer",
                "description": "Fire every hour at minute :MM (0-59).",
            },
            "prompt": {
                "type": "string",
                "description": "Prompt injected when the schedule fires.",
            },
        },
        "required": ["prompt"],
    }
    setup_full_doc = (
        "Installs a SchedulerTrigger. Provide exactly one of `every_minutes`, "
        "`daily_at`, or `hourly_at` — they cannot be combined. `every_minutes` "
        "aligns to midnight so e.g. `every_minutes: 30` fires at :00 and :30 "
        "of every hour. Stash the returned trigger id to stop_task later."
    )

    def __init__(
        self,
        every_minutes: int | None = None,
        daily_at: str | None = None,
        hourly_at: int | None = None,
        prompt: str | None = None,
        **options: Any,
    ):
        """
        Args:
            every_minutes: Fire every N minutes (1-1440)
            daily_at: Fire daily at "HH:MM" (24h format)
            hourly_at: Fire every hour at minute :MM (0-59)
            prompt: Prompt to include in event
        """
        super().__init__(prompt=prompt, **options)
        self.every_minutes = every_minutes
        self.daily_at = daily_at
        self.hourly_at = hourly_at
        self._stop_event: asyncio.Event | None = None

    def to_resume_dict(self) -> dict[str, Any]:
        return {
            "every_minutes": self.every_minutes,
            "daily_at": self.daily_at,
            "hourly_at": self.hourly_at,
            "prompt": self.prompt,
        }

    @classmethod
    def from_resume_dict(cls, data: dict[str, Any]) -> "SchedulerTrigger":
        return cls(
            every_minutes=data.get("every_minutes"),
            daily_at=data.get("daily_at"),
            hourly_at=data.get("hourly_at"),
            prompt=data.get("prompt"),
        )

    async def _on_start(self) -> None:
        self._stop_event = asyncio.Event()
        logger.debug(
            "Scheduler trigger started",
            every_minutes=self.every_minutes,
            daily_at=self.daily_at,
            hourly_at=self.hourly_at,
        )

    async def _on_stop(self) -> None:
        if self._stop_event:
            self._stop_event.set()

    async def wait_for_trigger(self) -> TriggerEvent | None:
        if not self._running or not self._stop_event:
            return None

        wait_seconds = self._seconds_until_next()
        if wait_seconds <= 0:
            wait_seconds = 1  # avoid busy loop

        try:
            await asyncio.wait_for(self._stop_event.wait(), timeout=wait_seconds)
            return None  # stopped
        except asyncio.TimeoutError:
            pass  # time to fire

        if not self._running:
            return None

        now = datetime.now()
        return self._create_event(
            EventType.TIMER,
            content=self.prompt or f"Scheduled event at {now.strftime('%H:%M')}",
            context={
                "trigger": "scheduler",
                "time": now.isoformat(),
                "every_minutes": self.every_minutes,
                "daily_at": self.daily_at,
                "hourly_at": self.hourly_at,
            },
        )

    def _seconds_until_next(self) -> float:
        now = datetime.now()

        if self.every_minutes:
            # Align to clock: next multiple of N minutes from midnight
            minutes_today = now.hour * 60 + now.minute
            next_slot = ((minutes_today // self.every_minutes) + 1) * self.every_minutes
            if next_slot >= 1440:
                # Past midnight — wrap to next day
                target = (now + timedelta(days=1)).replace(
                    hour=0, minute=0, second=0, microsecond=0
                )
            else:
                target = now.replace(
                    hour=next_slot // 60,
                    minute=next_slot % 60,
                    second=0,
                    microsecond=0,
                )
            if target <= now:
                target += timedelta(minutes=self.every_minutes)
            return (target - now).total_seconds()

        if self.daily_at:
            parts = self.daily_at.split(":")
            hour, minute = int(parts[0]), int(parts[1]) if len(parts) > 1 else 0
            target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if target <= now:
                target += timedelta(days=1)
            return (target - now).total_seconds()

        if self.hourly_at is not None:
            target = now.replace(minute=self.hourly_at, second=0, microsecond=0)
            if target <= now:
                target += timedelta(hours=1)
            return (target - now).total_seconds()

        return 60  # fallback
