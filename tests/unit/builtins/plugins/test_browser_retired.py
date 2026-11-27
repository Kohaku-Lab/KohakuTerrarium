"""Unit tests for ``builtins.plugins.browser.retired``: background closes the plugin waits for."""

import asyncio
import time

from kohakuterrarium.builtins.plugins.browser import retired as retired_module
from kohakuterrarium.builtins.plugins.browser.retired import RetiredSessions
from tests.unit.browser.test_session import make


async def launched(tmp_path, **kwargs):
    session, driver = make(tmp_path, **kwargs)
    await session.snapshot()
    return session, driver


async def test_settle_waits_for_a_close_in_progress(tmp_path):
    session, driver = await launched(tmp_path)
    driver.browser.hang_stages = {"context", "browser", "playwright"}
    retired = RetiredSessions()
    retired.retire(session)
    assert driver.browser.alive
    await retired.settle()
    assert session.state == "closed" and driver.alive() == []


async def test_settle_with_nothing_retired_returns_at_once():
    start = time.perf_counter()
    await RetiredSessions().settle()
    assert time.perf_counter() - start < 0.1


async def test_a_session_retired_without_a_loop_closes_at_the_next_settle(
    tmp_path, monkeypatch, caplog
):
    session, driver = await launched(tmp_path)
    retired = RetiredSessions()

    def no_loop():
        raise RuntimeError("no running event loop")

    monkeypatch.setattr(retired_module.asyncio, "get_running_loop", no_loop)
    retired.retire(session)
    monkeypatch.undo()
    assert "deferred" in caplog.text
    assert session.state == "ready" and len(driver.alive()) == 1
    await retired.settle()
    assert session.state == "closed" and driver.alive() == []


async def test_settle_is_bounded_by_the_close_timeout(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(retired_module, "SETTLE_MARGIN_S", 0.0)
    session, _ = await launched(tmp_path)
    session.close_timeout = 0.05
    never = asyncio.Event()

    async def stuck_close():
        await never.wait()

    session.close = stuck_close
    retired = RetiredSessions()
    retired.retire(session)
    start = time.perf_counter()
    await retired.settle()
    assert time.perf_counter() - start < 1.0
    assert "did not finish closing in time" in caplog.text
    never.set()
    await asyncio.sleep(0)


async def test_a_failing_close_is_logged_and_forgotten(tmp_path, caplog):
    session, driver = await launched(tmp_path)

    async def broken_close():
        raise RuntimeError("boom")

    real_close = session.close
    session.close = broken_close
    retired = RetiredSessions()
    retired.retire(session)
    await retired.settle()
    await asyncio.sleep(0)
    assert "Closing a browser session failed" in caplog.text
    await retired.settle()
    await real_close()
    assert driver.alive() == []
