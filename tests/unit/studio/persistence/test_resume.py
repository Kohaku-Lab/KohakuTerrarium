"""Studio resume keeps index registration off the event loop."""

import asyncio
import threading
from contextlib import closing

import kohakuterrarium.session.run_state as rs
from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.studio.persistence.resume import resume_session
from kohakuterrarium.studio.sessions.live import live_sessions
from kohakuterrarium.studio.persistence.session_index import get_session_index_default
from kohakuterrarium.studio.persistence.session_index import reconcile as reconcile_mod
from kohakuterrarium.studio.persistence.session_index.store import SessionIndex
from kohakuterrarium.studio.sessions import index_hooks
from kohakuterrarium.terrarium import Terrarium
from kohakuterrarium.terrarium.service import LocalTerrariumService
from kohakuterrarium.testing.llm import ScriptedLLM


async def test_resume_registers_real_history_without_index_io_on_loop(
    tmp_path, monkeypatch
):
    config = tmp_path / "worker.yaml"
    config.write_text(
        "name: worker\ninput: {type: none}\noutput: {type: none}\n", encoding="utf-8"
    )
    path = tmp_path / "saved.kohakutr"
    store = SessionStore(path)
    store.init_meta("saved", "agent", str(config), str(tmp_path), ["worker"])
    store.append_event("worker", "user_input", {"content": "resume preview"})
    store.close()
    other_path = tmp_path / "unrelated.kohakutr"
    with closing(SessionStore(other_path)) as other:
        other.init_meta("unrelated", "agent", str(config), str(tmp_path), ["worker"])
        other.append_event(
            "worker", "user_input", {"content": "other searchable input"}
        )
    reads = []
    original_read = reconcile_mod.read_entry_from_disk

    def observe_read(path):
        reads.append(path.name)
        return original_read(path)

    monkeypatch.setattr(reconcile_mod, "read_entry_from_disk", observe_read)
    writes = []
    original_upsert = SessionIndex.upsert

    def observe(index, entry):
        writes.append(threading.get_ident())
        return original_upsert(index, entry)

    monkeypatch.setattr(SessionIndex, "upsert", observe)
    session_id = None
    try:
        async with Terrarium(session_dir=str(tmp_path)) as engine:
            session = await resume_session(
                LocalTerrariumService(engine), path, llm=ScriptedLLM(["ready"])
            )
            session_id = session.session_id
            assert other_path.name not in reads
            index = await asyncio.to_thread(get_session_index_default, tmp_path)
            assert index.get(path.name)["preview"] == "resume preview"
            assert (
                index.list(search="searchable").rows[0]["filename"] == other_path.name
            )
            assert index.count() == 2
            assert writes and threading.get_ident() not in writes
            assert session_id in engine._session_stores
    finally:
        if session_id is not None:
            hook = index_hooks.registry().pop(session_id, None)
            if hook is not None:
                await asyncio.to_thread(hook.detach)


async def test_resume_keeps_stopped_creatures_stopped_and_does_not_nudge(
    tmp_path, monkeypatch
):
    config = tmp_path / "worker.yaml"
    config.write_text(
        "name: worker\ninput: {type: none}\noutput: {type: none}\n", encoding="utf-8"
    )
    for name, state in (
        ("halted", rs.STOPPED),
        ("cutoff", rs.ACTIVE),
        ("forced", rs.STOPPED),
    ):
        path = tmp_path / f"{name}.kohakutr"
        store = SessionStore(path)
        store.init_meta(name, "agent", str(config), str(tmp_path), ["worker"])
        monkeypatch.setattr(rs, "BOOT_ID", "earlier")
        rs.write_run(store, "worker", state)
        store.close()
    monkeypatch.setattr(rs, "BOOT_ID", "now")
    async with Terrarium(session_dir=str(tmp_path)) as engine:
        service = LocalTerrariumService(engine)
        halted = await resume_session(
            service, tmp_path / "halted.kohakutr", llm=ScriptedLLM(["x"])
        )
        cutoff = await resume_session(
            service, tmp_path / "cutoff.kohakutr", llm=ScriptedLLM(["x"])
        )
        forced = await resume_session(
            service,
            tmp_path / "forced.kohakutr",
            llm=ScriptedLLM(["x"]),
            restore_runs=False,
        )
        creature = lambda sid: engine.get_creature(
            next(iter(engine.get_graph(sid).creature_ids))
        )  # noqa: E731
        assert creature(halted.session_id).is_running is False
        assert creature(cutoff.session_id).is_running is True
        assert creature(forced.session_id).is_running is True
        await asyncio.sleep(0.05)
        users = [
            m
            for m in creature(
                cutoff.session_id
            ).agent.controller.conversation.to_messages()
            if m["role"] == "user"
        ]
        assert users == []
        assert (
            live_sessions().get(tmp_path / "halted.kohakutr")["session_id"]
            == halted.session_id
        )
    for sid in (halted.session_id, cutoff.session_id, forced.session_id):
        hook = index_hooks.registry().pop(sid, None)
        if hook is not None:
            await asyncio.to_thread(hook.detach)
