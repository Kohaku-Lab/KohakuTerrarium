"""Unit tests for :mod:`kohakuterrarium.builtins.tools.search_memory`."""

import asyncio
import threading
from types import SimpleNamespace

import numpy as np
import pytest

import kohakuterrarium.builtins.tools.search_memory as search_mod
from kohakuterrarium.core.session import Session
from kohakuterrarium.session.embedding import BaseEmbedder, NullEmbedder
from kohakuterrarium.session.output import SessionOutput
from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.terrarium.history_service import LocalHistoryServiceMixin
from kohakuterrarium.builtins.tools.search_memory import (
    SEARCH_RESULT_DISPLAY_CHARS,
    SearchMemoryTool,
)
from kohakuterrarium.modules.tool.base import ToolContext
from kohakuterrarium.session.memory import SearchResult


@pytest.mark.parametrize("phase", ["load", "index", "query"])
async def test_blocked_embedding_keeps_history_writes_and_close_available(
    tmp_path, monkeypatch, phase
):
    store = SessionStore(tmp_path / "blocked.kohakutr")
    store.init_meta("s", "agent", "", str(tmp_path), ["alice"])
    store.append_event("alice", "user_input", {"content": "original needle"})
    store.flush()
    agent = SimpleNamespace(
        session_store=store, config=None, is_processing=False, conversation_history=[]
    )
    context = ToolContext(
        agent_name="alice", session=Session(key="s"), agent=agent, working_dir=None
    )
    service = LocalHistoryServiceMixin()
    creature = SimpleNamespace(agent=agent, name="alice", graph_id="s")
    service._history_source = lambda _: (creature, store)
    output = SessionOutput("alice", store, None)
    await output.drain()
    entered, release = threading.Event(), threading.Event()

    def block():
        entered.set()
        assert release.wait(10), "embedding test gate was not released"

    class Embedder(BaseEmbedder):
        dimensions = 2

        def encode(self, texts):
            if phase == "index":
                block()
            return np.ones((len(texts), 2), dtype=np.float32)

        def encode_one(self, text):
            if phase == "query":
                block()
            return np.ones(2, dtype=np.float32)

    def create(_):
        if phase == "load":
            block()
        return Embedder()

    monkeypatch.setattr(search_mod, "create_embedder", create)
    task = asyncio.create_task(
        SearchMemoryTool().execute({"query": "needle"}, context=context)
    )
    try:
        assert await asyncio.to_thread(entered.wait, 3)
        history = await asyncio.wait_for(service.chat_history_page("alice"), 1)
        assert any(e.get("content") == "original needle" for e in history["events"])
        output._append_event("user_input", {"content": "saved while embedding waits"})
        await asyncio.wait_for(output.drain(), 1)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await asyncio.wait_for(asyncio.to_thread(store.close, update_status=False), 1)
        reader = SessionStore.open_readonly(store.path)
        try:
            assert any(
                e.get("content") == "saved while embedding waits"
                for e in reader.get_events("alice")
            )
        finally:
            reader.close(update_status=False)
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        memory = getattr(context.session, "_memory", None)
        if memory is not None:
            memory.close()
        await asyncio.to_thread(store.close, update_status=False)


async def test_fts_does_not_create_embedder(tmp_path, monkeypatch):
    store = SessionStore(tmp_path / "fts.kohakutr")
    store.init_meta("s", "agent", "", str(tmp_path), ["alice"])
    store.append_event("alice", "user_input", {"content": "needle"})
    calls = []
    monkeypatch.setattr(
        search_mod, "create_embedder", lambda cfg: calls.append(cfg) or NullEmbedder()
    )
    ctx = ToolContext(
        agent_name="alice",
        session=Session(key="s"),
        working_dir=None,
        agent=SimpleNamespace(session_store=store, config=None),
    )
    try:
        result = await SearchMemoryTool().execute(
            {"query": "needle", "mode": "fts"}, context=ctx
        )
        assert result.error is None
        assert "needle" in result.output
        assert calls == []
    finally:
        memory = getattr(ctx.session, "_memory", None)
        if memory is not None:
            memory.close()
        store.close(update_status=False)


class _FakeMemory:
    def __init__(self, results):
        self._results = results

    def search(self, query, mode="auto", k=5, agent=None):
        return self._results


class _FakeSession:
    def __init__(self, results):
        self._memory = _FakeMemory(results)


def _ctx(session):
    return ToolContext(agent_name="agent", session=session, working_dir=None)


async def _run(query, results):
    tool = SearchMemoryTool()
    ctx = _ctx(_FakeSession(results))
    return await tool.execute({"query": query}, context=ctx)


