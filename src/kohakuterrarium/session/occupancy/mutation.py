"""Cancellation-safe completion of session file mutation workers."""

import asyncio
from collections.abc import Callable
from typing import TypeVar

from kohakuterrarium.session.occupancy.family import FamilyLease

T = TypeVar("T")


async def await_mutation(
    task: asyncio.Task[T], *, on_success: Callable[[], None] | None = None
) -> T:
    """Drain an admitted mutation task before propagating caller cancellation."""
    try:
        result = await asyncio.shield(task)
    except asyncio.CancelledError as cancelled:
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
            except BaseException as failure:
                raise cancelled from failure
        if not task.cancelled():
            if (failure := task.exception()) is not None:
                raise cancelled from failure
            if on_success is not None:
                try:
                    on_success()
                except BaseException as failure:
                    raise cancelled from failure
        raise
    if on_success is not None:
        on_success()
    return result


async def run_admitted_mutation(
    admit: Callable[[], FamilyLease], mutation: Callable[[], T]
) -> T:
    """Wait off-loop, then validate and mutate synchronously on the caller's loop."""
    task = asyncio.create_task(asyncio.to_thread(admit))
    try:
        lease = await await_mutation(task)
    except BaseException:
        if task.done() and not task.cancelled() and task.exception() is None:
            task.result().finish(source_released=True)
        raise
    try:
        return mutation()
    finally:
        lease.finish(source_released=True)


async def run_mutation_worker(
    fn: Callable[..., T],
    /,
    *args: object,
    on_success: Callable[[], None] | None = None,
) -> T:
    """Run a worker-owned file mutation off-loop and drain it on cancellation."""
    task = asyncio.create_task(asyncio.to_thread(fn, *args))
    return await await_mutation(task, on_success=on_success)
