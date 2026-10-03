"""Studio resume keeps index registration off the event loop."""

import asyncio
import threading
from contextlib import closing

from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.studio.persistence.resume import resume_session
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
