"""Small helpers for the background futures of the browser runtime."""

import asyncio
from collections.abc import Awaitable

_outlasting: set[asyncio.Future] = set()


def _retrieve(future: asyncio.Future) -> None:
    if not future.cancelled():
        future.exception()


def discard(future: asyncio.Future) -> None:
    """Cancel ``future`` and mark whatever it ends with as retrieved."""
    future.cancel()
    future.add_done_callback(_retrieve)


async def outlast(operation: Awaitable[None], bound: float) -> None:
    """Run ``operation`` to its end, up to ``bound`` s, whatever cancels the caller.

    A cancellation that arrived meanwhile is raised once ``operation`` ended
    or the bound passed; past the bound ``operation`` runs on, unwaited.
    """
    task = asyncio.ensure_future(operation)
    _outlasting.add(task)
    task.add_done_callback(_outlasting.discard)
    loop = asyncio.get_running_loop()
    deadline = loop.time() + bound
    cancelled = False
    while not task.done() and loop.time() < deadline:
        try:
            await asyncio.wait({task}, timeout=deadline - loop.time())
        except asyncio.CancelledError:
            cancelled = True
    if cancelled:
        raise asyncio.CancelledError
