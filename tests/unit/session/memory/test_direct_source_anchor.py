"""Direct physical provenance in normal FTS/vector indexing and navigation."""

import re
from types import SimpleNamespace

import numpy as np
import pytest

import kohakuterrarium.session.memory.navigation as navigation

from kohakuterrarium.builtins.tools.search_memory import SearchMemoryTool
from kohakuterrarium.core.session import Session
from kohakuterrarium.modules.tool.base import ToolContext
from kohakuterrarium.session.memory.embedding import BaseEmbedder
from kohakuterrarium.session.memory.index import SessionMemory
from kohakuterrarium.session.store import SessionStore


class _Embedder(BaseEmbedder):
    dimensions = 4

    def encode(self, texts):
        return np.ones((len(texts), 4), dtype=np.float32)

    def encode_one(self, text):
        return np.ones(4, dtype=np.float32)


def _env(tmp_path):
    store = SessionStore(tmp_path / "direct.kohakutr")
    store.init_meta("s", "agent", "", str(tmp_path), ["alice"])
    agent = SimpleNamespace(session_store=store, config=None)
    ctx = ToolContext(
        agent=agent, session=Session(key="s"), agent_name="alice", working_dir=None
    )
    return store, ctx, SearchMemoryTool()


def _close(store, ctx):
    companion = getattr(ctx.session, "_memory", None)
    if companion is not None:
        companion.close()
    store.close(update_status=False)


def _token(output):
    matches = re.findall(r"^anchor: ([A-Za-z0-9_-]+)$", output, re.M)
    assert matches, output
    return matches[0]


async def test_query_and_expand_new_provenance_without_get_events(
    tmp_path, monkeypatch
):
    store, ctx, tool = _env(tmp_path)
    try:
        for kind, content in (
            ("user_input", "EXACT_DIRECT_SOURCE_USER"),
            ("text", "EXACT_DIRECT_SOURCE_REPLY"),
            ("processing_end", ""),
        ):
            store.append_event("alice", kind, {"content": content})

        def forbid(*args, **kwargs):
            raise AssertionError("Direct source navigation reread entire get_events")

        monkeypatch.setattr(store, "get_events", forbid)
        query = await tool.execute(
            {"query": "EXACT_DIRECT_SOURCE_REPLY", "mode": "fts"}, context=ctx
        )
        assert query.error is None, query.error
        token = _token(query.output)
        window = await tool.execute(
            {"anchor": token, "before": 2, "after": 2}, context=ctx
        )
        assert window.error is None, window.error
        assert "EXACT_DIRECT_SOURCE_USER" in window.output
        assert "EXACT_DIRECT_SOURCE_REPLY" in window.output
    finally:
        _close(store, ctx)


async def test_direct_metadata_survives_real_index_reopen_and_vector(
    tmp_path, monkeypatch
):
    import kohakuterrarium.builtins.tools.search_memory as module

    monkeypatch.setattr(module, "create_embedder", lambda _: _Embedder())
    store, ctx, tool = _env(tmp_path)
    try:
        store.append_event("alice", "user_input", {"content": "VECTOR_REAL_SOURCE"})
        query = await tool.execute(
            {"query": "VECTOR_REAL_SOURCE", "mode": "hybrid"}, context=ctx
        )
        assert query.error is None
        _token(query.output)
        live = ctx.session._memory
        assert live._memory._fts.get_by_id(1)[1]["source_ref"] == "alice:e000000"
        assert live._memory._vec.get_by_id(1)[1]["source_ref"] == "alice:e000000"
        # Main Search result interfaces themselves carry the exact source.
        assert live._memory.search("VECTOR_REAL_SOURCE", mode="fts")[0].source_ref == (
            "alice:e000000"
        )
        assert live._memory.search("VECTOR_REAL_SOURCE", mode="semantic")[
            0
        ].source_ref == ("alice:e000000")
        live.close()
        reopened = SessionMemory(str(store.path), _Embedder())
        try:
            assert reopened._fts.get_by_id(1)[1]["source_fingerprint"]
            assert reopened._vec.get_by_id(1)[1]["source_fingerprint"]
        finally:
            reopened.close()
    finally:
        _close(store, ctx)


