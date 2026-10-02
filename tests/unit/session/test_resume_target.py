"""Resume successors never change explicit historical reads."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from kohakuterrarium.errors import SessionNotResumableError
from kohakuterrarium.session.readonly import read_session_meta
from kohakuterrarium.session.resume_open import open_store_with_migration
from kohakuterrarium.session.resume_target import (
    active_resume_graph,
    resolve_resume_path,
)
from kohakuterrarium.session.store import SessionStore


def saved(path, agents=("alice",)):
    store = SessionStore(path)
    store.init_meta(path.stem, "agent", "/cfg", str(path.parent), list(agents))
    return store


def successor(source, target, **overrides):
    source.meta["resume_successor"] = {
        "kind": "merge",
        "state": "ready",
        "agents": ["alice"],
        "targets": [
            {
                "path": str(target.path),
                "conversation_id": target.meta["conversation_id"],
            }
        ],
        **overrides,
    }
    source.checkpoint()


def test_chain_resolves_current_file_but_history_stays_original(tmp_path):
    stores = [saved(tmp_path / f"{name}.kohakutr") for name in ("a", "b", "c")]
    try:
        a, b, c = stores
        successor(a, b)
        successor(b, c)
        assert resolve_resume_path(a.path, session_dir=tmp_path) == Path(c.path)
        assert read_session_meta(a.path)["session_id"] == "a"
        historical = open_store_with_migration(a.path)
        historical.close(update_status=False)
        with pytest.raises(SessionNotResumableError, match="superseded"):
            open_store_with_migration(a.path, writer_lock=True)
    finally:
        for store in stores:
            store.close(update_status=False)


@pytest.mark.parametrize(
    "failure",
    ["pending", "split", "cycle", "identity", "missing", "bounds", "membership"],
)
def test_invalid_successor_never_reopens_stale_source(tmp_path, failure):
    root = tmp_path / "sessions"
    root.mkdir()
    a = saved(root / "a.kohakutr")
    b = saved((tmp_path if failure == "bounds" else root) / "b.kohakutr")
    try:
        successor(a, b)
        if failure in ("pending", "split"):
            successor(
                a,
                b,
                **({"state": "pending"} if failure == "pending" else {"kind": "split"}),
            )
        elif failure == "cycle":
            successor(b, a)
        elif failure == "identity":
            b.meta["conversation_id"] = "replaced"
        elif failure == "membership":
            b.meta["agents"] = ["bob"]
        b.checkpoint()
        if failure == "missing":
            b.close(update_status=False)
            Path(b.path).unlink()
        with pytest.raises((SessionNotResumableError, FileNotFoundError)):
            resolve_resume_path(a.path, session_dir=root)
        assert read_session_meta(a.path)["session_id"] == "a"
    finally:
        a.close(update_status=False)
        b.close(update_status=False)


def test_version_companions_prefer_latest_supported_explicit_file(tmp_path):
    base = tmp_path / "saved.kohakutr"
    stores = [
        saved(base),
        saved(tmp_path / "saved.kohakutr.v2"),
        saved(tmp_path / "saved.kohakutr.v99"),
    ]
    try:
        stores[0].meta["format_version"] = 1
        stores[2].meta["format_version"] = 99
        for store in stores:
            store.checkpoint()
        assert resolve_resume_path(base) == tmp_path / "saved.kohakutr.v2"
        assert resolve_resume_path(stores[2].path) == tmp_path / "saved.kohakutr.v2"
    finally:
        for store in stores:
            store.close(update_status=False)


def test_explicit_historical_fork_does_not_follow_parent_successor(tmp_path):
    source = saved(tmp_path / "old.kohakutr")
    target = saved(tmp_path / "current.kohakutr")
    child_path = tmp_path / "fork.kohakutr"
    try:
        source.append_event(
            "alice", "user_message", {"content": "historical"}, turn_index=1
        )
        successor(source, target)
        child = source.fork(child_path, at_event_id=1)
        child.close(update_status=False)
        assert resolve_resume_path(child_path) == child_path
    finally:
        source.close(update_status=False)
        target.close(update_status=False)


def test_already_running_target_must_have_same_conversation_identity(tmp_path):
    target = saved(tmp_path / "current.kohakutr")
    try:
        target.checkpoint()
        runtime_store = SimpleNamespace(
            path=target.path, meta={"conversation_id": "another-conversation"}
        )
        engine = SimpleNamespace(
            _session_stores={"graph": runtime_store},
            _topology=SimpleNamespace(graphs={"graph": object()}),
        )
        with pytest.raises(SessionNotResumableError, match="identity changed"):
            active_resume_graph(engine, Path(target.path))
        runtime_store.meta["conversation_id"] = target.meta["conversation_id"]
        assert active_resume_graph(engine, Path(target.path)) == "graph"
    finally:
        target.close(update_status=False)
