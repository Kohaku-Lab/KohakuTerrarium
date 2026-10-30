"""Recovery checkpoints stay bounded without crossing durability barriers."""

import threading
from concurrent.futures import Future

import pytest

from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.session.output.text_buffer import OpenTextSegment


def test_busy_writer_retains_one_latest_checkpoint(tmp_path):
    store = SessionStore(tmp_path / "busy.kohakutr")
    release = threading.Event()
    started = threading.Event()
    submitted = []

    def busy():
        started.set()
        assert release.wait(5)

    def submit(fn, *args, **kwargs):
        future = store.submit(fn, *args, **kwargs)
        submitted.append(future)
        return future

    store.submit(busy)
    assert started.wait(5)
    try:
        segment = OpenTextSegment(store, "alice", submit=submit)
        for _ in range(512):
            segment.append("x" * 512)
        assert len(submitted) == 1
        release.set()
        store.submit(lambda: None).result(timeout=5)
        assert store.state["alice:open_text"] == "x" * (512 * 512)
        segment.append("y" * 512)
        store.submit(lambda: None).result(timeout=5)
        assert segment.recover() == "x" * (512 * 512) + "y" * 512
    finally:
        release.set()
        store.close()


@pytest.mark.parametrize("clear", [False, True])
def test_pending_checkpoints_do_not_cross_clear_or_take(tmp_path, clear):
    store = SessionStore(tmp_path / "barriers.kohakutr")
    release = threading.Event()
    store.submit(lambda: release.wait(5))
    try:
        segment = OpenTextSegment(store, "alice", submit=store.submit)
        old = "old" * 512
        new = "new" * 512
        segment.append(old)
        before_clear = store.submit(lambda: store.state["alice:open_text"])
        assert segment.take() == old
        event = store.submit(
            store.append_event, "alice", "text_chunk", {"content": old}
        )
        if clear:
            segment.clear(after=event)
        after_clear = store.submit(lambda: store.state["alice:open_text"])
        segment.append(new)
        release.set()
        store.submit(lambda: None).result(timeout=5)
        assert before_clear.result() == old
        assert after_clear.result() == ("" if clear else old)
        assert segment.recover() == new
        assert store.get_events("alice")[0]["content"] == old
    finally:
        release.set()
        store.close()


@pytest.mark.parametrize("failed_future", [False, True])
def test_rejected_submission_does_not_strand_later_text(tmp_path, failed_future):
    store = SessionStore(tmp_path / "rejected.kohakutr")
    attempts = 0

    def submit(fn, *args, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            if failed_future:
                future = Future()
                future.set_exception(RuntimeError("submission rejected"))
                return future
            raise RuntimeError("submission rejected")
        return store.submit(fn, *args, **kwargs)

    try:
        segment = OpenTextSegment(store, "alice", submit=submit)
        if failed_future:
            segment.append("a" * 512)
        else:
            with pytest.raises(RuntimeError, match="submission rejected"):
                segment.append("a" * 512)
        segment.append("b" * 512)
        store.submit(lambda: None).result(timeout=5)
        assert segment.recover() == "a" * 512 + "b" * 512
    finally:
        store.close()


@pytest.mark.parametrize("method", ["clear", "retire"])
def test_failed_event_preserves_coalesced_recovery(tmp_path, method):
    store = SessionStore(tmp_path / "failed.kohakutr")
    release = threading.Event()
    store.submit(lambda: release.wait(5))

    def fail():
        raise OSError("event write failed")

    try:
        segment = OpenTextSegment(store, "alice", submit=store.submit)
        segment.append("a" * 512)
        segment.append("b" * 512)
        segment.take()
        event = store.submit(fail)
        getattr(segment, method)(after=event)
        release.set()
        store.submit(lambda: None).result(timeout=5)
        assert segment.recover() == "a" * 512 + "b" * 512
    finally:
        release.set()
        store.close()
