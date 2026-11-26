"""Keep existing Search useful when previous search_memory calls echo the query."""

import re
from types import SimpleNamespace

import numpy as np
import pytest

from kohakuterrarium.builtins.tools.search_memory import SearchMemoryTool
from kohakuterrarium.core.session import Session
from kohakuterrarium.modules.tool.base import ToolContext
from kohakuterrarium.session.memory.embedding import BaseEmbedder
from kohakuterrarium.session.memory.index import SessionMemory
from kohakuterrarium.session.store import SessionStore

MARKER = "31.933066093773366"


class _Embedder(BaseEmbedder):
    dimensions = 4

    def encode(self, texts):
        return np.ones((len(texts), 4), dtype=np.float32)


@pytest.mark.parametrize("mode", ["fts", "semantic", "hybrid"])
def test_prior_search_calls_never_displace_relevant_top_k(tmp_path, mode):
    """Filter before ranking's final k; existing stored echo rows remain present."""
    rows = [
        {"type": "user_input", "content": "Benchmark historical result"},
        {
            "type": "tool_result",
            "name": "bash",
            "output": f"Actual speed {MARKER} tokens per second",
        },
    ]
    for _ in range(55):
        rows.extend(
            [
                {
                    "type": "tool_call",
                    "name": "search_memory",
                    "args": {"query": MARKER},
                },
                {
                    "type": "tool_result",
                    "name": "search_memory",
                    "output": f"Found result for {MARKER}",
                },
            ]
        )
    memory = SessionMemory(str(tmp_path / "echo.kohakutr"), _Embedder())
    try:
        memory.index_events("alice", rows)
        # The corpus is unchanged, including previous search invocations.
        assert memory._fts.count() > 100
        hits = memory.search(MARKER, mode=mode, k=1)
        assert len(hits) == 1
        assert hits[0].tool_name != "search_memory"
        if mode == "fts":
            assert hits[0].tool_name == "bash"
            assert MARKER in hits[0].content
        default_top_five = memory.search(MARKER, mode=mode, k=5)
        assert default_top_five
        assert all(hit.tool_name != "search_memory" for hit in default_top_five)
    finally:
        memory.close()


def test_search_only_echo_corpus_returns_no_results(tmp_path):
    rows = [
        {"type": "user_input", "content": "Old context"},
        {"type": "tool_call", "name": "search_memory", "args": {"query": MARKER}},
        {
            "type": "tool_result",
            "name": "search_memory",
            "output": f"Echo: {MARKER}",
        },
    ]
    memory = SessionMemory(str(tmp_path / "echo-only.kohakutr"))
    try:
        memory.index_events("alice", rows)
        assert memory.search(MARKER, mode="fts", k=5) == []
    finally:
        memory.close()


async def test_tool_call_and_truncated_result_show_actionable_navigation(tmp_path):
    store = SessionStore(tmp_path / "navigation.kohakutr")
    store.init_meta("s", "agent", "", str(tmp_path), ["alice"])
    agent = SimpleNamespace(session_store=store, config=None)
    context = ToolContext(
        agent_name="alice",
        agent=agent,
        session=Session(key="s"),
        working_dir=None,
    )
    store.append_event("alice", "user_input", {"content": "Check historical output"})
    store.append_event(
        "alice",
        "tool_call",
        {"name": "bash", "args": {"command": "echo ORIGINAL_COMMAND_MARKER"}},
    )
    store.append_event(
        "alice",
        "tool_result",
        {"name": "bash", "output": "FIRST_TOKEN_" + "x" * 1300 + "_LAST_TOKEN"},
    )
    tool = SearchMemoryTool()
    try:
        found = await tool.execute(
            {"query": "ORIGINAL_COMMAND_MARKER", "mode": "fts"}, context=context
        )
        assert found.error is None
        anchor = re.search(r"^anchor: ([a-zA-Z0-9_-]+)$", found.output, re.M).group(1)
        assert f'search_memory(anchor="{anchor}", before=0, after=3)' in found.output
        expanded = await tool.execute(
            {"anchor": anchor, "before": 0, "after": 1}, context=context
        )
        assert expanded.error is None
        assert "FIRST_TOKEN_" in expanded.output
        result_anchor = re.findall(
            r"^anchor: ([a-zA-Z0-9_-]+)$", expanded.output, re.M
        )[-1]
        assert (
            f'search_memory(anchor="{result_anchor}", before=0, after=0, offset=900)'
            in expanded.output
        )
    finally:
        memory = getattr(context.session, "_memory", None)
        if memory is not None:
            memory.close()
        store.close(update_status=False)