async def test_direct_window_skips_physical_sequence_hole(tmp_path):
    store, ctx, tool = _env(tmp_path)
    try:
        store.append_event("alice", "user_input", {"content": "DIRECT_FIRST"})
        store._event_seq["alice"] = 2
        store.append_event("alice", "text", {"content": "DIRECT_AFTER_GAP"})
        original = await tool.execute(
            {"query": "DIRECT_FIRST", "mode": "fts"}, context=ctx
        )
        assert original.error is None
        window = await tool.execute(
            {"anchor": _token(original.output), "before": 0, "after": 2},
            context=ctx,
        )
        assert window.error is None, window.error
        assert "DIRECT_AFTER_GAP" in window.output
        assert "has_after=false" in window.output
    finally:
        _close(store, ctx)


async def test_direct_window_returns_verified_prefix_on_excessive_sequence_gap(
    tmp_path, monkeypatch
):
    store, ctx, tool = _env(tmp_path)
    try:
        store.append_event("alice", "user_input", {"content": "DIRECT_GAP_SOURCE"})
        store._event_seq["alice"] = 500
        store.append_event("alice", "text", {"content": "DIRECT_GAP_TAIL"})
        source = await tool.execute(
            {"query": "DIRECT_GAP_SOURCE", "mode": "fts"}, context=ctx
        )
        assert source.error is None
        result = await tool.execute(
            {"anchor": _token(source.output), "before": 0, "after": 1},
            context=ctx,
        )
        assert result.error is None
        assert "DIRECT_GAP_SOURCE" in result.output
        assert "DIRECT_GAP_TAIL" not in result.output
        assert "has_after=unknown" in result.output
    finally:
        _close(store, ctx)


async def test_direct_hit_rejects_misbound_block_even_with_valid_source_fingerprint(
    tmp_path,
):
    from kohakuterrarium.session.memory.blocks import _anchor

    store, ctx, tool = _env(tmp_path)
    try:
        store.append_event("alice", "user_input", {"content": "ORIGINAL_BINDING"})
        store.append_event("alice", "text", {"content": "OTHER_VALID_SOURCE"})
        query = await tool.execute(
            {"query": "ORIGINAL_BINDING", "mode": "fts"}, context=ctx
        )
        assert query.error is None
        assert _token(query.output)

        memory = ctx.session._memory._memory
        row, score, meta = memory._fts.search("ORIGINAL_BINDING", k=1)[0]
        text, meta = memory._fts.get_by_id(row)
        target = store.events["alice:e000001"]
        memory._fts.update(
            row,
            value={
                **meta,
                "source_ref": "alice:e000001",
                "source_fingerprint": _anchor(target),
            },
        )
        result = await tool.execute(
            {"query": "ORIGINAL_BINDING", "mode": "fts"}, context=ctx
        )
        assert result.error is None
        assert "ORIGINAL_BINDING" in result.output
        assert "STALE_INDEX" in result.output
        assert not re.search(r"^anchor:", result.output, re.M)
    finally:
        _close(store, ctx)


async def test_old_unannotated_index_requires_explicit_migration(tmp_path):
    store, ctx, tool = _env(tmp_path)
    try:
        store.append_event("alice", "user_input", {"content": "LEGACY_SOURCE_CONTENT"})
        # Emulate main's existing index, built before source metadata was added.
        legacy = SessionMemory(str(store.path))
        try:
            legacy.index_events("alice", store.get_events("alice"))
            assert "source_ref" not in legacy._fts.get_by_id(1)[1]
        finally:
            legacy.close()
        result = await tool.execute(
            {"query": "LEGACY_SOURCE_CONTENT", "mode": "fts"}, context=ctx
        )
        assert result.error is None
        assert "LEGACY_SOURCE_CONTENT" in result.output
        assert "ANCHOR_REBUILD_REQUIRED" in result.output
        assert not re.search(r"^anchor:", result.output, re.M)
        # Ordinary search must not silently perform experimental backfill.
        assert "source_ref" not in ctx.session._memory._memory._fts.get_by_id(1)[1]
    finally:
        _close(store, ctx)


