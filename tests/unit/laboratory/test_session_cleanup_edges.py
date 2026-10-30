"""Session adapter guards and persisted lifecycle outcomes around cleanup."""

import asyncio
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

import pytest

from kohakuterrarium.laboratory.adapters.terrarium_session import (
    _MAX_RESUME_TOKENS,
    _transfer_path,
)
from kohakuterrarium.session.occupancy.gate import GATE, try_fast
from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.testing.terrarium import TestTerrariumBuilder
from tests.unit.laboratory.file_mutation_enrollment_helpers import wait_pending
from tests.unit.laboratory.test_session_cleanup_enrollment import _adapter, _msg


@pytest.mark.parametrize("unlink_fails", [False, True])
async def test_cancel_after_rollback_close_finishes_cleanup_without_new(
    tmp_path, monkeypatch, unlink_fails
):
    adapter, engine = _adapter(monkeypatch, tmp_path)
    path = tmp_path / "rollback.kohakutr"
    store = SessionStore(path)
    engine._session_stores["graph"] = store
    engine._owned_sessions.add("graph")
    adapter._remember_resume_token("token", "graph", path)
    close = store.close
    loop = asyncio.get_running_loop()
    outer = None
    failure = PermissionError("rollback unlink failed")
    real_unlink = Path.unlink

    def closed(*args, **kwargs):
        close(*args, **kwargs)
        loop.call_soon(outer.cancel)

    def unlink(target, *args, **kwargs):
        if target == path and unlink_fails:
            raise failure
        return real_unlink(target, *args, **kwargs)

    monkeypatch.setattr(store, "close", closed)
    monkeypatch.setattr(Path, "unlink", unlink)
    outer = asyncio.create_task(adapter._op_rollback_resume({"graph_id": "graph"}))
    with pytest.raises(asyncio.CancelledError) as caught:
        await outer
    assert not engine._session_stores and not engine._owned_sessions
    assert not adapter._resume_tokens
    assert not GATE.snapshot(path).registered
    if unlink_fails:
        assert caught.value.__cause__ is failure
        assert path.exists()
    else:
        assert not path.exists()
        assert await adapter._op_rollback_resume({"graph_id": "graph"}) == {
            "ok": True,
            "removed": [],
        }
        assert not path.exists()


@pytest.mark.parametrize("reattached", [False, True])
async def test_rollback_cleanup_survives_repeated_cancel_and_rechecks_owner(
    tmp_path, monkeypatch, reattached
):
    adapter, engine = _adapter(monkeypatch, tmp_path)
    path = tmp_path / "waiting.kohakutr"
    store = SessionStore(path)
    engine._session_stores["graph"] = store
    adapter._remember_resume_token("token", "graph", path)
    close = store.close
    leases = []

    def closed(*args, **kwargs):
        close(*args, **kwargs)
        lease = try_fast(path, eligible=True).lease
        assert lease is not None
        leases.append(lease)

    monkeypatch.setattr(store, "close", closed)
    outer = asyncio.create_task(adapter._op_rollback_resume({"graph_id": "graph"}))
    try:
        await asyncio.to_thread(wait_pending, path, 1)
        outer.cancel()
        await asyncio.sleep(0)
        outer.cancel()
        await asyncio.sleep(0)
        assert not outer.done() and path.exists()
        if reattached:
            engine._session_stores["replacement"] = SimpleNamespace(path=str(path))
        leases[0].finish(source_released=True)
        with pytest.raises(asyncio.CancelledError) as caught:
            await outer
        if reattached:
            assert isinstance(caught.value.__cause__, ValueError)
            assert "active session store" in str(caught.value.__cause__)
            assert path.exists()
        else:
            assert not path.exists()
        assert not GATE.snapshot(path).registered
    finally:
        for lease in leases:
            lease.finish(source_released=True)
        if not outer.done():
            try:
                await outer
            except asyncio.CancelledError:
                pass
        engine._session_stores.clear()


@pytest.mark.parametrize("value", [None, "", "resume/note.txt"])
def test_transfer_rejects_invalid_filename(tmp_path, monkeypatch, value):
    adapter, engine = _adapter(monkeypatch, tmp_path)
    with pytest.raises(ValueError):
        _transfer_path(engine, str(tmp_path / value) if value else value)


