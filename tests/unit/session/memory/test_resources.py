"""Memory occupancy spans native initialization, lazy opens and disposal."""

import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

import kohakuterrarium.session.memory.index as memory_module
from kohakuterrarium.session.memory.index import SessionMemory
from kohakuterrarium.session.occupancy.gate import GATE, try_fast
from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.session.vault.handles import dispose, is_disposed


def assert_idle(path):
    attempt = try_fast(path, eligible=True)
    assert attempt.lease is not None, attempt.reason
    attempt.lease.finish(source_released=True)


def test_memory_only_and_store_can_overlap(tmp_path):
    path = tmp_path / "memory.kohakutr"
    memory = SessionMemory(str(path))
    store = SessionStore(path)
    try:
        assert GATE.snapshot(path).legacy_active == 2
        memory.index_events("a", [{"type": "user_input", "content": "needle"}])
        assert memory.search("needle")
        store.close(update_status=False)
        assert try_fast(path, eligible=True).reason == "legacy_enrolled"
    finally:
        memory.close()
        store.close(update_status=False)
    assert_idle(path)


@pytest.mark.parametrize("operation", ["close", "drop"])
@pytest.mark.parametrize("error_type", [RuntimeError, KeyboardInterrupt])
def test_native_drop_faults_keep_memory_uncertain(
    tmp_path, monkeypatch, operation, error_type
):
    path = tmp_path / "drop-fault.kohakutr"
    memory = SessionMemory(str(path))
    table = memory._fts
    original = type(table).__delattr__

    def fail_drop(self, attr):
        if self is table and attr == "_vault":
            raise error_type("drop failed")
        return original(self, attr)

    try:
        with monkeypatch.context() as patched:
            patched.setattr(type(table), "__delattr__", fail_drop)
            if operation == "drop":
                with pytest.raises(error_type):
                    memory._resources.drop(table)
            elif error_type is KeyboardInterrupt:
                with pytest.raises(error_type):
                    memory.close()
            else:
                memory.close()
        memory.close()
        assert try_fast(path, eligible=True).reason == "legacy_release_uncertain"
    finally:
        for handle in (memory._fts, memory._state, memory._vec):
            if handle is not None:
                dispose(handle)


def test_memory_cannot_reopen_native_tables_after_close(tmp_path):
    path = tmp_path / "closed.kohakutr"
    memory = SessionMemory(str(path))
    del memory._fts._vault
    memory.close()
    with pytest.raises(RuntimeError, match="closed"):
        memory._resources.open(memory_module.TextVault, str(path), table="extra")
    assert_idle(path)


def test_memory_registers_before_first_handle(tmp_path, monkeypatch):
    path = tmp_path / "before-open.kohakutr"
    original = memory_module.TextVault
    seen = []

    def open_vault(*args, **kwargs):
        seen.append(try_fast(path, eligible=True).reason)
        return original(*args, **kwargs)

    monkeypatch.setattr(memory_module, "TextVault", open_vault)
    memory = SessionMemory(str(path))
    try:
        assert seen == ["legacy_enrolled"]
    finally:
        memory.close()
    assert_idle(path)


def test_partial_native_constructor_failure_stays_uncertain(tmp_path, monkeypatch):
    path = tmp_path / "partial.kohakutr"
    original = memory_module.TextVault
    opened = []

    def open_fts(*args, **kwargs):
        vault = original(*args, **kwargs)
        opened.append(vault)
        return vault

    def fail_state(*args, **kwargs):
        raise OSError("native open failed")

    monkeypatch.setattr(memory_module, "TextVault", open_fts)
    monkeypatch.setattr(memory_module, "KVault", fail_state)
    with pytest.raises(OSError, match="native open failed"):
        SessionMemory(str(path))
    assert opened and is_disposed(opened[0])
    assert try_fast(path, eligible=True).reason == "legacy_release_uncertain"
    assert GATE.snapshot(path).legacy_active == 0


def test_swallowed_state_close_failure_keeps_optimization_disabled(tmp_path):
    path = tmp_path / "close-error.kohakutr"
    memory = SessionMemory(str(path))
    original = memory._state.close

    def fail_close():
        original()
        raise OSError("state cleanup failed")

    memory._state.close = fail_close
    memory.close()
    memory.close()
    assert try_fast(path, eligible=True).reason == "legacy_release_uncertain"
    assert GATE.snapshot(path).legacy_active == 0


def test_replacing_vectors_does_not_retain_native_handles(tmp_path):
    path = tmp_path / "vectors.kohakutr"
    memory = SessionMemory(str(path))
    embedder = SimpleNamespace(dimensions=4)
    try:
        memory._set_embedder(embedder)
        old = memory._vec
        memory._set_embedder(embedder)
        assert is_disposed(old)
        assert len(memory._resources._handles) == 3
    finally:
        memory.close()
    assert_idle(path)


def test_memory_close_cannot_release_during_lazy_open(tmp_path):
    path = tmp_path / "lazy.kohakutr"
    memory = SessionMemory(str(path))
    entered, release, close_started, close_done = (threading.Event() for _ in range(4))
    opened = []

    def factory(*args, **kwargs):
        table = memory_module.KVault(*args, **kwargs)
        opened.append(table)
        entered.set()
        assert release.wait(5)
        return table

    def close():
        close_started.set()
        memory.close()
        close_done.set()

    with ThreadPoolExecutor(max_workers=2) as executor:
        opening = executor.submit(
            memory._resources.open,
            factory,
            str(path),
            table="lazy",
        )
        closing = None
        try:
            assert entered.wait(5)
            closing = executor.submit(close)
            assert close_started.wait(5)
            assert not close_done.wait(0.1)
            assert try_fast(path, eligible=True).reason == "legacy_enrolled"
        finally:
            release.set()
            opening.result(timeout=5)
            if closing is not None:
                closing.result(timeout=5)
            for table in opened:
                dispose(table)
    assert_idle(path)


def test_memory_close_holds_occupancy_through_native_disposal(tmp_path):
    path = tmp_path / "closing.kohakutr"
    memory = SessionMemory(str(path))
    entered, release = threading.Event(), threading.Event()
    original = memory._state.close

    def close_state():
        entered.set()
        assert release.wait(5)
        original()

    memory._state.close = close_state
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(memory.close)
        try:
            assert entered.wait(5)
            assert try_fast(path, eligible=True).reason == "legacy_enrolled"
        finally:
            release.set()
            future.result(timeout=5)
    assert_idle(path)
