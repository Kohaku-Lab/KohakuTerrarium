"""Async resume must recover interrupted output without scanning on the loop."""

import asyncio
import gc
import threading
import weakref

import pytest

from kohakuterrarium.session import resume_async
from kohakuterrarium.session.resume_async import resume_agent_async
from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.testing.llm import ScriptedLLM


def _interrupted_session(tmp_path):
    config = tmp_path / "creature"
    config.mkdir()
    (config / "config.yaml").write_text(
        "name: resumee\n"
        "controller: {tool_format: bracket, include_tools_in_prompt: false, "
        "include_hints_in_prompt: false}\n"
        "system_prompt: test\ninput: {type: none}\noutput: {type: stdout}\n"
    )
    path = tmp_path / "interrupted.kohakutr"
    store = SessionStore(str(path))
    try:
        store.init_meta("test", "agent", str(config), str(tmp_path), ["resumee"])
        store.append_event(
            "resumee",
            "user_message",
            {"content": "hello"},
            turn_index=1,
            branch_id=1,
            parent_branch_path=[],
        )
        store.save_conversation("resumee", [{"role": "user", "content": "hello"}])
        store.state["resumee:open_text"] = "interrupted response"
    finally:
        store.close(update_status=False)
    return path


async def test_recovery_scans_off_loop_and_persists_text_once(tmp_path, monkeypatch):
    path = _interrupted_session(tmp_path)
    loop_thread = threading.get_ident()
    scans = []
    original = SessionStore.get_events

    def observed_get_events(self, *args, **kwargs):
        scans.append(threading.get_ident())
        return original(self, *args, **kwargs)

    monkeypatch.setattr(SessionStore, "get_events", observed_get_events)
    for _ in range(2):
        agent, store = await resume_agent_async(path, llm=ScriptedLLM(["unused"]))
        try:
            await agent._session_output.drain()
            events = await store.run(store.get_events, "resumee")
            chunks = [event for event in events if event["type"] == "text_chunk"]
            assert len(chunks) == 1
            assert chunks[0]["content"] == "interrupted response"
            assert chunks[0]["turn_index"] == 1
            assert chunks[0]["branch_id"] == 1
            assert await store.run(store.state.get, "resumee:open_text") == ""
        finally:
            await asyncio.to_thread(store.close, update_status=False)
    assert scans
    assert loop_thread not in scans


async def test_cancelled_recovery_releases_writer_and_retains_text(
    tmp_path, monkeypatch
):
    path = _interrupted_session(tmp_path)
    loop = asyncio.get_running_loop()
    loop_thread = threading.get_ident()
    recovery_started = asyncio.Event()
    release = threading.Event()
    original = SessionStore.get_events
    scans = 0

    def gated_get_events(self, *args, **kwargs):
        nonlocal scans
        scans += 1
        if scans == 2 and threading.get_ident() != loop_thread:
            loop.call_soon_threadsafe(recovery_started.set)
            assert release.wait(5), "recovery was not released"
        return original(self, *args, **kwargs)

    monkeypatch.setattr(SessionStore, "get_events", gated_get_events)
    task = asyncio.create_task(resume_agent_async(path, llm=ScriptedLLM(["unused"])))
    cancel_requested = False
    try:
        await asyncio.wait_for(recovery_started.wait(), timeout=3)
        task.cancel()
        cancel_requested = True
        await asyncio.sleep(0)
    finally:
        release.set()
        if task.done() and not task.cancelled() and task.exception() is None:
            _, store = task.result()
            await asyncio.to_thread(store.close, update_status=False)
        else:
            if not cancel_requested:
                task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
    monkeypatch.setattr(SessionStore, "get_events", original)
    agent, store = await resume_agent_async(path, llm=ScriptedLLM(["unused"]))
    try:
        await agent._session_output.drain()
        events = await store.run(store.get_events, "resumee")
        assert [e["content"] for e in events if e["type"] == "text_chunk"] == [
            "interrupted response"
        ]
    finally:
        await asyncio.to_thread(store.close, update_status=False)


