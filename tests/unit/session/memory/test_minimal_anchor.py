"""Isolated feasibility tests for selected-lineage anchors on main's index."""

import re
from types import SimpleNamespace

import pytest

from kohakuterrarium.builtins.tools.search_memory import SearchMemoryTool
from kohakuterrarium.core.session import Session
from kohakuterrarium.modules.tool.base import ToolContext
from kohakuterrarium.session.store import SessionStore


def _ctx(store, name="alice"):
    agent = SimpleNamespace(session_store=store, config=None)
    return ToolContext(
        agent=agent,
        agent_name=name,
        session=Session(key="probe"),
        working_dir=None,
    )


def _close(store, ctx):
    memory = getattr(ctx.session, "_memory", None)
    if memory is not None:
        memory.close()
    store.close(update_status=False)


def _anchors(output):
    return re.findall(r"^anchor: ([A-Za-z0-9_-]+)$", output, re.M)


@pytest.mark.asyncio
async def test_tool_search_anchor_cross_turn_continuation(tmp_path):
    store = SessionStore(tmp_path / "main-anchor.kohakutr")
    store.init_meta("s", "agent", "", str(tmp_path), ["alice"])
    ctx = _ctx(store)
    tool = SearchMemoryTool()
    try:
        store.append_event("alice", "user_input", {"content": "ORIGINAL_REQUEST"})
        store.append_event(
            "alice",
            "tool_call",
            {"name": "bash", "call_id": "job_1", "args": {"command": "echo x"}},
        )
        store.append_event(
            "alice",
            "tool_result",
            {"name": "bash", "call_id": "job_1", "output": "RESULT_NEEDLE_8172 output"},
        )
        store.append_event("alice", "processing_end", {})
        store.append_event("alice", "user_input", {"content": "SECOND_TURN_FOLLOWUP"})
        store.append_event("alice", "text", {"content": "SECOND_TURN_REPLY"})
        query = await tool.execute(
            {"query": "RESULT_NEEDLE_8172", "mode": "fts"}, context=ctx
        )
        assert query.error is None, query.error
        anchors = _anchors(query.output)
        assert len(anchors) == 1, query.output
        window = await tool.execute(
            {"anchor": anchors[0], "before": 3, "after": 3}, context=ctx
        )
        assert window.error is None, window.error
        assert "ORIGINAL_REQUEST" in window.output
        assert "RESULT_NEEDLE_8172" in window.output
        assert "SECOND_TURN_FOLLOWUP" in window.output
        assert "SECOND_TURN_REPLY" in window.output
        assert len(_anchors(window.output)) >= 4

        older = await tool.execute(
            {"anchor": anchors[0], "before": 0, "after": 0}, context=ctx
        )
        assert "has_before=true" in older.output
        assert "has_after=true" in older.output

        invalid = await tool.execute(
            {"anchor": "unknown_cursor", "before": 0, "after": 0}, context=ctx
        )
        assert "INVALID_ANCHOR" in invalid.error
    finally:
        _close(store, ctx)


