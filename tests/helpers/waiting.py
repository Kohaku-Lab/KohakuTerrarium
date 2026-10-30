"""Deadline-based waiting for tests that observe background work."""

import asyncio
import time
from collections.abc import Callable
from typing import Any

DEFAULT_TIMEOUT = 60.0


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
