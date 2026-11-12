"""Unit tests for :mod:`kohakuterrarium.studio.sessions.live.restore`."""

import os

import kohakuterrarium.session.run_state as rs
from kohakuterrarium.session.resume.target import SUCCESSOR_KEY
from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.studio.sessions.live import live_sessions
from kohakuterrarium.studio.sessions.live.restore import (
    _held_elsewhere,
    restore_live_sessions,
    restore_row,
)
from kohakuterrarium.studio.sessions.lifecycle import start_creature
from kohakuterrarium.terrarium import LocalTerrariumService, Terrarium
from kohakuterrarium.utils.file.lock import FileLock


async def _saved_creature_session(tmp_path, creature_dir, name="alpha"):
    """Run one turn in a creature session, then shut the engine down; returns the session file."""
    engine = Terrarium(session_dir=str(tmp_path / "sessions"))
    service = LocalTerrariumService(engine)
    session = await start_creature(service, config_path=str(creature_dir), name=name)
    cid = session.creatures[0]["creature_id"]
    async for _ in service.chat(cid, "hello"):
        pass
    path = engine._session_stores[session.session_id].path
    await engine.shutdown()
    return path


async def test_restores_a_row_left_by_an_earlier_boot(
    tmp_path, scripted, creature_dir, monkeypatch
):
    path = await _saved_creature_session(tmp_path, creature_dir)
    assert [r["path"] for r in live_sessions().rows()] == [str(path)]
    assert await restore_live_sessions(lambda _d: None) == []
    monkeypatch.setattr(rs, "BOOT_ID", "next")
    engine = Terrarium(session_dir=str(tmp_path / "sessions"))
    service = LocalTerrariumService(engine)
    try:
        outcomes = await restore_live_sessions(
            lambda d: service if d == str(tmp_path / "sessions") else None
        )
        assert [(o["status"], o["stopped"], o["interrupted"]) for o in outcomes] == [
            ("restored", [], [])
        ]
        sid = outcomes[0]["session_id"]
        assert [g.graph_id for g in engine.list_graphs()] == [sid]
        row = live_sessions().get(path)
        assert (row["claimed_by"], row["failed"]) == ("next", None)
        assert await restore_live_sessions(lambda d: service) == []
    finally:
        await engine.shutdown()


async def test_failures_missing_files_and_unhosted_rows(tmp_path, monkeypatch):
    registry = live_sessions()
    broken = tmp_path / "broken.kohakutr"
    broken.write_bytes(b"not a session file")
    registry.add(broken, session_id="b", session_dir="d", boot_id="earlier")
    gone = tmp_path / "gone.kohakutr"
    registry.add(gone, session_id="g", session_dir="d", boot_id="earlier")
    elsewhere = tmp_path / "elsewhere.kohakutr"
    SessionStore(str(elsewhere)).close(update_status=False)
    registry.add(elsewhere, session_id="e", session_dir="other", boot_id="earlier")
    engine = Terrarium()
    service = LocalTerrariumService(engine)
    try:
        outcomes = {
            o["path"]: o
            for o in await restore_live_sessions(
                lambda d: service if d == "d" else None
            )
        }
    finally:
        await engine.shutdown()
    assert outcomes[str(broken.resolve())]["status"] == "failed"
    assert outcomes[str(broken.resolve())]["error"]
    assert registry.get(broken)["failed"] == outcomes[str(broken.resolve())]["error"]
    assert outcomes[str(gone.resolve())]["status"] == "missing"
    assert registry.get(gone) is None
    assert outcomes[str(elsewhere.resolve())]["status"] == "skipped"
    assert registry.get(elsewhere)["failed"] is None


