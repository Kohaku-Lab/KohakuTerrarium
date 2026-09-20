"""Unit tests for :mod:`kohakuterrarium.builtins.tools.search_memory`."""

from kohakuterrarium.builtins.tools.search_memory import (
    SEARCH_RESULT_DISPLAY_CHARS,
    SearchMemoryTool,
)
from kohakuterrarium.modules.tool.base import ToolContext
from kohakuterrarium.session.memory import SearchResult


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
            await tool._ensure_indexed(ctx, _FakeMemory([]))
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
