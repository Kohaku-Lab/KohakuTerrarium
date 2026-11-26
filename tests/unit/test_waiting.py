"""``tests.helpers.waiting.finish_within``: a call's own errors versus the test's limit."""

import asyncio
import time

import pytest

from kohakuterrarium.browser.errors import BrowserTimeoutError
from tests.helpers.waiting import finish_within


async def raising(error: BaseException, after: float = 0.01):
    await asyncio.sleep(after)
    raise error


@pytest.mark.parametrize(
    "error",
    [
        BrowserTimeoutError("opening a tab to read in timed out after 5 s"),
        TimeoutError("the call's own timeout"),
    ],
)
async def test_a_timeout_the_call_raises_reaches_the_caller_as_itself(error):
    started = time.monotonic()
    with pytest.raises(type(error)) as info:
        await finish_within(raising(error), "session.read_page", 30.0)
    assert info.value is error
    assert time.monotonic() - started < 5.0


async def test_other_errors_and_results_pass_through():
    with pytest.raises(ValueError, match="bad"):
        await finish_within(raising(ValueError("bad")), "call", 30.0)

    async def value():
        return 42

    assert await finish_within(value(), "call", 30.0) == 42


async def test_a_call_past_the_limit_fails_and_is_cancelled():
    ended = asyncio.Event()

    async def forever():
        try:
            await asyncio.Event().wait()
        finally:
            ended.set()

    started = time.monotonic()
    with pytest.raises(
        AssertionError, match=r"^slow call did not finish within 0.2 s$"
    ):
        await finish_within(forever(), "slow call", 0.2)
    assert ended.is_set()
    assert time.monotonic() - started < 5.0


async def test_cancelling_the_wait_cancels_the_call():
    ended = asyncio.Event()

    async def forever():
        try:
            await asyncio.Event().wait()
        finally:
            ended.set()

    waiting = asyncio.ensure_future(finish_within(forever(), "call", 30.0))
    await asyncio.sleep(0.05)
    waiting.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiting
    assert ended.is_set()