async def test_a_merged_pair_is_restored_once(
    tmp_path, scripted, creature_dir, monkeypatch
):
    kept = await _saved_creature_session(tmp_path, creature_dir, name="kept")
    retired = tmp_path / "sessions" / "retired.kohakutr"
    store = SessionStore(str(retired))
    store.init_meta(
        session_id="retired",
        config_type="agent",
        config_path="",
        pwd=str(tmp_path),
        agents=["kept"],
    )
    kept_ro = SessionStore.open_readonly(kept)
    conversation_id = kept_ro.meta.get("conversation_id")
    kept_ro.close(update_status=False)
    store.meta[SUCCESSOR_KEY] = {
        "kind": "merge",
        "state": "ready",
        "targets": [{"path": str(kept), "conversation_id": conversation_id}],
        "agents": [],
    }
    store.close(update_status=False)
    registry = live_sessions()
    registry.add(
        retired,
        session_id="retired",
        session_dir=str(tmp_path / "sessions"),
        boot_id="earlier",
    )
    monkeypatch.setattr(rs, "BOOT_ID", "next")
    engine = Terrarium(session_dir=str(tmp_path / "sessions"))
    service = LocalTerrariumService(engine)
    try:
        outcomes = await restore_live_sessions(lambda _d: service)
        assert sorted(o["status"] for o in outcomes) == ["merged", "restored"]
        assert registry.get(retired) is None
        assert len(engine.list_graphs()) == 1
    finally:
        await engine.shutdown()


async def test_restore_row_resolves_its_own_target(tmp_path):
    registry = live_sessions()
    missing = tmp_path / "missing.kohakutr"
    row = registry.add(missing, session_id="m", session_dir="", boot_id="earlier")
    assert (await restore_row(object(), row))["status"] == "missing"
    assert registry.get(missing) is None
    broken = tmp_path / "broken.kohakutr"
    broken.write_bytes(b"junk")
    row = registry.add(broken, session_id="b", session_dir="", boot_id="earlier")
    outcome = await restore_row(object(), row)
    assert outcome["status"] == "failed"
    assert registry.get(broken)["claimed_by"] == rs.BOOT_ID


async def test_another_servers_rows_are_left_untouched(
    tmp_path, scripted, creature_dir, monkeypatch
):
    path = await _saved_creature_session(tmp_path, creature_dir)
    live_sessions().update(path, server="port:8849")
    monkeypatch.setattr(rs, "BOOT_ID", "next")
    monkeypatch.setattr(rs, "SERVER_KEY", "port:8848")
    engine = Terrarium(session_dir=str(tmp_path / "sessions"))
    service = LocalTerrariumService(engine)
    try:
        assert await restore_live_sessions(lambda d: service) == []
        assert engine.list_graphs() == []
        row = live_sessions().get(path)
        assert (row["claimed_by"], row["failed"]) == (None, None)
    finally:
        await engine.shutdown()


async def test_own_servers_rows_are_restored(
    tmp_path, scripted, creature_dir, monkeypatch
):
    path = await _saved_creature_session(tmp_path, creature_dir)
    live_sessions().update(path, server="port:8848")
    monkeypatch.setattr(rs, "BOOT_ID", "next")
    monkeypatch.setattr(rs, "SERVER_KEY", "port:8848")
    engine = Terrarium(session_dir=str(tmp_path / "sessions"))
    service = LocalTerrariumService(engine)
    try:
        outcomes = await restore_live_sessions(lambda d: service)
        assert [o["status"] for o in outcomes] == ["restored"]
    finally:
        await engine.shutdown()


async def test_session_held_by_another_process_is_skipped_not_failed(
    tmp_path, scripted, creature_dir, monkeypatch
):
    path = await _saved_creature_session(tmp_path, creature_dir)
    monkeypatch.setattr(rs, "BOOT_ID", "next")
    monkeypatch.setattr(rs, "SERVER_KEY", "port:8848")
    holder = FileLock(str(path) + ".lock")
    holder.acquire()
    # The lock is held in-process here; pretend the recorded holder is a
    # different process, which is the situation the guard exists for.
    monkeypatch.setattr(FileLock, "read_holder_pid", lambda self: os.getpid() + 1)
    engine = Terrarium(session_dir=str(tmp_path / "sessions"))
    service = LocalTerrariumService(engine)
    try:
        assert await restore_live_sessions(lambda d: service) == []
        assert engine.list_graphs() == []
        row = live_sessions().get(path)
        assert (row["claimed_by"], row["failed"]) == (None, None)
    finally:
        holder.release()
        await engine.shutdown()


async def test_lock_held_by_this_process_does_not_count_as_elsewhere(tmp_path):
    """A leaked handle from an earlier restore must not strand the row forever."""
    path = tmp_path / "s.kohakutr"
    leaked = FileLock(str(path) + ".lock")
    leaked.acquire()
    try:
        assert _held_elsewhere(str(path)) is False
    finally:
        leaked.release()


async def test_free_lock_is_not_held(tmp_path):
    assert _held_elsewhere(str(tmp_path / "s.kohakutr")) is False
