"""Unit tests for ``kohakuterrarium.browser.tasks``."""

import asyncio
import gc
import time

import pytest

from kohakuterrarium.browser import tasks as tasks_module
from kohakuterrarium.browser.tasks import discard, outlast


async def test_discard_cancels_and_retrieves_a_late_failure():
    seen = []
    loop = asyncio.get_running_loop()
    loop.set_exception_handler(lambda _loop, context: seen.append(context))

    async def stubborn():
        try:
            await asyncio.Event().wait()
        finally:
            raise RuntimeError("cleanup failed")

    task = asyncio.create_task(stubborn())
    await asyncio.sleep(0)
    discard(task)
    await asyncio.sleep(0.01)
    assert task.done() and not task.cancelled()
    del task
    gc.collect()
    await asyncio.sleep(0)
    loop.set_exception_handler(None)
    assert seen == []


async def test_discard_of_a_plain_cancel():
    task = asyncio.create_task(asyncio.Event().wait())
    await asyncio.sleep(0)
    discard(task)
    await asyncio.sleep(0)
    assert task.cancelled()


async def finish_after(done: list, seconds: float) -> None:
    await asyncio.sleep(seconds)
    done.append(True)


async def test_outlast_returns_once_an_uncancelled_operation_ends():
    done: list = []
    await outlast(finish_after(done, 0.05), 5.0)
    assert done == [True]


@pytest.mark.parametrize("cancels", [1, 2])
async def test_outlast_finishes_the_operation_then_raises_a_cancel(cancels):
    done: list = []
    caller = asyncio.create_task(outlast(finish_after(done, 0.3), 5.0))
    for _ in range(cancels):
        await asyncio.sleep(0.05)
        caller.cancel()
    with pytest.raises(asyncio.CancelledError):
        await caller
    assert done == [True], "the operation was stopped by the cancel"


async def test_outlast_returns_at_its_bound_and_keeps_the_operation_running():
    done: list = []
    started = time.monotonic()
    await outlast(finish_after(done, 0.5), 0.1)
    assert time.monotonic() - started < 0.4 and done == []
    running = [task for task in tasks_module._outlasting if not task.done()]
    assert len(running) == 1, "the operation running past the bound is not kept"
    await asyncio.wait(running)
    assert done == [True] and not tasks_module._outlasting


async def test_outlast_leaves_the_operation_s_exception_on_it_and_returns():
    operation = asyncio.get_running_loop().create_future()
    operation.set_exception(RuntimeError("cleanup failed"))
    assert await outlast(operation, 5.0) is None
    assert isinstance(operation.exception(), RuntimeError)
