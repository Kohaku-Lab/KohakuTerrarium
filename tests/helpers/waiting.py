"""Deadline-based waiting for tests that observe background work."""

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Any

DEFAULT_TIMEOUT = 60.0


async def finish_within(awaitable: Awaitable[Any], what: str, limit: float) -> Any:
    """Await ``awaitable``; fail the test when it is still running after ``limit`` seconds.

    Whatever the call raises, a ``TimeoutError`` of its own included, reaches
    the caller unchanged; only the limit running out is reported as overrun.
    An overrun or cancelled wait cancels the call and waits for it to end.
    """
    task = asyncio.ensure_future(awaitable)
    try:
        done, _ = await asyncio.wait({task}, timeout=limit)
    except asyncio.CancelledError:
        task.cancel()
        await asyncio.wait({task})
        raise
    if not done:
        task.cancel()
        await asyncio.wait({task})
        raise AssertionError(f"{what} did not finish within {limit:g} s")
    return task.result()


async def wait_until(
    predicate: Callable[[], Any],
    *,
    timeout: float = DEFAULT_TIMEOUT,
    interval: float = 0.01,
    what: str = "condition",
) -> Any:
    """Return the first truthy ``predicate()`` result; fail once ``timeout`` passes."""
    deadline = time.monotonic() + timeout
    while True:
        value = predicate()
        if value:
            return value
        if time.monotonic() >= deadline:
            raise AssertionError(f"timed out after {timeout}s waiting for {what}")
        await asyncio.sleep(interval)


def wait_until_sync(
    predicate: Callable[[], Any],
    *,
    timeout: float = DEFAULT_TIMEOUT,
    interval: float = 0.01,
    what: str = "condition",
) -> Any:
    """Blocking twin of :func:`wait_until` for threaded or subprocess observation."""
    deadline = time.monotonic() + timeout
    while True:
        value = predicate()
        if value:
            return value
        if time.monotonic() >= deadline:
            raise AssertionError(f"timed out after {timeout}s waiting for {what}")
        time.sleep(interval)
