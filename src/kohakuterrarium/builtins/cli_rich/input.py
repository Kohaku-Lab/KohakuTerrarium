"""RichCLIInput — stub input module for the rich CLI mode.

In rich CLI mode, the actual input loop is driven by RichCLIApp (which
reads from the composer and calls agent.inject_input() directly). This
module exists to satisfy the agent's input module contract — it never
returns input through get_input(), instead waiting indefinitely.
"""

import asyncio

from kohakuterrarium.core.events import TriggerEvent
from kohakuterrarium.modules.input.base import BaseInputModule
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


class RichCLIInput(BaseInputModule):
    """Satisfy the input contract while RichCLIApp injects composer input."""

    def __init__(self):
        super().__init__()
        self._exit_requested = False
        self._wait_event = asyncio.Event()

    @property
    def exit_requested(self) -> bool:
        return self._exit_requested

    def request_exit(self) -> None:
        """Release the waiting input task after an app exit request."""
        self._exit_requested = True
        self._wait_event.set()

    async def _on_start(self) -> None:
        logger.debug("RichCLIInput started (stub mode)")

    async def _on_stop(self) -> None:
        self._wait_event.set()
        logger.debug("RichCLIInput stopped")

    async def get_input(self) -> TriggerEvent | None:
        """Wait for exit because composer input bypasses this method."""
        if self._exit_requested:
            return None
        await self._wait_event.wait()
        return None
