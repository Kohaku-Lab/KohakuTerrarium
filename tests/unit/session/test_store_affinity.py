"""Unit tests for SessionStore affinity-thread dispatch."""

import asyncio
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from kohakuterrarium.session import store_affinity as store_affinity_mod
from kohakuterrarium.session.store import SessionStore


def _gaps(stamps: list[float]) -> list[float]:
    return [stamps[i + 1] - stamps[i] for i in range(len(stamps) - 1)]


async def test_run_does_not_block_event_loop(tmp_path):
    store = SessionStore(str(tmp_path / "aff.kohakutr"))
    loop_alive: list[float] = []
    stop = asyncio.Event()

    def _block():
        time.sleep(0.3)
        return threading.get_ident()

    async def _ping():
        while not stop.is_set():
            loop_alive.append(time.monotonic())
            await asyncio.sleep(0.02)
        loop_alive.append(time.monotonic())

    try:
        ping = asyncio.create_task(_ping())
        await asyncio.sleep(0)
        worker_ident = await store.run(_block)
        stop.set()
        await ping
    finally:
        store.close()

    assert worker_ident != threading.get_ident()
    gaps = _gaps(loop_alive)
    assert max(gaps) < 0.15, f"store.run blocked the loop; max gap={max(gaps):.3f}s"


async def test_run_serializes_on_one_worker(tmp_path):
    store = SessionStore(str(tmp_path / "serial.kohakutr"))
    idents: list[int] = []

    def _mark():
        idents.append(threading.get_ident())

    try:
        await asyncio.gather(store.run(_mark), store.run(_mark), store.run(_mark))
    finally:
        store.close()

    assert len(idents) == 3
    assert len(set(idents)) == 1


async def test_run_after_close_raises(tmp_path):
    store = SessionStore(str(tmp_path / "closed.kohakutr"))
    store.close()
    try:
        await store.run(lambda: None)
        raise AssertionError("run on a closed store must fail")
    except RuntimeError as exc:
        assert "closed" in str(exc).lower()


def test_concurrent_first_dispatch_creates_one_executor(tmp_path, monkeypatch):
    store = SessionStore(str(tmp_path / "race.kohakutr"))
    real_tpe = ThreadPoolExecutor

    def slow_executor(*args, **kwargs):
        # Widen the creation window so all eight threads are genuinely
        # contended around the first executor construction.
        time.sleep(0.05)
        return real_tpe(*args, **kwargs)

    monkeypatch.setattr(store_affinity_mod, "ThreadPoolExecutor", slow_executor)
    barrier = threading.Barrier(8)
    executors: list[threading.ThreadPoolExecutor] = []

    def _create():
        barrier.wait()
        executors.append(store._ensure_affinity())

    threads = [threading.Thread(target=_create) for _ in range(8)]
    try:
        for t in threads:
            t.start()
        for t in threads:
            t.join()
    finally:
        monkeypatch.undo()
        store.close()

    # All racing threads share one executor, and exactly one shutdown hook
    # was registered with the store's companion closers.
    assert len({id(e) for e in executors}) == 1
    closers = [
        c
        for c in store._companion_closers
        if getattr(c, "__name__", "") == "_shutdown_affinity"
    ]
    assert len(closers) == 1


async def test_submit_queues_without_await_and_rejects_after_close(tmp_path):
    store = SessionStore(str(tmp_path / "submit.kohakutr"))
    store.init_meta("a", "agent", "/p", "/w", ["a"])
    try:
        seen: list[int] = []
        fut = store.submit(store.append_event, "a", "user_message", {"i": 1})
        fut2 = store.submit(lambda: seen.append(7))
        fut.result(timeout=5)
        fut2.result(timeout=5)
        assert seen == [7]
        assert len(store.get_events("a")) == 1
    finally:
        store.close()
    try:
        store.submit(lambda: None)
        raise AssertionError("submit on a closed store must fail")
    except RuntimeError as exc:
        assert "closed" in str(exc).lower()


@pytest.mark.parametrize("dispatch", ["run", "submit"])
@pytest.mark.parametrize("warm", [False, True])
def test_close_serializes_with_dispatch_acceptance(
    tmp_path, monkeypatch, dispatch, warm
):
    store = SessionStore(tmp_path / "closing.kohakutr")
    store.state["sentinel"] = "kept"
    if warm:
        store.submit(lambda: None).result(timeout=5)
    entered = threading.Event()
    release = threading.Event()
    closed = threading.Event()
    original = store._ensure_affinity
    values, errors = [], []

    def gated_ensure():
        entered.set()
        assert release.wait(5)
        return original()

    def request():
        try:
            if dispatch == "run":
                values.append(asyncio.run(store.run(store.state.get, "sentinel")))
            else:
                values.append(store.submit(store.state.get, "sentinel").result(5))
        except Exception as exc:
            errors.append(exc)

    def close():
        try:
            store.close(update_status=False)
        finally:
            closed.set()

    monkeypatch.setattr(store, "_ensure_affinity", gated_ensure)
    requester = threading.Thread(target=request, daemon=True)
    closer = threading.Thread(target=close, daemon=True)
    try:
        requester.start()
        assert entered.wait(5)
        closer.start()
        closed.wait(0.25)
        release.set()
        requester.join(5)
        closer.join(5)
        assert not requester.is_alive() and not closer.is_alive()
        if errors:
            assert len(errors) == 1
            assert isinstance(errors[0], RuntimeError)
            assert str(errors[0]) == "SessionStore is closed"
        else:
            assert values == ["kept"]
        assert getattr(store, "_affinity", None) is None
        with pytest.raises(RuntimeError, match="SessionStore is closed"):
            store.submit(lambda: None)
    finally:
        release.set()
        requester.join(5)
        if closer.ident is not None:
            closer.join(5)
        store._shutdown_affinity()
        store.close(update_status=False)


def test_close_from_worker_is_rejected_without_disposing_store(tmp_path):
    store = SessionStore(tmp_path / "self-close.kohakutr")
    try:
        with pytest.raises(RuntimeError, match="affinity worker"):
            store.submit(store.close).result(timeout=5)
        store.submit(store.state.put, "still-open", "value").result(timeout=5)
        assert store.submit(store.state.get, "still-open").result(timeout=5) == "value"
    finally:
        store.close(update_status=False)
