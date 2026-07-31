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
