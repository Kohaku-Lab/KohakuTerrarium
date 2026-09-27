"""Task-scoped observers for transient model recovery state."""

import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Awaitable, Callable

from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)
_observer: ContextVar[tuple | None] = ContextVar(
    "model_recovery_observer", default=None
)


@contextmanager
def observe_model_recovery(callback: Callable[[dict], Awaitable[None]]):
    """Observe recovery only in the calling task, excluding inherited child tasks."""
    token = _observer.set((asyncio.current_task(), callback))
    try:
        yield
    finally:
        _observer.reset(token)


async def notify_model_recovery(payload: dict) -> None:
    """Publish a transient state without putting it in generated model text."""
    observer = _observer.get()
    if observer is None or observer[0] is not asyncio.current_task():
        return
    try:
        await observer[1](payload)
    except Exception:
        logger.warning("Model recovery observer failed", exc_info=True)
