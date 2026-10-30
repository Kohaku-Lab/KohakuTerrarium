"""Success callbacks cannot replace an already pending request cancellation."""

import asyncio

import pytest

from kohakuterrarium.session.occupancy.mutation import await_mutation


@pytest.mark.parametrize("repeated_cancel", [False, True])
async def test_callback_failure_preserves_cancellation_and_callback_cause(
    repeated_cancel,
):
    started, release = asyncio.Event(), asyncio.Event()
    callback_root = OSError("callback underlying failure")
    callback_error = ValueError("callback failed")
    callback_error.__cause__ = callback_root
    calls = []

    async def operation():
        started.set()
        await release.wait()
        return "published"

    def callback():
        calls.append("completed")
        raise callback_error

    inner = asyncio.create_task(operation())
    outer = asyncio.create_task(await_mutation(inner, on_success=callback))
    await started.wait()
    await asyncio.sleep(0)
    outer.cancel("request disconnected")
    await asyncio.sleep(0)
    if repeated_cancel:
        outer.cancel("cancel again")
        await asyncio.sleep(0)
    assert not outer.done()
    release.set()
    with pytest.raises(asyncio.CancelledError) as caught:
        await outer
    assert caught.value.args == ("request disconnected",)
    assert caught.value.__cause__ is callback_error
    assert callback_error.__cause__ is callback_root
    assert outer.cancelled()
    assert inner.result() == "published"
    assert calls == ["completed"]


async def test_uncancelled_callback_error_is_not_wrapped():
    failure = ValueError("callback failed")

    def callback():
        raise failure

    task = asyncio.create_task(asyncio.sleep(0, result="published"))
    with pytest.raises(ValueError) as caught:
        await await_mutation(task, on_success=callback)
    assert caught.value is failure
    assert task.result() == "published"
