"""Unit tests for ``kohakuterrarium.browser.settle``: grace, quiescence, cap, navigations."""

import asyncio
import time

import pytest

from kohakuterrarium.browser.errors import (
    ContextDestroyedError,
    DriverDisconnectedError,
    DriverError,
    DriverTimeoutError,
    PageClosedError,
)
from kohakuterrarium.browser.settle import QUIET_JS, settle


class Page:
    """Answers ``evaluate`` from a list of outcomes (values, exceptions, or ``hang``)."""

    def __init__(self, *outcomes, load=None):
        self.outcomes = list(outcomes)
        self.load = load
        self.calls = []

    async def evaluate(self, expression, arg=None, *, ref=None, timeout):
        assert expression == QUIET_JS
        self.calls.append(("evaluate", arg, timeout))
        outcome = self.outcomes.pop(0) if self.outcomes else "quiet"
        if outcome == "hang":
            await asyncio.Event().wait()
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    async def wait_for_load(self, timeout):
        self.calls.append(("load", timeout))
        if isinstance(self.load, BaseException):
            raise self.load
        if self.load == "hang":
            await asyncio.Event().wait()


async def test_quiet_page_returns_after_the_grace():
    page = Page("quiet")
    started = time.monotonic()
    result = await settle(page, asyncio.Event(), grace=0.05, quiet_ms=150, cap=2.0)
    assert result.reason == "quiet" and not result.navigated
    assert time.monotonic() - started >= 0.04
    [(kind, arg, timeout)] = page.calls
    assert kind == "evaluate" and arg[0] == 150 and 1800 < arg[1] <= 2000
    assert timeout <= 2.05


async def test_navigation_waits_for_the_load_first():
    page = Page("quiet")
    event = asyncio.Event()
    event.set()
    result = await settle(page, event, grace=1.0, cap=2.0)
    assert result.navigated and result.reason == "quiet"
    assert [c[0] for c in page.calls] == ["load", "evaluate"]
    assert result.elapsed < 0.5


async def test_context_destroyed_mid_wait_is_retried_not_surfaced():
    page = Page(ContextDestroyedError("gone"), "quiet")
    result = await settle(page, asyncio.Event(), grace=0.001, cap=2.0)
    assert result.reason == "quiet" and result.navigated
    assert [c[0] for c in page.calls] == ["evaluate", "load", "evaluate"]


async def test_repeated_navigation_gives_up_at_the_attempt_limit():
    errors = [ContextDestroyedError("gone") for _ in range(5)]
    page = Page(*errors)
    result = await settle(page, asyncio.Event(), grace=0.001, cap=2.0)
    assert result.reason == "cap" and result.navigated
    assert sum(1 for c in page.calls if c[0] == "evaluate") == 3


async def test_animated_page_hits_the_cap():
    page = Page("cap")
    assert (await settle(page, asyncio.Event(), grace=0.001, cap=0.2)).reason == "cap"


async def test_hung_script_is_bounded_by_the_cap():
    page = Page("hang")
    started = time.monotonic()
    result = await settle(page, asyncio.Event(), grace=0.001, cap=0.2)
    assert result.reason == "cap" and time.monotonic() - started < 0.6


async def test_hung_load_is_bounded_by_the_cap():
    event = asyncio.Event()
    event.set()
    page = Page("quiet", load="hang")
    started = time.monotonic()
    result = await settle(page, event, grace=0.001, cap=0.2)
    assert time.monotonic() - started < 0.6
    assert result.reason == "cap" and result.navigated


@pytest.mark.parametrize(
    "outcome,reason",
    [
        (DriverTimeoutError("timed out"), "cap"),
        (DriverError("boom"), "error"),
        ({"weird": 1}, "error"),
    ],
)
async def test_other_failures_end_the_wait(outcome, reason):
    result = await settle(Page(outcome), asyncio.Event(), grace=0.001, cap=1.0)
    assert result.reason == reason


@pytest.mark.parametrize(
    "error", [DriverDisconnectedError("dead"), PageClosedError("closed")]
)
async def test_browser_or_tab_loss_propagates(error):
    with pytest.raises(type(error)):
        await settle(Page(error), asyncio.Event(), grace=0.001, cap=1.0)
    event = asyncio.Event()
    event.set()
    with pytest.raises(type(error)):
        await settle(Page("quiet", load=error), event, grace=0.001, cap=1.0)


async def test_load_errors_are_tolerated():
    event = asyncio.Event()
    event.set()
    page = Page("quiet", load=DriverError("load failed"))
    assert (await settle(page, event, grace=0.001, cap=1.0)).reason == "quiet"
    page = Page("quiet", load=ContextDestroyedError("again"))
    assert (await settle(page, event, grace=0.001, cap=1.0)).reason == "quiet"


async def test_no_time_left_skips_the_script():
    ticks = iter([0.0, 5.0, 5.0, 5.0, 5.0, 5.0])
    page = Page("quiet")
    result = await settle(
        page, asyncio.Event(), grace=0.001, cap=2.0, clock=lambda: next(ticks)
    )
    assert result.reason == "cap" and page.calls == []