@pytest.mark.parametrize("cancel_twice", [False, True])
async def test_cancel_between_recovery_append_and_slot_clear_does_not_duplicate(
    tmp_path, monkeypatch, cancel_twice
):
    path = _interrupted_session(tmp_path)
    loop = asyncio.get_running_loop()
    append_queued = asyncio.Event()
    close_started = asyncio.Event()
    release = threading.Event()
    submit = SessionStore.submit
    shutdown = SessionStore._shutdown_affinity

    def gated_submit(self, fn, *args, **kwargs):
        future = submit(self, fn, *args, **kwargs)
        if getattr(fn, "__name__", "") == "append_event" and args[1] == "text_chunk":
            loop.call_soon_threadsafe(append_queued.set)
            assert release.wait(5), "recovery append was not released"
        return future

    def observed_shutdown(self):
        if self._closed:
            loop.call_soon_threadsafe(close_started.set)
        shutdown(self)

    monkeypatch.setattr(SessionStore, "submit", gated_submit)
    monkeypatch.setattr(SessionStore, "_shutdown_affinity", observed_shutdown)
    task = asyncio.create_task(resume_agent_async(path, llm=ScriptedLLM(["unused"])))
    try:
        await asyncio.wait_for(append_queued.wait(), timeout=3)
        task.cancel()
        await asyncio.sleep(0)
        if cancel_twice:
            task.cancel()
            await asyncio.sleep(0)
        # On a broken implementation close marks the store closed before
        # attachment can submit the recovery-slot clear. A safe cancellation
        # waits for attachment; give the old close path time to enter its gate.
        try:
            await asyncio.wait_for(close_started.wait(), timeout=0.2)
        except asyncio.TimeoutError:
            pass
    finally:
        release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    monkeypatch.setattr(SessionStore, "submit", submit)
    monkeypatch.setattr(SessionStore, "_shutdown_affinity", shutdown)
    check = SessionStore(path)
    try:
        assert check.state.get("resumee:open_text") == ""
        assert [
            e["content"]
            for e in check.get_events("resumee")
            if e["type"] == "text_chunk"
        ] == ["interrupted response"]
    finally:
        check.close(update_status=False)
    agent, store = await resume_agent_async(path, llm=ScriptedLLM(["unused"]))
    try:
        await agent._session_output.drain()
        events = await store.run(store.get_events, "resumee")
        assert [e["content"] for e in events if e["type"] == "text_chunk"] == [
            "interrupted response"
        ]
    finally:
        await asyncio.to_thread(store.close, update_status=False)


@pytest.mark.parametrize("cancel_twice", [False, True])
async def test_cancelled_open_closes_unclaimed_vaults(
    tmp_path, monkeypatch, cancel_twice
):
    path = _interrupted_session(tmp_path)
    entered = threading.Event()
    release = threading.Event()
    returned = threading.Event()
    references = []
    daemons = []
    opener = resume_async._open_store_with_migration

    def gated_open(*args, **kwargs):
        store = opener(*args, **kwargs)
        references.append(weakref.ref(store.events))
        daemons.append(store.events._daemon_thread)
        entered.set()
        assert release.wait(5)
        returned.set()
        return store

    monkeypatch.setattr(resume_async, "_open_store_with_migration", gated_open)
    task = asyncio.create_task(resume_agent_async(path, llm=ScriptedLLM(["unused"])))
    try:
        assert await asyncio.to_thread(entered.wait, 3)
        task.cancel()
        await asyncio.sleep(0)
        if cancel_twice:
            task.cancel()
            await asyncio.sleep(0)
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert await asyncio.to_thread(returned.wait, 3)
        await asyncio.sleep(0)
        gc.collect()
        assert all(ref() is None or ref()._closed for ref in references)
        assert all(not thread.is_alive() for thread in daemons)
    finally:
        release.set()
        for ref in references:
            vault = ref()
            if vault is not None:
                await asyncio.to_thread(vault.close)
