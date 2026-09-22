"""Unit tests for SessionStore affinity-thread dispatch."""

import asyncio
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from kohakuterrarium.session import store_affinity as store_affinity_mod
from kohakuterrarium.core.events import TriggerEvent
from kohakuterrarium.core.trigger_manager import TriggerManager
from kohakuterrarium.session.attachment_service import _emit_lineage
from kohakuterrarium.session.output import SessionOutput
from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.modules.trigger.timer import TimerTrigger


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


@pytest.mark.parametrize("cancel_when", ["queued", "running"])
async def test_cancelled_run_keeps_accepted_write_in_fifo(tmp_path, cancel_when):
    path = tmp_path / "cancelled-write.kohakutr"
    store = SessionStore(path)
    store.append_event("alice", "user_message", {"content": "before"})
    release = threading.Event()
    started = asyncio.Event()
    loop = asyncio.get_running_loop()
    task = None

    def write():
        if cancel_when == "running":
            loop.call_soon_threadsafe(started.set)
            assert release.wait(5), "running write was not released"
        return store.append_event(
            "alice", "user_message", {"content": "accepted"}, turn_index=2, branch_id=1
        )

    try:
        if cancel_when == "queued":
            store.submit(release.wait, 5)
        task = asyncio.create_task(store.run(write))
        # run submits synchronously up to its first await: the next loop turn
        # guarantees acceptance before cancellation, without polling internals.
        await asyncio.sleep(0)
        if cancel_when == "running":
            await asyncio.wait_for(started.wait(), timeout=2)
        later = store.submit(
            store.append_event, "alice", "user_message", {"content": "after"}
        )
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=1)
        assert not release.is_set()
        assert not later.done(), "cancellation must return while work is still blocked"
    finally:
        release.set()
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)
        await asyncio.to_thread(store.close, update_status=False)
    reopened = SessionStore(path)
    try:
        events = reopened.get_events("alice")
        assert [e["content"] for e in events] == ["before", "accepted", "after"]
        assert events[1]["turn_index"] == 2
        assert events[1]["branch_id"] == 1
    finally:
        reopened.close(update_status=False)


def test_cancelled_run_survives_waiter_event_loop_shutdown(tmp_path):
    path = tmp_path / "closed-loop.kohakutr"
    store = SessionStore(path)
    release = threading.Event()
    store.submit(release.wait, 5)

    async def queue_and_cancel():
        task = asyncio.create_task(
            store.run(store.append_event, "alice", "user_message", {"content": "saved"})
        )
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    try:
        asyncio.run(queue_and_cancel())
    finally:
        # The caller's loop is gone. Store.close still owns the accepted write
        # and must drain it before disposing the native database handles.
        release.set()
        store.close(update_status=False)
    reopened = SessionStore(path)
    try:
        assert [e["content"] for e in reopened.get_events("alice")] == ["saved"]
    finally:
        reopened.close(update_status=False)


def _emit_live_event(store, producer):
    if producer == "schedule_drift":
        manager = TriggerManager(lambda event: None)
        manager._session_store = store
        manager._agent_name = "root"
        manager._maybe_emit_schedule_drift(
            "timer-1",
            TimerTrigger(interval=60),
            TriggerEvent(type="timer", context={"scheduled_at": time.time() - 5}),
        )
    else:
        _emit_lineage(
            store,
            "root",
            event_type=producer,
            agent_name="helper",
            role="reviewer",
            attach_seq=0,
            attached_by="root",
            session_id="s",
        )


@pytest.mark.parametrize(
    "producer", ["schedule_drift", "agent_attached", "agent_detached"]
)
async def test_live_producers_preserve_queued_output(tmp_path, producer):
    store = SessionStore(tmp_path / "mixed-writers.kohakutr")
    output = SessionOutput("root", store, None)
    loop = asyncio.get_running_loop()
    allocated = asyncio.Event()
    release = threading.Event()
    counter_code = SessionStore._next_event_seq.__code__
    gated = False

    def pause_allocation(frame, event, arg):
        nonlocal gated
        if frame.f_code is not counter_code:
            return None
        if event == "line" and "seq" in frame.f_locals and not gated:
            gated = True
            loop.call_soon_threadsafe(allocated.set)
            assert release.wait(5), "event allocation was not released"
        return pause_allocation

    try:
        await store.run(sys.settrace, pause_allocation)
        output._append_event("text_chunk", {"content": "queued output"})
        await asyncio.wait_for(allocated.wait(), timeout=3)
        _emit_live_event(store, producer)
        release.set()
        await output.drain()
        await store.run(store.flush)
        rows = await store.run(
            lambda: [(key, store.events[key]) for key in sorted(store.events.keys())]
        )
        assert [key for key, _ in rows] == [b"root:e000000", b"root:e000001"]
        assert [event["type"] for _, event in rows] == ["text_chunk", producer]
        assert [event["event_id"] for _, event in rows] == [1, 2]
        assert rows[0][1]["content"] == "queued output"
        if producer == "schedule_drift":
            assert rows[1][1]["trigger_id"] == "timer-1"
        else:
            assert rows[1][1]["agent_name"] == "helper"
    finally:
        release.set()
        await store.run(sys.settrace, None)
        await asyncio.to_thread(store.close, update_status=False)


@pytest.mark.parametrize(
    "producer", ["schedule_drift", "agent_attached", "agent_detached"]
)
@pytest.mark.parametrize("failure", ["write", "closed"])
async def test_live_producer_failures_are_logged(
    tmp_path, monkeypatch, caplog, producer, failure
):
    store = SessionStore(tmp_path / "failed-producer.kohakutr")

    def fail_write(*args, **kwargs):
        raise OSError("storage unavailable")

    try:
        if failure == "closed":
            store.close(update_status=False)
        else:
            monkeypatch.setattr(store, "append_event", fail_write)
        _emit_live_event(store, producer)
        if failure == "write":
            await store.run(lambda: None)
        expected = (
            "schedule_drift emit failed"
            if producer == "schedule_drift"
            else "Lineage event emit failed"
        )
        assert expected in caplog.text
        assert (
            "storage unavailable" if failure == "write" else "SessionStore is closed"
        ) in caplog.text
    finally:
        await asyncio.to_thread(store.close, update_status=False)
