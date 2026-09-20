"""Unit tests for SessionStore affinity-thread dispatch."""

import asyncio
import threading
import time
from concurrent.futures import ThreadPoolExecutor

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