async def test_physical_anchor_survives_saved_branch_index_mismatch(tmp_path):
    from kohakuterrarium.core.conversation.continuation import persist_branch_view

    store, ctx, tool = _env(tmp_path)
    try:
        for branch, marker in (
            (1, "OLDER_SAVED_SELECTION"),
            (2, "LATEST_INDEX_SELECTION"),
        ):
            store.append_event(
                "alice",
                "user_input",
                {"content": marker},
                turn_index=1,
                branch_id=branch,
                parent_branch_path=[],
            )
        persist_branch_view(store, "alice", {1: 1})
        result = await tool.execute(
            {"query": "LATEST_INDEX_SELECTION", "mode": "fts"}, context=ctx
        )
        assert result.error is None
        assert "LATEST_INDEX_SELECTION" in result.output
        token = _token(result.output)
        args = {"anchor": token, "before": 1, "after": 0}
        selected = await tool.execute(args, context=ctx)
        assert selected.error is None
        assert "LATEST_INDEX_SELECTION" not in selected.output
        assert "has_after=unknown" in selected.output
        raw = await tool.execute({**args, "scope": "raw"}, context=ctx)
        assert raw.error is None
        assert "OLDER_SAVED_SELECTION" in raw.output
        assert "LATEST_INDEX_SELECTION" in raw.output
    finally:
        _close(store, ctx)


async def test_accepted_zero_zero_bookkeeping_does_not_block_valid_anchors(
    tmp_path,
):
    store, ctx, tool = _env(tmp_path)
    try:
        store.append_event(
            "alice",
            "user_input",
            {"content": "BOOKKEEPING_ROOT_QUERY"},
            turn_index=1,
            branch_id=1,
            parent_branch_path=[],
        )
        store.append_event(
            "alice",
            "processing_end",
            {},
            turn_index=0,
            branch_id=0,
        )
        query = await tool.execute(
            {"query": "BOOKKEEPING_ROOT_QUERY", "mode": "fts"}, context=ctx
        )
        assert query.error is None
        token = _token(query.output)
        window = await tool.execute(
            {"anchor": token, "before": 0, "after": 0}, context=ctx
        )
        assert window.error is None
        assert "BOOKKEEPING_ROOT_QUERY" in window.output
    finally:
        _close(store, ctx)


async def test_pending_index_excludes_hits_but_not_verified_physical_anchor(tmp_path):
    store, ctx, tool = _env(tmp_path)
    try:
        store.append_event(
            "alice",
            "user_input",
            {"content": "PENDING_INDEX_SOURCE"},
            turn_index=1,
            branch_id=1,
            parent_branch_path=[],
        )
        first = await tool.execute(
            {"query": "PENDING_INDEX_SOURCE", "mode": "fts"}, context=ctx
        )
        assert first.error is None and _token(first.output)
        live = ctx.session._memory
        hits = live._memory.search("PENDING_INDEX_SOURCE", mode="fts")
        live._memory._state["alice:cursor:keywords:pending"] = True
        assert not live._memory.search("PENDING_INDEX_SOURCE", mode="fts")
        hits = live._navigator.register_hits(hits, "PENDING_INDEX_SOURCE")
        args = {"anchor": hits[0].anchor, "before": 0, "after": 0}
        selected = await tool.execute(args, context=ctx)
        assert selected.error is None
        assert "PENDING_INDEX_SOURCE" not in selected.output
        raw = await tool.execute({**args, "scope": "raw"}, context=ctx)
        assert raw.error is None and "PENDING_INDEX_SOURCE" in raw.output
    finally:
        _close(store, ctx)


