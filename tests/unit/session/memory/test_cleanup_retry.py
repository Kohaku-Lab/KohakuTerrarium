"""Interrupted Memory cleanup preserves handles and initialization errors."""

import subprocess
import sys
from types import SimpleNamespace

import pytest

import kohakuterrarium.session.memory.resources as resources_module
from kohakuterrarium.session.memory.index import SessionMemory
from kohakuterrarium.session.memory.resources import MemoryResources
from kohakuterrarium.session.occupancy.gate import GATE, try_fast
from kohakuterrarium.session.vault.handles import dispose, is_disposed


def test_cyclic_initialization_causes_do_not_hang():
    script = """
from types import SimpleNamespace
from kohakuterrarium.session.memory import index as memory

for two_nodes in (False, True):
    primary = OSError("primary")
    root = ValueError("root") if two_nodes else primary
    primary.__cause__ = root
    root.__cause__ = primary
    cleanup = KeyboardInterrupt("cleanup")

    def close():
        raise cleanup

    def fail(self, path):
        raise primary

    memory.MemoryResources = lambda path: SimpleNamespace(close=close)
    memory.SessionMemory._open_indexes = fail
    try:
        memory.SessionMemory("unused")
    except BaseException as caught:
        assert caught is primary
        if two_nodes:
            assert primary.__cause__ is root
        assert root.__cause__ is cleanup
    else:
        raise AssertionError("initialization must fail")
"""
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, timeout=15
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("failed_handle", ["fts", "state", "vector"])
@pytest.mark.parametrize("failure", [KeyboardInterrupt, RuntimeError])
def test_failed_handle_disposal_can_be_retried(
    tmp_path, monkeypatch, failed_handle, failure
):
    path = tmp_path / "retry.kohakutr"
    memory = SessionMemory(str(path))
    memory._set_embedder(SimpleNamespace(dimensions=4))
    handles = [memory._fts, memory._state, memory._vec]
    table = handles[{"fts": 0, "state": 1, "vector": 2}[failed_handle]]

    def fail_dispose(handle):
        if handle is table:
            raise failure("dispose failed")
        return dispose(handle)

    try:
        with monkeypatch.context() as patch:
            patch.setattr(resources_module, "dispose", fail_dispose)
            if failure is KeyboardInterrupt:
                with pytest.raises(KeyboardInterrupt):
                    memory.close()
            else:
                memory.close()
        assert not is_disposed(table)
        assert GATE.snapshot(path).legacy_uncertain
        with pytest.raises(RuntimeError, match="closed"):
            memory._resources.open(lambda: None)
        memory.close()
        memory.close()
        assert all(is_disposed(handle) for handle in handles)
        assert memory._resources._handles == []
        assert try_fast(path, eligible=True).reason == "legacy_release_uncertain"
    finally:
        for handle in handles:
            dispose(handle)


def test_state_close_interruption_retains_handle_for_retry(tmp_path, monkeypatch):
    path = tmp_path / "state.kohakutr"
    memory = SessionMemory(str(path))
    state = memory._state

    def interrupted():
        raise KeyboardInterrupt("state close interrupted")

    try:
        with monkeypatch.context() as patch:
            patch.setattr(state, "close", interrupted)
            with pytest.raises(KeyboardInterrupt):
                memory.close()
        memory.close()
        assert is_disposed(state)
        assert is_disposed(memory._fts)
        assert GATE.snapshot(path).legacy_uncertain
    finally:
        dispose(state)
        dispose(memory._fts)


@pytest.mark.parametrize("chained_primary", [False, True])
def test_initialization_error_survives_cleanup_interruption(
    tmp_path, monkeypatch, chained_primary
):
    path = tmp_path / "init.kohakutr"
    primary = OSError("initialization failed")
    root = ValueError("original initialization cause")
    if chained_primary:
        primary.__cause__ = root
    secondary = KeyboardInterrupt("rollback interrupted")
    original_close = MemoryResources.close

    def fail_init(self, db_path):
        raise primary

    def fail_close(self):
        original_close(self)
        raise secondary

    monkeypatch.setattr(SessionMemory, "_open_indexes", fail_init)
    monkeypatch.setattr(MemoryResources, "close", fail_close)
    with pytest.raises(BaseException) as caught:
        SessionMemory(str(path))
    assert caught.value is primary
    if chained_primary:
        assert caught.value.__cause__ is root
        assert root.__cause__ is secondary
    else:
        assert caught.value.__cause__ is secondary
    assert not GATE.snapshot(path).registered