@pytest.mark.asyncio
async def test_selected_lineage_skips_sibling_content(tmp_path):
    store = SessionStore(tmp_path / "main-branches.kohakutr")
    store.init_meta("s", "agent", "", str(tmp_path), ["alice"])
    ctx = _ctx(store)
    tool = SearchMemoryTool()
    try:
        for branch, secret in ((1, "SECRET_A_NEEDLE"), (2, "VISIBLE_B_NEEDLE")):
            store.append_event(
                "alice",
                "user_input",
                {"content": secret},
                turn_index=1,
                branch_id=branch,
                parent_branch_path=[],
            )
            store.append_event(
                "alice",
                "text",
                {"content": f"ANSWER_BRANCH_{branch}"},
                turn_index=1,
                branch_id=branch,
                parent_branch_path=[],
            )
        a = await tool.execute({"query": "SECRET_A_NEEDLE", "mode": "fts"}, context=ctx)
        assert a.error is None and "No results found" in a.output, a.output
        b = await tool.execute(
            {"query": "VISIBLE_B_NEEDLE", "mode": "fts"}, context=ctx
        )
        assert b.error is None
        tokens = _anchors(b.output)
        assert len(tokens) == 1, b.output
        window = await tool.execute(
            {"anchor": tokens[0], "before": 10, "after": 10}, context=ctx
        )
        assert "VISIBLE_B_NEEDLE" in window.output
        assert "SECRET_A_NEEDLE" not in window.output
        assert "ANSWER_BRANCH_2" in window.output
        assert "ANSWER_BRANCH_1" not in window.output

        raw = await tool.execute(
            {"anchor": tokens[0], "before": 10, "after": 10, "scope": "raw"},
            context=ctx,
        )
        assert raw.error is None
        assert "SECRET_A_NEEDLE" in raw.output
        assert "ANSWER_BRANCH_1" in raw.output
        assert "VISIBLE_B_NEEDLE" in raw.output
        assert raw.output.index("SECRET_A_NEEDLE") < raw.output.index(
            "VISIBLE_B_NEEDLE"
        )
        assert "turn=1 branch=1" in raw.output and "turn=1 branch=2" in raw.output
        sibling_anchor = _anchors(raw.output)[0]
        default = await tool.execute(
            {"anchor": sibling_anchor, "before": 0, "after": 0}, context=ctx
        )
        assert default.error is None and "SECRET_A_NEEDLE" not in default.output
        assert 'scope="raw"' in default.output
        sibling = await tool.execute(
            {"anchor": sibling_anchor, "before": 0, "after": 0, "scope": "raw"},
            context=ctx,
        )
        assert sibling.error is None and "SECRET_A_NEEDLE" in sibling.output
        # Query mode still uses selected-lineage search, not raw retrieval.
        invalid = await tool.execute(
            {"query": "SECRET_A_NEEDLE", "mode": "fts", "scope": "raw"}, context=ctx
        )
        assert invalid.error and "scope" in invalid.error.lower()
    finally:
        _close(store, ctx)


@pytest.mark.asyncio
async def test_large_tool_output_offset_and_explicit_stale_event(tmp_path):
    store = SessionStore(tmp_path / "large.kohakutr")
    store.init_meta("s", "agent", "", str(tmp_path), ["alice"])
    ctx = _ctx(store)
    tool = SearchMemoryTool()
    try:
        payload = "x" * 1800 + " EMBEDDED_LATE_NUMBER_19.50610779999988 " + "z" * 500
        store.append_event("alice", "user_input", {"content": "benchmark"})
        store.append_event("alice", "tool_result", {"name": "bash", "output": payload})
        hit = await tool.execute(
            {"query": "EMBEDDED_LATE_NUMBER", "mode": "fts"}, context=ctx
        )
        assert hit.error is None
        token = _anchors(hit.output)[0]
        preview = await tool.execute(
            {"anchor": token, "before": 0, "after": 0}, context=ctx
        )
        assert preview.error is None
        assert "19.50610779999988" in preview.output
        page = await tool.execute(
            {"anchor": token, "before": 0, "after": 0, "offset": 0},
            context=ctx,
        )
        assert page.error is None
        assert "content_slice=0:900/" in page.output
        malformed = await tool.execute({"anchor": token, "offset": -1}, context=ctx)
        assert "INVALID_OFFSET" in malformed.error

        raw_page = await tool.execute(
            {"anchor": token, "before": 0, "after": 0, "offset": 0, "scope": "raw"},
            context=ctx,
        )
        assert raw_page.error is None
        assert 'offset=900, scope="raw")' in raw_page.output
        bad_scope = await tool.execute({"anchor": token, "scope": "other"}, context=ctx)
        assert "INVALID_SCOPE" in bad_scope.error

        store.events["alice:e000001"] = {
            **store.events["alice:e000001"],
            "output": "TAMPERED_SOURCE",
        }
        stale = await tool.execute(
            {"anchor": token, "before": 0, "after": 0}, context=ctx
        )
        assert "ANCHOR_EXPIRED" in stale.error
        stale_raw = await tool.execute(
            {"anchor": token, "before": 0, "after": 0, "scope": "raw"}, context=ctx
        )
        assert "ANCHOR_EXPIRED" in stale_raw.error
    finally:
        _close(store, ctx)


