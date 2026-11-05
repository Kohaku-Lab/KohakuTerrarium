"""Async resume must recover interrupted output without scanning on the loop."""

import asyncio
import gc
import threading
import weakref

import pytest
from kohakuvault import KVault

from kohakuterrarium import Agent
from kohakuterrarium.session.resume import async_flow as resume_async
from kohakuterrarium.session.resume.async_flow import resume_agent_async
from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.testing.llm import ScriptedLLM
from kohakuterrarium.session.store.events import branch_index as branch_index_module


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


async def test_clean_checkpoint_continues_without_historical_payload_scan(
    tmp_path, monkeypatch
):
    config = tmp_path / "clean-creature"
    config.mkdir()
    (config / "config.yaml").write_text(
        "name: clean\nsystem_prompt: checkpoint test\n"
        "controller: {tool_format: bracket, include_tools_in_prompt: false, "
        "include_hints_in_prompt: false}\n"
        "input: {type: none}\noutput: {type: none}\n"
    )
    path = tmp_path / "clean.kohakutr"
    agent = await Agent.build(config, llm=ScriptedLLM(["saved answer"]))
    store = SessionStore(path)
    store.init_meta("clean", "agent", str(config), str(tmp_path), ["clean"])
    agent.attach_session_store(store)
    try:
        await agent.start()
        await agent.run("saved question")
        assert store._resume_candidate is not None
        expected = agent.controller.conversation.snapshot_messages(
            preserve_pending_tail=True
        )
    finally:
        await agent.stop()
        assert store._resume_candidate is not None
        historical_keys = {
            f"clean:e{index:06d}" for index in range(store._event_seq["clean"])
        }
        store.close(update_status=False)

    reads = []
    original = SessionStore.get_events
    original_prefix = branch_index_module.read_prefix

    def observed_get_events(self, *args, **kwargs):
        reads.append("get_events")
        return original(self, *args, **kwargs)

    def observed_prefix(*args, **kwargs):
        reads.append("branch-index")
        return original_prefix(*args, **kwargs)

    for method in ("get", "__getitem__", "get_many"):
        original_read = getattr(KVault, method)

        def observe_payload(self, key, *args, _read=original_read, **kwargs):
            requested = key if isinstance(key, (list, tuple)) else [key]
            if any(
                (k.decode() if isinstance(k, bytes) else k) in historical_keys
                for k in requested
            ):
                reads.append("historical-payload")
            return _read(self, key, *args, **kwargs)

        monkeypatch.setattr(KVault, method, observe_payload)
    monkeypatch.setattr(SessionStore, "get_events", observed_get_events)
    monkeypatch.setattr(branch_index_module, "read_prefix", observed_prefix)
    for turn in (2, 3):
        llm = ScriptedLLM([f"continued {turn}"])
        resumed, reopened = await resume_agent_async(path, llm=llm)
        try:
            assert (
                resumed.controller.conversation.snapshot_messages(
                    preserve_pending_tail=True
                )
                == expected
            )
            assert resumed._turn_index == turn - 1
            await resumed.start()
            result = await resumed.run(f"next question {turn}")
            assert result.text == f"continued {turn}"
            assert resumed._turn_index == turn
            assert resumed._branch_id == 1
            assert llm.call_count == 1
            assert any(m.get("content") == "saved question" for m in llm.last_messages)
            expected = resumed.controller.conversation.snapshot_messages(
                preserve_pending_tail=True
            )
        finally:
            await resumed.stop()
            reopened.close(update_status=False)
    assert reads == []


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
            assert release.wait(30), "recovery was not released"
        return original(self, *args, **kwargs)

    monkeypatch.setattr(SessionStore, "get_events", gated_get_events)
    task = asyncio.create_task(resume_agent_async(path, llm=ScriptedLLM(["unused"])))
    cancel_requested = False
    try:
        await asyncio.wait_for(recovery_started.wait(), timeout=30)
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
            assert release.wait(30), "recovery append was not released"
        return future

    def observed_shutdown(self):
        if self._closed:
            loop.call_soon_threadsafe(close_started.set)
        shutdown(self)

    monkeypatch.setattr(SessionStore, "submit", gated_submit)
    monkeypatch.setattr(SessionStore, "_shutdown_affinity", observed_shutdown)
    task = asyncio.create_task(resume_agent_async(path, llm=ScriptedLLM(["unused"])))
    try:
        await asyncio.wait_for(append_queued.wait(), timeout=30)
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
    loop = asyncio.get_running_loop()
    entered = asyncio.Event()
    release = threading.Event()
    returned = threading.Event()
    references = []
    pipelines = []
    opener = resume_async._open_store_with_migration

    def gated_open(*args, **kwargs):
        store = opener(*args, **kwargs)
        references.append(weakref.ref(store.events))
        pipelines.append(store._flush)
        loop.call_soon_threadsafe(entered.set)
        assert release.wait(30)
        returned.set()
        return store

    monkeypatch.setattr(resume_async, "_open_store_with_migration", gated_open)
    task = asyncio.create_task(resume_agent_async(path, llm=ScriptedLLM(["unused"])))
    try:
        await asyncio.wait_for(entered.wait(), timeout=30)
        task.cancel()
        await asyncio.sleep(0)
        if cancel_twice:
            task.cancel()
            await asyncio.sleep(0)
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert returned.is_set()
        gc.collect()
        assert all(ref() is None or ref()._closed for ref in references)
        assert all(p._closed and p._timer is None for p in pipelines)
    finally:
        release.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        for ref in references:
            vault = ref()
            if vault is not None:
                await asyncio.to_thread(vault.close)
