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

    def encode_one(self, query):
        return np.ones(4, dtype=np.float32)


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


def test_bounded_search_returns_partial_hits_with_warning_not_an_error(
    tmp_path, monkeypatch
):
    import kohakuterrarium.session.memory.index as index_mod

    # Tiny budget forces the same last-page condition without a huge FTS corpus.
    monkeypatch.setattr(index_mod, "_MAX_RANKED_CANDIDATES", 4)
    memory = SessionMemory(str(tmp_path / "small-budget.kohakutr"))
    try:
        memory.index_events(
            "alice",
            [
                {"type": "user_input", "content": "historical note"},
                {
                    "type": "tool_result",
                    "name": "bash",
                    "output": f"real {MARKER} result",
                },
                *(
                    {
                        "type": "tool_call",
                        "name": "search_memory",
                        "args": {"query": MARKER, "sequence": i},
                    }
                    for i in range(5)
                ),
            ],
        )
        # Ranked FTS can select few, many, or no real hits at the budget.
        hits = memory.search(MARKER, mode="fts", k=5)
        assert isinstance(hits, list)
        assert all(hit.tool_name != "search_memory" for hit in hits)
        assert hits.warning is not None
        assert "may be incomplete" in hits.warning
        assert "4" in hits.warning
    finally:
        memory.close()


def test_vec0_backend_limit_does_not_fail_on_partial_result(tmp_path):
    """Real vec0 backend must never receive k greater than 4096."""
    from kohakuterrarium.session.memory.index import _MAX_VECTOR_CANDIDATES

    assert _MAX_VECTOR_CANDIDATES == 4096
    memory = SessionMemory(str(tmp_path / "vec0-window.kohakutr"), _Embedder())
    try:
        query = np.ones(4, dtype=np.float32)
        hidden = np.ones(4, dtype=np.float32)
        # Earlier ranked rows are search-generated echoes; the true result is
        # outside the 4096-result backend window.
        for i in range(4096):
            memory._vec.insert(
                hidden,
                {
                    "content": f"search echo {i}",
                    "agent": "alice",
                    "tool_name": "search_memory",
                    "type": "tool",
                    "block_id": f"echo:{i}",
                },
            )
        memory._vec.insert(
            -np.ones(4, dtype=np.float32),
            {
                "content": "historical real result",
                "agent": "alice",
                "tool_name": "bash",
                "type": "tool",
                "block_id": "actual",
            },
        )
        assert memory._vec.count() == 4097
        hits = memory._search_vector(query, k=5, agent=None)
        assert hits == []
        assert hits.warning is not None
        assert "4096" in hits.warning
        # It is safe to report uncertainty rather than claiming the corpus
        # has no matching source or throwing a native vec0 exception.
    finally:
        memory.close()


async def test_live_search_surfaces_partial_warning_with_no_hits(tmp_path, monkeypatch):
    import kohakuterrarium.session.memory.index as index_mod

    monkeypatch.setattr(index_mod, "_MAX_RANKED_CANDIDATES", 3)
    store = SessionStore(tmp_path / "warning.kohakutr")
    store.init_meta("s", "agent", "", str(tmp_path), ["alice"])
    store.append_event("alice", "user_input", {"content": "No genuine matching marker"})
    for i in range(4):
        store.append_event(
            "alice",
            "tool_call",
            {"name": "search_memory", "args": {"query": MARKER, "sequence": i}},
        )
    context = ToolContext(
        agent_name="alice",
        session=Session(key="warning"),
        agent=SimpleNamespace(session_store=store, config=None),
        working_dir=None,
    )
    try:
        result = await SearchMemoryTool().execute(
            {"query": MARKER, "mode": "fts"}, context=context
        )
        assert result.error is None
        assert "may be incomplete" in result.output
        assert "No eligible hits within the scanned candidate budget." in result.output
        assert "No results found." not in result.output
    finally:
        memory = getattr(context.session, "_memory", None)
        if memory is not None:
            memory.close()
        store.close(update_status=False)


def test_exhausted_corpus_does_not_claim_partial_results(tmp_path):
    memory = SessionMemory(str(tmp_path / "complete.kohakutr"), _Embedder())
    try:
        memory.index_events(
            "alice",
            [{"type": "user_input", "content": f"ONLY_REAL_{MARKER}"}],
        )
        for mode in ("fts", "semantic", "hybrid"):
            hits = memory.search(MARKER, mode=mode, k=5)
            assert hits
            assert hits.warning is None
    finally:
        memory.close()


async def test_hybrid_keeps_fts_hits_and_semantic_partial_warning(
    tmp_path, monkeypatch
):
    """Budget exhaustion is not an embedding failure and must not trigger fallback."""
    import kohakuterrarium.builtins.tools.search_memory as tool_mod
    import kohakuterrarium.session.memory.index as index_mod

    class EchoPreferredEmbedder(_Embedder):
        def encode(self, texts):
            # Echoes rank in front of the genuine result in vector retrieval.
            return np.stack(
                [
                    (
                        np.ones(4, dtype=np.float32)
                        if "search_memory" in text
                        else -np.ones(4, dtype=np.float32)
                    )
                    for text in texts
                ]
            )

    monkeypatch.setattr(index_mod, "_MAX_VECTOR_CANDIDATES", 3)
    monkeypatch.setattr(tool_mod, "create_embedder", lambda _: EchoPreferredEmbedder())
    store = SessionStore(tmp_path / "hybrid-partial.kohakutr")
    store.init_meta("s", "agent", "", str(tmp_path), ["alice"])
    store.append_event("alice", "user_input", {"content": f"ACTUAL_EVIDENCE_{MARKER}"})
    for i in range(6):
        store.append_event(
            "alice",
            "tool_call",
            {"name": "search_memory", "args": {"query": MARKER, "sequence": i}},
        )
    context = ToolContext(
        agent_name="alice",
        session=Session(key="s"),
        agent=SimpleNamespace(session_store=store, config=None),
        working_dir=None,
    )
    try:
        result = await SearchMemoryTool().execute(
            {"query": MARKER, "mode": "hybrid", "k": 5}, context=context
        )
        assert result.error is None, result.error
        assert "ACTUAL_EVIDENCE_" in result.output
        assert "Search results may be incomplete" in result.output
        assert "Semantic candidate limit (3)" in result.output
        assert "Embedding unavailable" not in result.output
    finally:
        memory = getattr(context.session, "_memory", None)
        if memory is not None:
            memory.close()
        store.close(update_status=False)


async def test_offline_studio_search_displays_partial_warning(tmp_path, monkeypatch):
    import kohakuterrarium.session.memory.index as index_mod
    from kohakuterrarium.studio.sessions.memory.search import search_session_memory

    monkeypatch.setattr(index_mod, "_MAX_RANKED_CANDIDATES", 3)
    path = tmp_path / "studio-warning.kohakutr"
    store = SessionStore(path)
    store.init_meta("s", "agent", "", str(tmp_path), ["alice"])
    for i in range(5):
        store.append_event(
            "alice",
            "tool_call" if i else "user_input",
            (
                {"content": "Old session"}
                if not i
                else {"name": "search_memory", "args": {"query": MARKER, "sequence": i}}
            ),
        )
    store.close(update_status=False)

    response = await search_session_memory(path, q=MARKER, mode="fts", k=5)
    assert response["count"] == 0
    assert "may be incomplete" in response["warning"]