@pytest.mark.asyncio
async def test_unique_nonmonotonic_ids_retain_physical_anchors(tmp_path):
    store = SessionStore(tmp_path / "mirrors.kohakutr")
    store.init_meta("s", "agent", "", str(tmp_path), ["alice"])
    ctx = _ctx(store)
    tool = SearchMemoryTool()
    try:
        store.append_event(
            "alice", "user_input", {"event_id": 50, "content": "MIRROR_PROBE_50"}
        )
        store.append_event(
            "alice", "text", {"event_id": 10, "content": "UNORDERED_PROBE_10"}
        )
        result = await tool.execute(
            {"query": "MIRROR_PROBE_50", "mode": "fts"}, context=ctx
        )
        assert result.error is None
        assert "MIRROR_PROBE_50" in result.output
        assert _anchors(result.output), result.output
        window = await tool.execute(
            {"anchor": _anchors(result.output)[0], "before": 0, "after": 1},
            context=ctx,
        )
        assert window.error is None
        assert "UNORDERED_PROBE_10" in window.output
    finally:
        _close(store, ctx)


@pytest.mark.parametrize("mode", ["semantic", "hybrid", "auto"])
@pytest.mark.asyncio
async def test_all_existing_search_modes_can_issue_anchor(tmp_path, monkeypatch, mode):
    import numpy as np

    import kohakuterrarium.builtins.tools.search_memory as memory_tool

    class Embedder:
        dimensions = 4

        def encode(self, texts):
            return np.ones((len(texts), 4), dtype=np.float32)

        def encode_one(self, _query):
            return np.ones(4, dtype=np.float32)

    monkeypatch.setattr(memory_tool, "create_embedder", lambda _: Embedder())
    store = SessionStore(tmp_path / f"{mode}.kohakutr")
    store.init_meta("s", "agent", "", str(tmp_path), ["alice"])
    ctx = _ctx(store)
    try:
        store.append_event("alice", "user_input", {"content": "ANCHOR_SEMANTIC_TEST"})
        result = await SearchMemoryTool().execute(
            {"query": "ANCHOR_SEMANTIC_TEST", "mode": mode}, context=ctx
        )
        assert result.error is None, result.error
        tokens = _anchors(result.output)
        assert tokens, result.output
        context = await SearchMemoryTool().execute(
            {"anchor": tokens[0], "before": 0, "after": 0}, context=ctx
        )
        assert context.error is None
        assert "ANCHOR_SEMANTIC_TEST" in context.output
    finally:
        _close(store, ctx)


@pytest.mark.asyncio
async def test_existing_anchor_remains_bound_to_old_branch_after_regeneration(tmp_path):
    store = SessionStore(tmp_path / "regenerate.kohakutr")
    store.init_meta("s", "agent", "", str(tmp_path), ["alice"])
    ctx = _ctx(store)
    tool = SearchMemoryTool()
    try:
        for branch, marker in ((1, "REGENERATED_ORIGINAL"),):
            store.append_event(
                "alice",
                "user_input",
                {"content": marker},
                turn_index=1,
                branch_id=branch,
                parent_branch_path=[],
            )
        first = await tool.execute(
            {"query": "REGENERATED_ORIGINAL", "mode": "fts"}, context=ctx
        )
        assert first.error is None
        old_anchor = _anchors(first.output)[0]

        store.append_event(
            "alice",
            "user_input",
            {"content": "REGENERATED_REPLACEMENT"},
            turn_index=1,
            branch_id=2,
            parent_branch_path=[],
        )
        current = await tool.execute(
            {"query": "REGENERATED_REPLACEMENT", "mode": "fts"}, context=ctx
        )
        assert current.error is None, current.error
        assert len(_anchors(current.output)) == 1, current.output

        original_window = await tool.execute(
            {"anchor": old_anchor, "before": 1, "after": 1}, context=ctx
        )
        assert original_window.error is None, original_window.error
        assert "REGENERATED_ORIGINAL" in original_window.output
        assert "REGENERATED_REPLACEMENT" not in original_window.output

        current_window = await tool.execute(
            {"anchor": _anchors(current.output)[0], "before": 1, "after": 1},
            context=ctx,
        )
        assert current_window.error is None
        assert "REGENERATED_REPLACEMENT" in current_window.output
        assert "REGENERATED_ORIGINAL" not in current_window.output
    finally:
        _close(store, ctx)


