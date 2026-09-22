"""Owned store opening keeps resource cleanup inside the cancelled operation."""

import asyncio
import threading

import pytest

from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.session.store_open import open_owned_store


async def test_successful_open_transfers_live_store_off_loop(tmp_path):
    threads = []

    def opener():
        threads.append(threading.get_ident())
        return SessionStore(str(tmp_path / "opened.kohakutr"))

    store = await open_owned_store(opener)
    try:
        await store.run(store.append_event, "ag", "text", {"content": "owned"})
        assert (await store.run(store.get_events, "ag"))[0]["content"] == "owned"
        assert threads and threading.get_ident() not in threads
    finally:
        await asyncio.to_thread(store.close)


async def test_open_error_reaches_caller():
    error = OSError("cannot open")

    def opener():
        raise error

    with pytest.raises(OSError) as exc:
        await open_owned_store(opener)
    assert exc.value is error


async def test_open_error_after_cancellation_keeps_cancelled_outcome(caplog):
    entered = threading.Event()
    release = threading.Event()

    def opener():
        entered.set()
        assert release.wait(5)
        raise OSError("cannot open")

    task = asyncio.create_task(open_owned_store(opener))
    try:
        assert await asyncio.to_thread(entered.wait, 3)
        task.cancel()
        await asyncio.sleep(0)
    finally:
        release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert "Session open failed after cancellation" in caplog.text


async def test_repeated_cancellation_waits_for_off_loop_close(tmp_path, monkeypatch):
    entered = threading.Event()
    opened = threading.Event()
    closing = threading.Event()
    release_close = threading.Event()
    close_threads = []
    stores = []
    original = SessionStore.close

    def opener():
        store = SessionStore(str(tmp_path / "cancelled.kohakutr"))
        store.init_meta("s", "agent", "config", str(tmp_path), ["ag"])
        stores.append(store)
        entered.set()
        assert opened.wait(5)
        return store

    def gated_close(self, **kwargs):
        close_threads.append(threading.get_ident())
        closing.set()
        assert release_close.wait(5)
        original(self, **kwargs)

    monkeypatch.setattr(SessionStore, "close", gated_close)
    task = asyncio.create_task(open_owned_store(opener))
    try:
        assert await asyncio.to_thread(entered.wait, 3)
        task.cancel()
        opened.set()
        assert await asyncio.to_thread(closing.wait, 3)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
    finally:
        opened.set()
        release_close.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        for store in stores:
            original(store, update_status=False)
    assert stores[0].events._closed
    assert threading.get_ident() not in close_threads
