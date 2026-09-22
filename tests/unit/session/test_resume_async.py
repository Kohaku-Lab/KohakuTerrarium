"""Async resume must recover interrupted output without scanning on the loop."""

import asyncio
import threading

import pytest

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