@pytest.mark.asyncio
async def test_unambiguous_anchor_does_not_expand_conflicting_scope(
    tmp_path,
):
    store = SessionStore(tmp_path / "reused-scope.kohakutr")
    store.init_meta("s", "agent", "", str(tmp_path), ["alice"])
    ctx = _ctx(store)
    try:
        for turn, branch, ancestry, marker in (
            (1, 1, [], "ROOT_ONE"),
            (2, 1, [(1, 1)], "MIRROR_CHILD_ONE"),
            (1, 2, [], "ROOT_TWO"),
            (2, 1, [(1, 2)], "MIRROR_CHILD_TWO"),
        ):
            store.append_event(
                "alice",
                "user_input",
                {"content": marker},
                turn_index=turn,
                branch_id=branch,
                parent_branch_path=ancestry,
            )
        result = await SearchMemoryTool().execute(
            {"query": "ROOT_TWO", "mode": "fts"}, context=ctx
        )
        assert result.error is None
        assert "ROOT_TWO" in result.output
        anchors = _anchors(result.output)
        assert anchors, result.output
        window = await SearchMemoryTool().execute(
            {"anchor": anchors[0], "before": 3, "after": 3}, context=ctx
        )
        assert window.error is None
        assert "ROOT_TWO" in window.output
        assert "ROOT_ONE" not in window.output
        assert "MIRROR_CHILD_ONE" not in window.output
        assert "MIRROR_CHILD_TWO" not in window.output
    finally:
        _close(store, ctx)


@pytest.mark.asyncio
async def test_late_background_result_is_navigable_even_if_legacy_fts_misses_it(
    tmp_path,
):
    """The anchor feature need not repair the historical index projection."""
    store = SessionStore(tmp_path / "late-result.kohakutr")
    store.init_meta("s", "agent", "", str(tmp_path), ["alice"])
    ctx = _ctx(store)
    tool = SearchMemoryTool()
    try:
        store.append_event(
            "alice", "user_input", {"content": "INDEXED_ORIGIN_FOR_SLOW_JOB"}
        )
        store.append_event(
            "alice",
            "tool_call",
            {"name": "bash", "call_id": "slow_job", "args": {"command": "sleep"}},
        )
        store.append_event("alice", "processing_end", {})
        store.append_event(
            "alice",
            "tool_result",
            {"name": "bash", "call_id": "slow_job", "output": "LATE_BG_RESULT_UNIQUE"},
        )
        direct = await tool.execute(
            {"query": "LATE_BG_RESULT_UNIQUE", "mode": "fts"}, context=ctx
        )
        assert "No results found" in direct.output
        origin = await tool.execute(
            {"query": "INDEXED_ORIGIN_FOR_SLOW_JOB", "mode": "fts"}, context=ctx
        )
        assert origin.error is None
        token = _anchors(origin.output)[0]
        window = await tool.execute(
            {"anchor": token, "before": 0, "after": 5}, context=ctx
        )
        assert window.error is None
        assert "LATE_BG_RESULT_UNIQUE" in window.output
    finally:
        _close(store, ctx)
