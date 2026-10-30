"""Task-scoped observers for the size of tool-call arguments while they stream.

A provider that streams native tool calls creates one :class:`ToolCallProgress`
per request and feeds it every argument delta; the controller observes it to
show "writing app… 12 KB" before the call is complete.
"""

import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Awaitable, Callable

from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

REPORT_STEP_CHARS = 2048

_observer: ContextVar[tuple | None] = ContextVar(
    "tool_call_progress_observer", default=None
)


@contextmanager
def observe_tool_call_progress(callback: Callable[[dict], Awaitable[None]]):
    """Observe argument progress only in the calling task."""
    token = _observer.set((asyncio.current_task(), callback))
    try:
        yield
    finally:
        _observer.reset(token)


class ToolCallProgress:
    """Throttled reports of one response's streamed tool-call arguments."""

    def __init__(self, step: int = REPORT_STEP_CHARS) -> None:
        self.step = step
        self._reported: dict[int, tuple[str, int]] = {}

    async def update(self, slot: int, name: str, chars: int) -> None:
        """Report when a call gets its name or grows by ``step`` characters."""
        if not name:
            return
        last = self._reported.get(slot)
        if last is not None and last[0] == name and chars - last[1] < self.step:
            return
        self._reported[slot] = (name, chars)
        observer = _observer.get()
        if observer is None or observer[0] is not asyncio.current_task():
            return
        try:
            await observer[1]({"index": slot, "name": name, "chars": chars})
        except Exception:
            logger.warning("Tool-call progress observer failed", exc_info=True)
