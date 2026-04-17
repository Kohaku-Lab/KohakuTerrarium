"""
Timer trigger - fires at regular intervals.
"""

import asyncio
from typing import Any

from kohakuterrarium.core.events import EventType, TriggerEvent
from kohakuterrarium.modules.trigger.base import BaseTrigger
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


class TimerTrigger(BaseTrigger):
    """
    Trigger that fires at regular intervals.

    Usage:
        trigger = TimerTrigger(
            interval=60,  # seconds
            prompt="Check system status",
        )
        await trigger.start()
        event = await trigger.wait_for_trigger()
    """

    resumable = True
    universal = True

    setup_tool_name = "add_timer"
    setup_description = (
        "Install a timer that wakes the agent every N seconds with a custom prompt."
    )
    setup_param_schema = {
        "type": "object",
        "properties": {
            "interval": {
                "type": "number",
                "description": "Seconds between fires (must be > 0).",
            },
            "prompt": {
                "type": "string",
                "description": "Prompt injected when the timer fires.",
            },
            "immediate": {
                "type": "boolean",
                "description": "If true, fire once right after install before waiting.",
                "default": False,
            },
        },
        "required": ["interval", "prompt"],
    }
    setup_full_doc = (
        "Installs a TimerTrigger. The agent will be woken every `interval` "
        "seconds with the given `prompt` injected as a user_input-style event. "
        "Set `immediate: true` to fire once immediately on install as well. "
        "The tool returns the installed trigger id — stash it in scratchpad "
        "if you may need to stop_task it later."
    )

    def to_resume_dict(self) -> dict[str, Any]:
        return {
            "interval": self.interval,
            "prompt": self.prompt,
            "immediate": False,  # Don't fire immediately on resume
        }

    @classmethod
    def from_resume_dict(cls, data: dict[str, Any]) -> "TimerTrigger":
        return cls(
            interval=data.get("interval", 60.0),
            prompt=data.get("prompt"),
            immediate=data.get("immediate", False),
        )

    def __init__(
        self,
        interval: float = 60.0,
        prompt: str | None = None,
        immediate: bool = False,
        **options: Any,
    ):
        """
        Initialize timer trigger.

        Args:
            interval: Seconds between triggers
            prompt: Prompt to include in event
            immediate: Fire immediately on start (before first interval)
            **options: Additional options
        """
        super().__init__(prompt=prompt, **options)
        self.interval = interval
        self.immediate = immediate
        self._first_trigger = True
        self._stop_event: asyncio.Event | None = None

    def _ensure_events(self) -> None:
        """Lazily create asyncio primitives if not yet initialized."""
        if self._stop_event is None:
            self._stop_event = asyncio.Event()

    async def _on_start(self) -> None:
        """Reset state on start."""
        self._first_trigger = True
        self._ensure_events()
        self._stop_event.clear()
        logger.debug("Timer trigger started", interval=self.interval)

    async def _on_stop(self) -> None:
        """Signal stop."""
        self._ensure_events()
        self._stop_event.set()
        logger.debug("Timer trigger stopped")

    async def wait_for_trigger(self) -> TriggerEvent | None:
        """Wait for timer interval."""
        if not self._running:
            return None

        # Fire immediately if configured and first trigger
        if self.immediate and self._first_trigger:
            self._first_trigger = False
            return self._create_event(
                EventType.TIMER,
                content=self.prompt or "Timer fired (immediate)",
                context={"trigger": "timer", "interval": self.interval},
            )

        self._first_trigger = False

        # Wait for interval or stop
        self._ensure_events()
        try:
            await asyncio.wait_for(
                self._stop_event.wait(),
                timeout=self.interval,
            )
            # Stop event was set
            return None
        except asyncio.TimeoutError:
            # Interval elapsed - fire trigger
            if not self._running:
                return None

            return self._create_event(
                EventType.TIMER,
                content=self.prompt or f"Timer fired (interval={self.interval}s)",
                context={"trigger": "timer", "interval": self.interval},
            )
