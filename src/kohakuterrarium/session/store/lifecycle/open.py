"""Cancellation-safe ownership transfer for stores opened off the event loop."""

import asyncio
from collections.abc import Callable
from typing import Any, TypeVar

from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)
_T = TypeVar("_T")


async def _finish_cleanup(task: asyncio.Task[_T]) -> _T:
    """Finish an owned operation even when its waiter is cancelled again."""
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            continue
    return task.result()


async def open_owned_store(
    opener: Callable[..., SessionStore], /, *args: Any, **kwargs: Any
) -> SessionStore:
    """Open off-loop; a cancelled handoff closes the unclaimed store off-loop."""
    opening = asyncio.create_task(asyncio.to_thread(opener, *args, **kwargs))
    try:
        return await asyncio.shield(opening)
    except asyncio.CancelledError:
        try:
            store = await _finish_cleanup(opening)
        except Exception:
            logger.warning("Session open failed after cancellation", exc_info=True)
        else:
            closing = asyncio.create_task(
                asyncio.to_thread(store.close, update_status=False)
            )
            try:
                await _finish_cleanup(closing)
            except Exception:
                logger.warning("Cancelled session open cleanup failed", exc_info=True)
        raise