async def test_guard_errors_do_not_mutate_session(tmp_path, monkeypatch):
    adapter, engine = _adapter(monkeypatch, tmp_path)
    engine._topology = SimpleNamespace(graphs={})
    path = tmp_path / "saved.kohakutr"
    with closing(SessionStore(path)) as store:
        store.init_meta("sid", "agent", "config", str(tmp_path), ["alice"])
    original = path.read_bytes()
    for body in ({}, {"graph_id": "missing"}):
        with pytest.raises(ValueError):
            await adapter._op_unload(body)
    for body in ({}, {"session_path": str(path)}):
        with pytest.raises(ValueError):
            await adapter._op_rollback_resume(body)
    with pytest.raises(ValueError):
        adapter._op_set_lifecycle({"path": str(tmp_path / "missing")})
    for body in (
        {"scope": "config://"},
        {"path": str(path), "resume_token": ""},
        {
            "path": str(path),
            "pwd_override": str(tmp_path),
            "workspace_overrides": {"x": str(tmp_path)},
        },
    ):
        with pytest.raises(ValueError):
            await adapter._op_resume(body)
    with pytest.raises(ValueError):
        await adapter._op_remove({})
    with pytest.raises(ValueError):
        adapter._op_workspace_preflight({})
    with pytest.raises(FileNotFoundError):
        adapter._op_workspace_preflight({"path": str(tmp_path / "missing")})
    assert path.read_bytes() == original


async def test_persisted_lifecycle_preflight_and_store_filter(tmp_path, monkeypatch):
    adapter, engine = _adapter(monkeypatch, tmp_path)
    path = tmp_path / "saved.kohakutr"
    with closing(SessionStore(path)) as store:
        store.init_meta("sid", "agent", "config", str(tmp_path), ["alice"])
    result = await adapter._dispatch(
        _msg("set_lifecycle", {"session_path": str(path), "conversation_open": False})
    )
    assert result["ok"] is True
    preflight = await adapter._dispatch(
        _msg("workspace_preflight", {"path": str(path)})
    )
    assert preflight == {"legacy": True, "ready": True, "members": [], "gaps": []}
    with closing(SessionStore.open_readonly(path)) as store:
        assert store.meta["status"] == "completed"
        engine._session_stores["one"] = store
        engine._session_stores["two"] = SimpleNamespace(session_id="another")
        filtered = adapter._op_stores({"session_id": "one"})
        assert [row["session_id"] for row in filtered["stores"]] == ["one"]
        engine._topology = SimpleNamespace(graphs={})
        with pytest.raises(ValueError, match="different graph"):
            await adapter._op_unload({"graph_id": "missing", "session_path": str(path)})
    engine._session_stores.clear()


def test_token_eviction_and_path_binding(tmp_path, monkeypatch):
    adapter, _ = _adapter(monkeypatch, tmp_path)
    path = tmp_path / "token.kohakutr"
    for index in range(_MAX_RESUME_TOKENS + 1):
        adapter._remember_resume_token(str(index), "graph", path)
    assert adapter._resolve_resume_token("0", path) == ""
    assert adapter._resolve_resume_token(str(_MAX_RESUME_TOKENS), path) == "graph"
    with pytest.raises(ValueError, match="does not match"):
        adapter._resolve_resume_token(
            str(_MAX_RESUME_TOKENS), tmp_path / "other.kohakutr"
        )


async def test_real_unload_keeps_saved_database(tmp_path, monkeypatch):
    engine = await TestTerrariumBuilder().with_creature("alice").build()
    gid = engine.list_graphs()[0].graph_id
    path = tmp_path / "unloaded.kohakutr"
    store = SessionStore(path)
    store.init_meta("sid", "agent", "config", str(tmp_path), ["alice"])
    engine._session_stores[gid] = store
    engine._owned_sessions.add(gid)
    adapter, _ = _adapter(monkeypatch, tmp_path)
    adapter._engine = engine
    try:
        result = await adapter._op_unload({"graph_id": gid})
        assert result["removed"] == ["alice"]
        assert gid not in engine._session_stores
        with closing(SessionStore.open_readonly(path)) as reopened:
            assert reopened.meta["session_id"] == "sid"
    finally:
        await engine.shutdown()
        store.close(update_status=False)
