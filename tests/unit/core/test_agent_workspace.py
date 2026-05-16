"""Unit tests for :mod:`kohakuterrarium.core.agent_workspace`."""

import types
from pathlib import Path

import pytest

from kohakuterrarium.core.agent_workspace import WorkspaceController
from kohakuterrarium.utils.file_guard import FileReadState, PathBoundaryGuard


def _fake_agent(tmp_path, mode="warn"):
    """Build a duck-typed Agent stand-in with the minimum surface area."""
    executor = types.SimpleNamespace(
        _working_dir=tmp_path,
        _path_guard=PathBoundaryGuard(cwd=tmp_path, mode=mode),
        _file_read_state=FileReadState(),
    )
    store = types.SimpleNamespace(meta={"pwd": str(tmp_path)}, touched=0)

    def _touch():
        store.touched += 1

    store.touch = _touch
    config = types.SimpleNamespace(name="alice", pwd_guard=mode)
    agent = types.SimpleNamespace(
        executor=executor,
        config=config,
        _path_guard=executor._path_guard,
        _file_read_state=executor._file_read_state,
        _processing_task=None,
        session_store=store,
    )
    return agent


# ── get ──────────────────────────────────────────────────────────


class TestGet:
    def test_returns_executor_cwd(self, tmp_path):
        a = _fake_agent(tmp_path)
        ws = WorkspaceController(a)
        assert ws.get() == str(tmp_path.resolve())

    def test_fallback_to_process_cwd(self):
        a = types.SimpleNamespace()
        ws = WorkspaceController(a)
        assert ws.get() == str(Path.cwd())

    def test_executor_with_none_working_dir(self):
        a = types.SimpleNamespace(executor=types.SimpleNamespace(_working_dir=None))
        ws = WorkspaceController(a)
        assert ws.get() == str(Path.cwd())


# ── set ──────────────────────────────────────────────────────────


class TestSet:
    def test_switches_executor_state(self, tmp_path):
        new_dir = tmp_path / "subdir"
        new_dir.mkdir()
        a = _fake_agent(tmp_path)
        ws = WorkspaceController(a)
        result = ws.set(new_dir)
        assert result == str(new_dir.resolve())
        assert a.executor._working_dir == new_dir.resolve()
        # Path guard rebuilt with same mode.
        assert isinstance(a.executor._path_guard, PathBoundaryGuard)
        assert a.executor._path_guard.cwd == str(new_dir.resolve())
        # Same guard exposed on the agent.
        assert a._path_guard is a.executor._path_guard
        # Fresh FileReadState, not the prior one.
        assert isinstance(a.executor._file_read_state, FileReadState)
        # Session meta updated + touched.
        assert a.session_store.meta["pwd"] == str(new_dir.resolve())
        assert a.session_store.touched == 1

    def test_empty_path_rejected(self, tmp_path):
        a = _fake_agent(tmp_path)
        ws = WorkspaceController(a)
        with pytest.raises(ValueError, match="required"):
            ws.set("")

    def test_missing_dir_rejected(self, tmp_path):
        a = _fake_agent(tmp_path)
        ws = WorkspaceController(a)
        with pytest.raises(ValueError, match="does not exist"):
            ws.set(tmp_path / "nope_xyz")

    def test_file_path_rejected(self, tmp_path):
        f = tmp_path / "f.txt"
        f.write_text("x")
        a = _fake_agent(tmp_path)
        ws = WorkspaceController(a)
        with pytest.raises(ValueError, match="not a directory"):
            ws.set(f)

    def test_mid_turn_rejected(self, tmp_path):
        a = _fake_agent(tmp_path)
        a._processing_task = object()  # any truthy task-like value
        ws = WorkspaceController(a)
        with pytest.raises(RuntimeError, match="Interrupt"):
            ws.set(tmp_path)

    def test_no_executor_rejected(self, tmp_path):
        target = tmp_path / "x"
        target.mkdir()
        a = types.SimpleNamespace(
            executor=None,
            config=types.SimpleNamespace(name="x", pwd_guard="warn"),
            _processing_task=None,
        )
        ws = WorkspaceController(a)
        with pytest.raises(RuntimeError, match="no executor"):
            ws.set(target)

    def test_no_store_silently_skipped(self, tmp_path):
        a = _fake_agent(tmp_path)
        a.session_store = None
        target = tmp_path / "y"
        target.mkdir()
        ws = WorkspaceController(a)
        # Must not raise even without a store.
        ws.set(target)
        assert a.executor._working_dir == target.resolve()

    def test_user_dir_expanded(self, tmp_path):
        new_dir = tmp_path / "u"
        new_dir.mkdir()
        a = _fake_agent(tmp_path)
        ws = WorkspaceController(a)
        # Resolved path string still ends in the directory we created.
        out = ws.set(str(new_dir))
        assert Path(out) == new_dir.resolve()
