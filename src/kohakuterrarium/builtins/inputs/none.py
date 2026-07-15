"""Blocking no-input adapter for trigger-driven agents."""

import asyncio
from typing import Any

from kohakuterrarium.core.events import TriggerEvent
from kohakuterrarium.modules.input.base import BaseInputModule
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


class NoneInput(BaseInputModule):
    """Remain blocked until stopped while producing no input events."""

    def __init__(self, **_options: Any):
        super().__init__()
        self._stop_event: asyncio.Event | None = None
        self._exit_requested = False

    @property
    def exit_requested(self) -> bool:
        """Return whether the blocking input loop has been released."""
        return self._exit_requested

    async def _on_start(self) -> None:
        """Create the event that releases the blocked input wait."""
        self._stop_event = asyncio.Event()
        logger.debug("NoneInput started (trigger-only mode)")

    async def _on_stop(self) -> None:
        """Release any pending input wait during shutdown."""
        if self._stop_event:
            self._stop_event.set()
        logger.debug("NoneInput stopped")

    async def get_input(self) -> TriggerEvent | None:
        """Wait for shutdown and return no input event."""
        if not self._running:
            return None

        if self._stop_event:
            await self._stop_event.wait()

        self._exit_requested = True
        return None