class TestSearchMemoryDisplay:
    async def test_no_results(self):
        result = await _run("q", [])
        assert "No results found" in result.output

    async def test_long_result_display_capped_at_constant(self):
        long_content = "y" * 5000
        result = await _run(
            "q",
            [
                SearchResult(
                    content=long_content,
                    round_num=1,
                    block_num=1,
                    agent="a",
                    block_type="tool",
                    score=1.0,
                    tool_name="bash",
                )
            ],
        )
        assert "bash" in result.output
        assert f"({len(long_content)} chars total)" in result.output
        assert "y" * SEARCH_RESULT_DISPLAY_CHARS in result.output
        assert long_content not in result.output

    async def test_short_result_not_capped(self):
        result = await _run(
            "q",
            [
                SearchResult(
                    content="needle",
                    round_num=1,
                    block_num=1,
                    agent="a",
                    block_type="tool",
                    score=1.0,
                )
            ],
        )
        assert "needle" in result.output
        assert "chars total" not in result.output


class TestEnsureIndexedOffLoop:
    async def test_index_refresh_does_not_block_event_loop(self, tmp_path):
        # S5 negative case: the full-table event scan for index refresh
        # runs on the store's affinity thread, keeping the loop alive.
        import asyncio
        import time

        from kohakuterrarium.modules.tool.base import ToolContext
        from kohakuterrarium.session.store import SessionStore

        store = SessionStore(str(tmp_path / "tool-slow.kohakutr"))
        try:
            store.init_meta("sess", "agent", "/p", "/w", ["alice"])
            store.append_event("alice", "user_input", {"content": "hi"})
            store.flush()
            real_get_events = store.get_events

            def slow_get_events(agent, **kwargs):
                time.sleep(0.3)
                return real_get_events(agent, **kwargs)

            store.get_events = slow_get_events

            agent = type("A", (), {"session_store": store, "config": None})()
            ctx = ToolContext(
                agent_name="alice", session=None, working_dir=None, agent=agent
            )
            tool = SearchMemoryTool()
            loop_alive: list[float] = []
            stop = asyncio.Event()

            async def _ping():
                while not stop.is_set():
                    loop_alive.append(time.monotonic())
                    await asyncio.sleep(0.02)
                loop_alive.append(time.monotonic())

            ping = asyncio.create_task(_ping())
            await asyncio.sleep(0)
            result = await tool.execute({"query": "hi", "mode": "fts"}, context=ctx)
            assert result.error is None
            assert "hi" in result.output
            stop.set()
            await ping
            gaps = [
                loop_alive[i + 1] - loop_alive[i] for i in range(len(loop_alive) - 1)
            ]
            assert (
                max(gaps) < 0.15
            ), f"index refresh blocked the loop; max={max(gaps):.3f}s"
        finally:
            store.close()


async def test_concurrent_first_search_reuses_memory_per_session(tmp_path):
    tool = SearchMemoryTool()
    stores = []
    memories = []
    tasks = []
    contexts = []
    release = threading.Event()
    try:
        for name in ("alice", "bob"):
            store = SessionStore(str(tmp_path / f"{name}.kohakutr"))
            stores.append(store)
            store.init_meta(name, "agent", "", str(tmp_path), [name])
            store.state["embedding_config"] = {"provider": "none"}
            store.append_event(name, "user_input", {"content": f"{name} needle"})
            context = ToolContext(
                agent_name=name,
                session=Session(key=name),
                working_dir=str(tmp_path),
                agent=SimpleNamespace(session_store=store, config=None),
            )
            contexts.append(context)
            # Queue both cold requests before their serialized construction starts.
            store.submit(release.wait, 5)
            tasks.extend(
                asyncio.create_task(tool._get_memory(context)) for _ in range(2)
            )
        await asyncio.sleep(0)
        release.set()
        memories = await asyncio.gather(*tasks)
        assert memories[0] is memories[1] is contexts[0].session._memory
        assert memories[2] is memories[3] is contexts[1].session._memory
        assert memories[0] is not memories[2]
        for context in contexts:
            result = await tool.execute(
                {"query": "needle", "mode": "fts"}, context=context
            )
            assert result.error is None
            assert f"{context.agent_name} needle" in result.output
            other = "bob" if context.agent_name == "alice" else "alice"
            assert f"{other} needle" not in result.output
            assert await tool._get_memory(context) is context.session._memory
    finally:
        release.set()
        if tasks:
            memories = await asyncio.gather(*tasks)
        for memory in {id(m): m for m in memories}.values():
            memory.close()
        for store in stores:
            store.close(update_status=False)
