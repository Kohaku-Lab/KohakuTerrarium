"""Coverage for ``agent.workspace`` runtime cwd switching.

Exercises the helper directly with a duck-typed agent so we don't pay
the full ``Agent.from_path`` cost. The helper only touches:

* ``agent._processing_task`` (None == idle)
* ``agent.executor._working_dir`` / ``executor._path_guard`` /
  ``executor._file_read_state``
* ``agent._path_guard``, ``agent._file_read_state``
* ``agent.config.pwd_guard`` (default mode for a freshly built guard)
* ``agent.session_store.meta`` (persistence)
"""

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from kohakuterrarium.core.agent_workspace import WorkspaceController
from kohakuterrarium.utils.file_guard import FileReadState, PathBoundaryGuard


class _FakeMeta:
    """Mimics ``SessionStore.meta`` (a KV-like attr) for the persist test."""

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}

    def __setitem__(self, key: str, value: Any) -> None:
        self._data[key] = value

    def __getitem__(self, key: str) -> Any:
        return self._data[key]

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)


class _FakeStore:
    def __init__(self) -> None:
        self.meta = _FakeMeta()
        self.touched = 0

    def touch(self) -> None:
        self.touched += 1


def _make_agent(initial_dir: Path, *, mode: str = "warn", processing: bool = False):
    guard = PathBoundaryGuard(cwd=initial_dir, mode=mode)
    state = FileReadState()
    executor = SimpleNamespace(
        _working_dir=Path(initial_dir).resolve(),
        _path_guard=guard,
        _file_read_state=state,
    )
    return SimpleNamespace(
        config=SimpleNamespace(name="agent", pwd_guard=mode),
        executor=executor,
        _path_guard=guard,
        _file_read_state=state,
        _processing_task=("running" if processing else None),
        session_store=_FakeStore(),
    )


def test_get_returns_resolved_current_pwd(tmp_path):
    agent = _make_agent(tmp_path)
    ws = WorkspaceController(agent)
    assert ws.get() == str(tmp_path.resolve())


def test_set_updates_executor_and_guards(tmp_path):
    a = tmp_path / "a"
    b = tmp_path / "b"
    a.mkdir()
    b.mkdir()
    agent = _make_agent(a, mode="block")
    ws = WorkspaceController(agent)

    applied = ws.set(b)
    assert applied == str(b.resolve())
    # Executor cwd updated
    assert agent.executor._working_dir == b.resolve()
    # Path guard rebuilt with same mode + new cwd
    assert isinstance(agent._path_guard, PathBoundaryGuard)
    assert agent.executor._path_guard is agent._path_guard
    assert agent._path_guard.mode == "block"
    assert agent._path_guard.cwd == str(b.resolve())
    # File-read state replaced (not just cleared) — old set() entries must
    # not haunt the new tree.
    assert isinstance(agent._file_read_state, FileReadState)
    assert agent.executor._file_read_state is agent._file_read_state


def test_set_persists_pwd_to_session_meta(tmp_path):
    target = tmp_path / "next"
    target.mkdir()
    agent = _make_agent(tmp_path)
    ws = WorkspaceController(agent)

    ws.set(target)
    assert agent.session_store.meta["pwd"] == str(target.resolve())
    assert agent.session_store.touched == 1


def test_set_drops_stale_file_read_records(tmp_path):
    """Read-before-write tracking is path-keyed; old entries are toxic
    after a switch (could permit a write to a new-tree path that was
    only read in the old tree)."""
    target = tmp_path / "next"
    target.mkdir()
    agent = _make_agent(tmp_path)
    agent._file_read_state.record_read(
        path=str(tmp_path / "stale.txt"),
        mtime_ns=0,
        partial=False,
        timestamp=0.0,
    )
    ws = WorkspaceController(agent)
    ws.set(target)
    # New state object — the old one's records are forgotten.
    assert agent._file_read_state.get(str(tmp_path / "stale.txt")) is None


def test_set_rejects_missing_path(tmp_path):
    agent = _make_agent(tmp_path)
    ws = WorkspaceController(agent)
    with pytest.raises(ValueError, match="does not exist"):
        ws.set(tmp_path / "no-such-dir")


def test_set_rejects_path_pointing_to_a_file(tmp_path):
    agent = _make_agent(tmp_path)
    not_a_dir = tmp_path / "file.txt"
    not_a_dir.write_text("hi")
    ws = WorkspaceController(agent)
    with pytest.raises(ValueError, match="not a directory"):
        ws.set(not_a_dir)


def test_set_rejects_empty_path(tmp_path):
    agent = _make_agent(tmp_path)
    ws = WorkspaceController(agent)
    with pytest.raises(ValueError, match="required"):
        ws.set("")


def test_set_rejects_when_processing(tmp_path):
    target = tmp_path / "next"
    target.mkdir()
    agent = _make_agent(tmp_path, processing=True)
    ws = WorkspaceController(agent)
    with pytest.raises(RuntimeError, match="processing"):
        ws.set(target)
    # And nothing was changed
    assert agent.executor._working_dir == tmp_path.resolve()


def test_set_with_no_session_store_still_succeeds(tmp_path):
    target = tmp_path / "next"
    target.mkdir()
    agent = _make_agent(tmp_path)
    agent.session_store = None
    ws = WorkspaceController(agent)
    applied = ws.set(target)
    assert applied == str(target.resolve())
    assert agent.executor._working_dir == target.resolve()
