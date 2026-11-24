"""Direct physical provenance in normal FTS/vector indexing and navigation."""

import re
from types import SimpleNamespace

import numpy as np

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


async def test_direct_window_fails_explicitly_on_excessive_sequence_gap(
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
        assert "CONTEXT_SCAN_LIMIT" in (result.error or "")
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
        assert "ANCHOR_MIGRATION_REQUIRED" in result.output
        assert not re.search(r"^anchor:", result.output, re.M)
        # Ordinary search must not silently perform experimental backfill.
        assert "source_ref" not in ctx.session._memory._memory._fts.get_by_id(1)[1]
    finally:
        _close(store, ctx)


async def test_explicit_legacy_metadata_backfill_enables_direct_path(
    tmp_path, monkeypatch
):
    from kohakuterrarium.session.memory.migration import backfill_legacy_sources

    store, ctx, tool = _env(tmp_path)
    try:
        store.append_event("alice", "user_input", {"content": "UPGRADED_OLD_INDEX"})
        legacy = SessionMemory(str(store.path))
        try:
            legacy.index_events("alice", store.get_events("alice"))
            outcome = backfill_legacy_sources(store, legacy, "alice")
            assert outcome.fts_patched == 1
        finally:
            legacy.close()

        def forbid(*args, **kwargs):
            raise AssertionError("Backfilled direct path reread full get_events")

        monkeypatch.setattr(store, "get_events", forbid)
        result = await tool.execute(
            {"query": "UPGRADED_OLD_INDEX", "mode": "fts"}, context=ctx
        )
        assert result.error is None, result.error
        token = _token(result.output)
        context = await tool.execute(
            {"anchor": token, "before": 0, "after": 0}, context=ctx
        )
        assert context.error is None
        assert "UPGRADED_OLD_INDEX" in context.output
    finally:
        _close(store, ctx)


async def test_anchor_withheld_when_saved_branch_differs_from_legacy_index(tmp_path):
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
        # A previously indexed latest-branch hit remains a normal FTS hit,
        # but must not authorize expanding its foreign physical context.
        assert "LATEST_INDEX_SELECTION" in result.output
        assert "UNVERIFIABLE_SOURCE" in result.output
        assert not re.search(r"^anchor:", result.output, re.M)
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


async def test_direct_anchor_not_issued_from_incomplete_keyword_checkpoint(tmp_path):
    from kohakuterrarium.session.memory.navigation import (
        MemoryNavigationError,
    )

    store, ctx, tool = _env(tmp_path)
    try:
        store.append_event("alice", "user_input", {"content": "PENDING_INDEX_SOURCE"})
        first = await tool.execute(
            {"query": "PENDING_INDEX_SOURCE", "mode": "fts"}, context=ctx
        )
        assert first.error is None and _token(first.output)
        live = ctx.session._memory
        hits = live._memory.search("PENDING_INDEX_SOURCE", mode="fts")
        live._memory._state["alice:cursor:keywords:pending"] = True
        with __import__("pytest").raises(MemoryNavigationError, match="incomplete"):
            live._navigator.register_hits(hits, "PENDING_INDEX_SOURCE")
    finally:
        _close(store, ctx)


async def test_partial_legacy_backfill_keeps_individually_safe_search_anchors(
    tmp_path, monkeypatch
):
    import pytest

    from kohakuterrarium.session.memory.migration import (
        backfill_legacy_sources,
    )

    store, ctx, tool = _env(tmp_path)
    old = None
    try:
        for content in ("PARTIAL_CERTIFIED_ONE", "PARTIAL_LEGACY_TWO"):
            store.append_event("alice", "user_input", {"content": content})
        old = SessionMemory(str(store.path))
        old.index_events("alice", store.get_events("alice"))
        fts_type = type(old._fts)
        original = fts_type.update
        count = 0

        def fail_second(self, *args, **kwargs):
            nonlocal count
            count += 1
            if count == 2:
                raise RuntimeError("synthetic interrupted backfill")
            return original(self, *args, **kwargs)

        monkeypatch.setattr(fts_type, "update", fail_second)
        with pytest.raises(RuntimeError, match="interrupted backfill"):
            backfill_legacy_sources(store, old, "alice")
        assert "source_ref" in old._fts.get_by_id(1)[1]
        assert "source_ref" not in old._fts.get_by_id(2)[1]
        monkeypatch.setattr(fts_type, "update", original)
        old.close()
        old = None

        # A partially migrated index must not pretend it is globally ready.
        # Independently certified rows may have anchors; missing ones still
        # return the original Search result but explicitly need migration.
        for content, certified in (
            ("PARTIAL_CERTIFIED_ONE", True),
            ("PARTIAL_LEGACY_TWO", False),
        ):
            searched = await tool.execute(
                {"query": content, "mode": "fts"}, context=ctx
            )
            assert searched.error is None and content in searched.output
            if certified:
                token = _token(searched.output)
                expanded = await tool.execute(
                    {"anchor": token, "before": 0, "after": 0},
                    context=ctx,
                )
                assert expanded.error is None and content in expanded.output
            else:
                assert "ANCHOR_MIGRATION_REQUIRED" in searched.output
                assert not re.search(r"^anchor:", searched.output, re.M)
    finally:
        if old is not None:
            old.close()
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

        # If an actual later neighbor was requested, the limit is a real
        # correctness boundary; do not return an incomplete normal window.
        neighbor = await tool.execute(
            {"anchor": token, "before": 0, "after": 1}, context=ctx
        )
        assert "CONTEXT_SCAN_LIMIT" in (neighbor.error or "")
    finally:
        _close(store, ctx)