async def test_zero_neighbor_window_survives_unverifiable_further_continuity(
    tmp_path,
):
    """An unknowable has_more flag must not invalidate requested anchor content."""
    store, ctx, tool = _env(tmp_path)
    try:
        store.append_event(
            "alice",
            "user_input",
            {"content": "VISIBLE_AT_START_OF_SIBLINGS"},
            turn_index=1,
            branch_id=2,
            parent_branch_path=[],
        )
        for number in range(270):
            store.append_event(
                "alice",
                "user_input",
                {"content": f"INVISIBLE_SIBLING_{number}"},
                turn_index=1,
                branch_id=1,
                parent_branch_path=[],
            )

        search = await tool.execute(
            {"query": "VISIBLE_AT_START_OF_SIBLINGS", "mode": "fts"}, context=ctx
        )
        assert search.error is None, search.error
        token = _token(search.output)
        exact = await tool.execute(
            {"anchor": token, "before": 0, "after": 0}, context=ctx
        )
        assert exact.error is None, exact.error
        assert "VISIBLE_AT_START_OF_SIBLINGS" in exact.output
        assert "INVISIBLE_SIBLING" not in exact.output
        assert "has_before=false" in exact.output
        assert "has_after=unknown" in exact.output

        # Budget exhaustion does not invalidate verified events.
        neighbor = await tool.execute(
            {"anchor": token, "before": 0, "after": 1}, context=ctx
        )
        assert neighbor.error is None
        assert "VISIBLE_AT_START_OF_SIBLINGS" in neighbor.output
        assert "INVISIBLE_SIBLING" not in neighbor.output
        assert "has_after=unknown" in neighbor.output
    finally:
        _close(store, ctx)


async def test_old_all_branch_cursor_allows_raw_physical_navigation(tmp_path):
    store, ctx, tool = _env(tmp_path)
    try:
        store.append_event(
            "alice",
            "user_input",
            {"content": "ALL_BRANCH_INDEX_MISMATCH"},
            turn_index=1,
            branch_id=1,
            parent_branch_path=[],
        )
        first = await tool.execute(
            {"query": "ALL_BRANCH_INDEX_MISMATCH", "mode": "fts"}, context=ctx
        )
        assert first.error is None and _token(first.output)
        live = ctx.session._memory
        cursor = live._memory._state["alice:cursor:keywords"]
        live._memory._state["alice:cursor:keywords"] = {
            **cursor,
            "all_branches": True,
        }
        result = await tool.execute(
            {"query": "ALL_BRANCH_INDEX_MISMATCH", "mode": "fts"},
            context=ctx,
        )
        assert result.error is None
        token = _token(result.output)
        args = {"anchor": token, "before": 0, "after": 0}
        selected = await tool.execute(args, context=ctx)
        assert selected.error is None and "has_after=unknown" in selected.output
        assert "ALL_BRANCH_INDEX_MISMATCH" not in selected.output
        raw = await tool.execute({**args, "scope": "raw"}, context=ctx)
        assert raw.error is None and "ALL_BRANCH_INDEX_MISMATCH" in raw.output
    finally:
        _close(store, ctx)


async def test_legacy_inferred_ancestry_uses_mainline_selection(tmp_path):
    store, ctx, tool = _env(tmp_path)
    try:
        for turn in (1, 2):
            store.append_event(
                "alice",
                "user_input",
                {"content": f"LEGACY_TURN_{turn}"},
                turn_index=turn,
                branch_id=1,
            )
        result = await tool.execute(
            {"query": "LEGACY_TURN_2", "mode": "fts"}, context=ctx
        )
        assert result.error is None
        window = await tool.execute(
            {"anchor": _token(result.output), "before": 1, "after": 0},
            context=ctx,
        )
        assert window.error is None
        assert "LEGACY_TURN_1" in window.output
        assert "LEGACY_TURN_2" in window.output
    finally:
        _close(store, ctx)


