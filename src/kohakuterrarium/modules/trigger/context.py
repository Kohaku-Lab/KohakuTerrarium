"""Fire debounced events when externally supplied context changes."""

import asyncio
from typing import Any

from kohakuterrarium.core.events import EventType, TriggerEvent
from kohakuterrarium.modules.trigger.base import BaseTrigger
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


class ContextUpdateTrigger(BaseTrigger):
    """Fire after context changes settle for the configured debounce interval."""

    def __init__(
        self,
        prompt: str | None = None,
        debounce_ms: int = 100,
        **options: Any,
    ):
        """Initialize prompt, debounce duration, and lazy async state."""
        super().__init__(prompt=prompt, **options)
        self.debounce_ms = debounce_ms
        self._pending_event: asyncio.Event | None = None
        self._last_context: dict[str, Any] = {}
        self._stop_event: asyncio.Event | None = None

    def _ensure_events(self) -> None:
        """Lazily create asyncio primitives if not yet initialized."""
        if self._pending_event is None:
            self._pending_event = asyncio.Event()
        if self._stop_event is None:
            self._stop_event = asyncio.Event()

    async def _on_start(self) -> None:
        """Reset state on start."""
        self._ensure_events()
        self._pending_event.clear()
        self._stop_event.clear()
        self._last_context = {}
        logger.debug("Context update trigger started")

    async def _on_stop(self) -> None:
        """Signal stop."""
        self._ensure_events()
        self._stop_event.set()
        self._pending_event.set()
        logger.debug("Context update trigger stopped")

    def _on_context_update(self, context: dict[str, Any]) -> None:
        """Signal only context values that differ from the previous update."""
        self._ensure_events()
        if context != self._last_context:
            self._last_context = context.copy()
            self._pending_event.set()
            logger.debug("Context update detected")

    async def wait_for_trigger(self) -> TriggerEvent | None:
        """Wait for context change."""
        if not self._running:
            return None

        self._ensure_events()
        await self._pending_event.wait()

        if not self._running:
            return None

        if self.debounce_ms > 0:
            await asyncio.sleep(self.debounce_ms / 1000)

        self._pending_event.clear()

        if not self._running:
            return None

        return self._create_event(
            EventType.CONTEXT_UPDATE,
            content=self.prompt or "Context updated",
            context=self._last_context.copy(),
        )

    def trigger_now(self, context: dict[str, Any] | None = None) -> None:
        """Signal the trigger immediately after optionally replacing context."""
        self._ensure_events()
        if context:
            self._context.update(context)
            self._last_context = context.copy()
        self._pending_event.set()
