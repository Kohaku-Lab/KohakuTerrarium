"""Model-facing retrieval must not turn its own tool feedback into evidence."""

from contextlib import closing
from types import SimpleNamespace
import re

import numpy as np
import pytest

from kohakuterrarium.session.memory.blocks import TOOL_RESULT_INDEX_CHARS
from kohakuterrarium.session.memory.embedding import BaseEmbedder
from kohakuterrarium.session.memory.index import SessionMemory
from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.builtins.tools.search_memory import SearchMemoryTool
from kohakuterrarium.core.session import Session
from kohakuterrarium.modules.tool.base import ToolContext
from kohakuterrarium.llm.tools.schemas import _BUILTIN_SCHEMAS


class _FixedEmbedder(BaseEmbedder):
    dimensions = 3

    def encode(self, texts):
        return np.tile(np.array([1, 0, 0], dtype=np.float32), (len(texts), 1))


def _event(eid, kind, *, content="", name="", args=None):
    data = {
        "event_id": eid,
        "type": kind,
        "turn_index": 1,
        "branch_id": 1,
        "parent_branch_path": [],
    }
    if name:
        data["name"] = name
    if args is not None:
        data["args"] = args
    if content:
        data["output" if kind == "tool_result" else "content"] = content
    return data


@pytest.mark.parametrize("mode", ["fts", "semantic", "hybrid"])
@pytest.mark.parametrize("scoped", [False, True])
def test_self_search_echo_does_not_consume_ranked_top_k(tmp_path, mode, scoped):
    rows = [
        _event(1, "user_input", content="benchmark"),
        _event(
            2,
            "tool_result",
            content="Actual 31.933066093773366 tokens/s",
            name="python",
        ),
        _event(
            3, "tool_call", name="search_memory", args={"query": "31.933066093773366"}
        ),
        _event(
            4,
            "tool_result",
            content="Found 1 result: 31.933066093773366",
            name="search_memory",
        ),
        _event(
            5, "tool_call", name="search_memory", args={"query": "31.933066093773366"}
        ),
    ]
    with closing(
        SessionMemory(
            str(tmp_path / "echo.kohakutr"),
            _FixedEmbedder(),
            index_all_branches=True,
        )
    ) as memory:
        memory.index_events("alice", rows)
        scope = {"branch_view": {1: 1}} if scoped else {}
        hits = memory.search("31.933066093773366", mode=mode, k=1, **scope)
        assert hits
        assert hits[0].tool_name != "search_memory"
        if mode == "fts":
            assert hits[0].tool_name == "python"
            assert "31.933066093773366" in hits[0].content
        debug = memory.search(
            "31.933066093773366",
            mode=mode,
            k=10,
            include_search_history=True,
            **scope,
        )
        assert any(hit.tool_name == "search_memory" for hit in debug)


def test_long_tool_output_has_an_explicit_index_limit(tmp_path):
    value = "31.933066093773366"
    content = (
        "run_current_full.py "
        + value
        + " "
        + "X" * (TOOL_RESULT_INDEX_CHARS + 100)
        + " TAIL_95.10 tokens/s"
    )
    rows = [
        _event(1, "user_input", content="benchmark"),
        _event(2, "tool_result", content=content, name="python"),
    ]
    with closing(
        SessionMemory(str(tmp_path / "long.kohakutr"), index_all_branches=True)
    ) as memory:
        memory.index_events("alice", rows)
        hits = memory.search(value, mode="fts", k=1, branch_view={1: 1})
        assert len(hits) == 1
        assert hits[0].tool_name == "python"
        assert value in hits[0].content
        # The underlying full source remains navigable by anchor, but the
        # indexed prefix cannot promise full-text recall past this limit.
        assert memory.search("TAIL_95.10", mode="fts", k=5, branch_view={1: 1}) == []


@pytest.mark.asyncio
async def test_real_agent_tool_excludes_its_own_feedback_and_can_opt_in(tmp_path):
    path = tmp_path / "agent-echo.kohakutr"
    with closing(SessionStore(path)) as store:
        store.init_meta("s", "agent", "", str(tmp_path), ["alice"])
        for kind, fields in (
            ("user_input", {"content": "Benchmark"}),
            (
                "tool_result",
                {"name": "python", "output": "Actual 31.933066093773366 tokens/s"},
            ),
            (
                "tool_call",
                {"name": "search_memory", "args": {"query": "31.933066093773366"}},
            ),
            (
                "tool_result",
                {"name": "search_memory", "output": "Search result 31.933066093773366"},
            ),
        ):
            store.append_event(
                "alice",
                kind,
                fields,
                turn_index=1,
                branch_id=1,
                parent_branch_path=[],
            )
        agent = SimpleNamespace(
            session_store=store,
            config=SimpleNamespace(name="alice", memory=None),
            _turn_index=1,
            _branch_id=1,
            _parent_branch_path=(),
        )
        context = ToolContext(
            agent_name="alice",
            agent=agent,
            session=Session(key="s"),
            working_dir=tmp_path,
        )
        tool = SearchMemoryTool()
        try:
            found = await tool.execute(
                {"query": "31.933066093773366", "mode": "fts", "k": 1},
                context=context,
            )
            assert found.error is None
            assert "Actual 31.933066093773366" in found.output
            assert "tool:search_memory" not in found.output
            assert re.search(r"^anchor: [-A-Za-z0-9_]+$", found.output, re.M)

            debug = await tool.execute(
                {
                    "query": "31.933066093773366",
                    "mode": "fts",
                    "include_search_history": "true",
                    "k": 5,
                },
                context=context,
            )
            assert debug.error is None
            assert "tool:search_memory" in debug.output

            invalid = await tool.execute(
                {"query": "31.933066093773366", "include_search_history": "yes"},
                context=context,
            )
            assert "must be true or false" in invalid.error
        finally:
            live_memory = getattr(context.session, "_memory", None)
            if live_memory is not None:
                live_memory.close()


def test_model_schema_exposes_debug_only_echo_inclusion():
    prop = _BUILTIN_SCHEMAS["search_memory"]["properties"]["include_search_history"]
    assert prop["type"] == "boolean"
    assert "default false" in prop["description"]