@pytest.mark.parametrize("regenerate", [False, True])
async def test_late_legacy_result_does_not_require_another_search(
    tmp_path, monkeypatch, regenerate
):
    store, ctx, tool = _env(tmp_path)
    try:
        store.append_event(
            "alice",
            "user_input",
            {"content": "FIRST_TURN"},
            turn_index=1,
            branch_id=1,
        )
        store.append_event(
            "alice",
            "user_input",
            {"content": "SECOND_TURN"},
            turn_index=2,
            branch_id=1,
        )
        store.append_event(
            "alice",
            "tool_call",
            {"name": "bash", "args": {"command": "LEGACY_CALL"}},
            turn_index=2,
            branch_id=1,
        )
        store.append_event("alice", "processing_end", {}, turn_index=2, branch_id=1)
        result = await tool.execute(
            {"query": "LEGACY_CALL", "mode": "fts"}, context=ctx
        )
        token = _token(result.output)
        if regenerate:
            store.append_event(
                "alice",
                "user_input",
                {"content": "SIBLING_ROOT"},
                turn_index=1,
                branch_id=2,
                parent_branch_path=[],
            )
        store.append_event(
            "alice",
            "tool_result",
            {"name": "bash", "output": "LATE_LEGACY_OUTPUT"},
            turn_index=2,
            branch_id=1,
        )
        for _ in range(navigation.MAX_LEGACY_PREFIX + 1):
            store.append_event("alice", "processing_start", {})
        sizes = []
        infer = navigation.index_parent_paths

        def observe(rows):
            sizes.append(len(rows))
            return infer(rows)

        monkeypatch.setattr(navigation, "index_parent_paths", observe)
        window = await tool.execute(
            {"anchor": token, "before": 0, "after": 1}, context=ctx
        )
        assert sizes == [navigation.MAX_LEGACY_PREFIX]
        assert window.error is None
        assert ("LATE_LEGACY_OUTPUT" in window.output) is not regenerate
        assert "SIBLING_ROOT" not in window.output
        raw = await tool.execute(
            {"anchor": token, "before": 0, "after": 3, "scope": "raw"}, context=ctx
        )
        assert raw.error is None and "LATE_LEGACY_OUTPUT" in raw.output
        assert sizes == [navigation.MAX_LEGACY_PREFIX]
    finally:
        _close(store, ctx)


@pytest.mark.parametrize("explicit_focus", [False, True])
async def test_deep_legacy_anchor_has_bounded_selected_and_raw_context(
    tmp_path, monkeypatch, explicit_focus
):
    store, ctx, tool = _env(tmp_path)
    try:
        store.append_event(
            "alice",
            "user_input",
            {"content": "FIRST_TURN"},
            turn_index=1,
            branch_id=1,
            parent_branch_path=[],
        )
        store._event_seq["alice"] = 500
        store.append_event(
            "alice",
            "user_input",
            {"content": "DEEP_LEGACY_FOCUS"},
            turn_index=2,
            branch_id=1,
            parent_branch_path=[(1, 1)] if explicit_focus else None,
        )
        store.append_event(
            "alice",
            "text",
            {"content": "DEEP_LEGACY_NEIGHBOR"},
            turn_index=2,
            branch_id=1,
        )
        result = await tool.execute(
            {"query": "DEEP_LEGACY_FOCUS", "mode": "fts"}, context=ctx
        )
        token = _token(result.output)

        def forbid(*args, **kwargs):
            raise AssertionError("Context must not scan the complete event history")

        monkeypatch.setattr(store, "_event_rows", forbid)
        args = {"anchor": token, "before": 0, "after": 1}
        selected = await tool.execute(args, context=ctx)
        assert selected.error is None
        assert "DEEP_LEGACY_NEIGHBOR" not in selected.output
        assert ("DEEP_LEGACY_FOCUS" in selected.output) is explicit_focus
        assert "has_after=unknown" in selected.output
        assert 'scope="raw"' in selected.output
        raw = await tool.execute({**args, "scope": "raw"}, context=ctx)
        assert raw.error is None
        assert "DEEP_LEGACY_FOCUS" in raw.output
        assert "DEEP_LEGACY_NEIGHBOR" in raw.output
        assert "turn=2 branch=1" in raw.output
    finally:
        _close(store, ctx)
