"""Native release certainty includes retained background workers and primary errors."""

import threading

import pytest

import kohakuterrarium.session.memory as memory_module
import kohakuterrarium.session.store.lifecycle.handle as handle_module
from kohakuterrarium.session.memory import SessionMemory
from kohakuterrarium.session.resource_gate import try_fast
from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.session.store.lifecycle.lock import (
    TABLE_ATTRS,
    discard_partial_open,
)
from kohakuterrarium.session.vault_handles import dispose


@pytest.mark.parametrize("partial", [False, True])
def test_store_daemon_timeout_does_not_enable_new(tmp_path, partial):
    path = tmp_path / "daemon.kohakutr"
    store = SessionStore(path)
    entered, release = threading.Event(), threading.Event()
    events = store.events
    events.disable_cache()
    original = events.flush_cache

    def blocked_flush():
        if threading.current_thread() is events._daemon_thread:
            native = events._inner
            entered.set()
            assert release.wait(15)
            assert native is not None
        return original()

    events.flush_cache = blocked_flush
    events.enable_cache(flush_interval=0.01)
    daemon = events._daemon_thread
    try:
        assert entered.wait(5)
        if partial:
            released = discard_partial_open(
                [(name, getattr(store, name)) for name in (*TABLE_ATTRS, "fts")],
                str(path),
            )
            assert not released
        else:
            store.close(update_status=False)
            assert daemon.is_alive()
            attempt = try_fast(path, eligible=True)
            if attempt.lease is not None:
                attempt.lease.finish(source_released=True)
            assert attempt.reason == "legacy_release_uncertain"
    finally:
        release.set()
        daemon.join(5)
        assert not daemon.is_alive()
        if partial:
            store._readonly = True
            store.close(update_status=False)
        else:
            store.close(update_status=False)


def test_returned_memory_handle_tracking_failure_retains_uncertainty(tmp_path):
    path = tmp_path / "tracking.kohakutr"
    memory = SessionMemory(str(path))
    opened = []

    class RejectAppend(list):
        def append(self, value):
            opened.append(value)
            raise MemoryError("track failed")

    memory._resources._handles = RejectAppend(memory._resources._handles)
    try:
        with pytest.raises(MemoryError):
            memory._resources.open(memory_module.TextVault, str(path), table="extra")
        memory.close()
        assert try_fast(path, eligible=True).reason == "legacy_release_uncertain"
    finally:
        memory.close()
        for table in opened:
            dispose(table)


def test_primary_error_survives_secondary_store_close_failure(tmp_path, monkeypatch):
    store = SessionStore(tmp_path / "primary.kohakutr")
    original_close = store._close_enrolled_resources

    class Interrupted(BaseException):
        pass

    # Ordinary close-body failures are logged; an interrupt escapes them.
    primary = Interrupted("primary persistence interrupt")
    secondary = RuntimeError("secondary disposal failure")

    def persist(*args):
        raise primary

    def cleanup():
        original_close()
        raise secondary

    monkeypatch.setattr(handle_module, "persist_event_counter", persist)
    monkeypatch.setattr(store, "_close_enrolled_resources", cleanup)
    with pytest.raises(Interrupted) as caught:
        store.close(update_status=False)
    assert caught.value is primary
    assert caught.value.__cause__ is secondary
