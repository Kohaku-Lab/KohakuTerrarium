"""Unit tests for ``kohakuterrarium.browser.tasks``."""

import asyncio
import gc

from kohakuterrarium.browser.tasks import discard


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
