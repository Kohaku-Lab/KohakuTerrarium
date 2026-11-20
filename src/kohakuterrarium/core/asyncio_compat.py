"""Compatibility helpers for task cancellation requests."""

from __future__ import annotations

import asyncio
from weakref import WeakKeyDictionary

_requested_tasks: WeakKeyDictionary[asyncio.Task, bool] = WeakKeyDictionary()


def cancellation_requested(task: asyncio.Task | None) -> bool:
    """Return whether cancellation has been requested for a task."""
    if task is None:
        return False
    cancelling = getattr(task, "cancelling", None)
    if callable(cancelling):
        return bool(cancelling())
    return _requested_tasks.get(task, False)


def request_cancellation(task: asyncio.Task) -> bool:
    """Request task cancellation once and report whether a request was sent."""
    if cancellation_requested(task):
        return False
    _requested_tasks[task] = True
    task.cancel()
    return True
