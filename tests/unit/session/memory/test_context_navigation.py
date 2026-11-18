"""Creature-facing bidirectional navigation over verified session events."""

import re
from pathlib import Path
from types import SimpleNamespace

import pytest

import kohakuterrarium.builtins.tools.search_memory as search_mod
from kohakuterrarium.builtins.tools.search_memory import SearchMemoryTool
from kohakuterrarium.core.session import Session
from kohakuterrarium.llm.tools.schemas import _BUILTIN_SCHEMAS
from kohakuterrarium.modules.tool.base import ToolContext
from kohakuterrarium.session.store import SessionStore


def _context(store, name="alice"):
    agent = SimpleNamespace(
        session_store=store,
        config=SimpleNamespace(name=name, memory=None),
        _turn_index=2,
        _branch_id=1,
        _parent_branch_path=((1, 1),),
    )
    return ToolContext(
        agent_name=name,
        agent=agent,
        session=Session(key="s"),
        working_dir=Path(store.path).parent,
    )


def _append(store, turn, event_type, data, branch=1, parent=None):
    store.append_event(
        "alice",
        event_type,
        data,
        turn_index=turn,
        branch_id=branch,
        parent_branch_path=(
            parent if parent is not None else ([[1, 1]] if turn == 2 else [])
        ),
    )


def _seed_two_turns(store):
    store.init_meta("s", "agent", "", str(Path(store.path).parent), ["alice"])
    for turn, user in ((1, "original request"), (2, "follow-up request")):
        _append(store, turn, "user_input", {"content": user})
        _append(store, turn, "tool_call", {"name": "bash", "args": {"command": "pwd"}})
        _append(
            store,
            turn,
            "tool_result",
            {
                "name": "bash",
                "error": "traceback-needle" if turn == 1 else "",
                "output": "command result",
            },
        )
        _append(store, turn, "text", {"content": f"round {turn} finished"})
        _append(store, turn, "processing_end", {})
    store.state["alice:branch_view"] = {"1": 1, "2": 1}


def _anchor(output):
    match = re.search(r"^anchor: ([A-Za-z0-9_-]+)$", output, re.M)
    assert match, output
    return match.group(1)


@pytest.mark.asyncio
async def test_find_expand_cross_turn_and_chain(tmp_path):
    store = SessionStore(tmp_path / "context.kohakutr")
    _seed_two_turns(store)
    tool, ctx = SearchMemoryTool(), _context(store)
    try:
        result = await tool.execute(
            {"query": "traceback-needle", "mode": "fts"}, context=ctx
        )
        assert result.error is None
        token = _anchor(result.output)
        expanded = await tool.execute(
            {"anchor": token, "before": 2, "after": 5}, context=ctx
        )
        assert expanded.error is None
        assert "original request" in expanded.output
        assert "[match] tool_result" in expanded.output
        assert "follow-up request" in expanded.output
        assert "has_before=false has_after=false" in expanded.output
        expanded_lines = expanded.output.splitlines()
        after_text = next(
            i
            for i, line in enumerate(expanded_lines)
            if line.startswith("[after] text")
        )
        chained = expanded_lines[after_text + 1].split(": ", 1)[1]
        later = await tool.execute(
            {"anchor": chained, "before": 0, "after": 3}, context=ctx
        )
        assert later.error is None
        assert "has_before=true" in later.output
        assert "follow-up request" in later.output
        assert "traceback-needle" not in later.output
    finally:
        store.close(update_status=False)


@pytest.mark.asyncio
async def test_anchor_retains_original_branch_when_selection_switches(tmp_path):
    store = SessionStore(tmp_path / "branches.kohakutr")
    store.init_meta("s", "agent", "", str(tmp_path), ["alice"])
    _append(store, 1, "user_input", {"content": "needle old"}, branch=1)
    _append(store, 1, "text", {"content": "old explanation"}, branch=1)
    _append(store, 1, "processing_end", {}, branch=1)
    _append(store, 1, "user_input", {"content": "needle new"}, branch=2)
    _append(store, 1, "text", {"content": "new explanation"}, branch=2)
    store.state["alice:branch_view"] = {"1": 1}
    ctx, tool = _context(store), SearchMemoryTool()
    try:
        first = await tool.execute({"query": "needle", "mode": "fts"}, context=ctx)
        assert first.error is None
        assert "needle old" in first.output
        assert "needle new" not in first.output
        token = _anchor(first.output)
        memory = await store.run(lambda: store._live_memory._memory)
        generation = await store.run(memory._generation, "alice")
        stats = await store.run(memory.get_stats)
        assert stats == {
            "fts_blocks": 4,
            "vec_blocks": 0,
            "has_vectors": False,
            "dimensions": 0,
        }

        await store.run(store.state.__setitem__, "alice:branch_view", {"1": 2})
        expanded = await tool.execute(
            {"anchor": token, "before": 0, "after": 3}, context=ctx
        )
        assert expanded.error is None
        assert "old explanation" in expanded.output
        assert "new explanation" not in expanded.output
        second = await tool.execute({"query": "needle", "mode": "fts"}, context=ctx)
        assert second.error is None
        assert "needle new" in second.output
        assert "needle old" not in second.output
        assert await store.run(memory._generation, "alice") == generation
        assert await store.run(memory.get_stats) == stats
    finally:
        store.close(update_status=False)


@pytest.mark.asyncio
async def test_invalid_window_and_unknown_or_ambiguous_requests(tmp_path):
    store = SessionStore(tmp_path / "errors.kohakutr")
    _seed_two_turns(store)
    tool, ctx = SearchMemoryTool(), _context(store)
    try:
        assert "exactly one" in (await tool.execute({}, context=ctx)).error
        assert (
            "exactly one"
            in (await tool.execute({"query": "x", "anchor": "x"}, context=ctx)).error
        )
        bad = await tool.execute({"anchor": "not-an-anchor"}, context=ctx)
        assert "INVALID_ANCHOR" in bad.error
        valid = await tool.execute(
            {"query": "traceback-needle", "mode": "fts"}, context=ctx
        )
        token = _anchor(valid.output)
        for args in ({"before": -1}, {"before": 11}, {"after": True}):
            result = await tool.execute({"anchor": token, **args}, context=ctx)
            assert "INVALID_WINDOW" in result.error
        schema = _BUILTIN_SCHEMAS["search_memory"]
        assert not schema["required"]
        assert {"query", "anchor", "before", "after"} <= set(schema["properties"])
    finally:
        store.close(update_status=False)


@pytest.mark.asyncio
async def test_changed_source_expires_anchor_even_with_same_event_id(tmp_path):
    store = SessionStore(tmp_path / "stale.kohakutr")
    _seed_two_turns(store)
    tool, ctx = SearchMemoryTool(), _context(store)
    try:
        found = await tool.execute(
            {"query": "traceback-needle", "mode": "fts"}, context=ctx
        )
        token = _anchor(found.output)
        key = "alice:e000002"
        event = store.events[key]
        store.events[key] = {**event, "error": "different outcome"}
        store.events.flush_cache()
        stale = await tool.execute({"anchor": token}, context=ctx)
        assert "ANCHOR_EXPIRED" in stale.error
    finally:
        store.close(update_status=False)


@pytest.mark.asyncio
async def test_anchor_is_store_local_and_does_not_survive_restart(tmp_path):
    path = tmp_path / "restart.kohakutr"
    old_store = SessionStore(path)
    _seed_two_turns(old_store)
    old = await SearchMemoryTool().execute(
        {"query": "traceback-needle", "mode": "fts"},
        context=_context(old_store),
    )
    token = _anchor(old.output)
    old_store.close(update_status=False)

    restarted = SessionStore(path)
    try:
        other = await SearchMemoryTool().execute(
            {"anchor": token}, context=_context(restarted)
        )
        assert "INVALID_ANCHOR" in other.error
    finally:
        restarted.close(update_status=False)


@pytest.mark.asyncio
async def test_read_does_not_load_embedding_or_decode_full_modern_history(
    tmp_path, monkeypatch
):
    store = SessionStore(tmp_path / "read-only.kohakutr")
    _seed_two_turns(store)
    ctx, tool = _context(store), SearchMemoryTool()
    calls = []

    def forbidden_embedder(_):
        calls.append("embedding")
        raise AssertionError("read should not initialize embeddings")

    monkeypatch.setattr(search_mod, "create_embedder", forbidden_embedder)
    try:
        found = await tool.execute(
            {"query": "traceback-needle", "mode": "fts"}, context=ctx
        )
        assert found.error is None
        token = _anchor(found.output)

        def forbidden_full_history(*args, **kwargs):
            raise AssertionError("context expansion decoded the entire event log")

        monkeypatch.setattr(store, "get_events", forbidden_full_history)
        window = await tool.execute(
            {"anchor": token, "before": 2, "after": 3}, context=ctx
        )
        assert window.error is None
        assert "original request" in window.output
        assert calls == []
    finally:
        store.close(update_status=False)


@pytest.mark.asyncio
async def test_match_focuses_paragraph_and_bounds_large_event(tmp_path):
    store = SessionStore(tmp_path / "paragraph.kohakutr")
    store.init_meta("s", "agent", "", str(tmp_path), ["alice"])
    paragraph = ("A" * 600) + "\n\n" + ("B" * 100) + " needleword " + ("C" * 4000)
    _append(store, 1, "user_input", {"content": "request"})
    _append(store, 1, "text", {"content": paragraph})
    ctx, tool = _context(store), SearchMemoryTool()
    try:
        found = await tool.execute({"query": "needleword", "mode": "fts"}, context=ctx)
        assert found.error is None
        token = _anchor(found.output)
        expanded = await tool.execute(
            {"anchor": token, "before": 1, "after": 0}, context=ctx
        )
        assert expanded.error is None
        assert "[part 1]" in expanded.output
        assert "needleword" in expanded.output
        assert "A" * 200 not in expanded.output
        assert "chars total" in expanded.output
        assert len(expanded.output) < 4000
    finally:
        store.close(update_status=False)


@pytest.mark.asyncio
async def test_compaction_append_does_not_expire_source_anchor(tmp_path):
    store = SessionStore(tmp_path / "compact.kohakutr")
    _seed_two_turns(store)
    ctx, tool = _context(store), SearchMemoryTool()
    try:
        found = await tool.execute(
            {"query": "traceback-needle", "mode": "fts"}, context=ctx
        )
        token = _anchor(found.output)
        _append(store, 2, "compact_complete", {"summary": "compacted"})
        result = await tool.execute(
            {"anchor": token, "before": 0, "after": 8}, context=ctx
        )
        assert result.error is None
        assert "traceback-needle" in result.output
        assert "compacted" in result.output
    finally:
        store.close(update_status=False)


@pytest.mark.asyncio
async def test_legacy_ancestry_excludes_incompatible_siblings(tmp_path):
    store = SessionStore(tmp_path / "legacy-context.kohakutr")
    store.init_meta("s", "agent", "", str(tmp_path), ["alice"])
    # Legacy records have logical turn/branch but no stored parent path.
    for turn, branch, content in (
        (1, 1, "old root"),
        (2, 1, "old child"),
        (1, 2, "new root"),
        (2, 2, "needle new child"),
    ):
        store.append_event(
            "alice",
            "user_input",
            {"content": content},
            turn_index=turn,
            branch_id=branch,
        )
    store.state["alice:branch_view"] = {"1": 2, "2": 2}
    try:
        ctx, tool = _context(store), SearchMemoryTool()
        found = await tool.execute({"query": "needle", "mode": "fts"}, context=ctx)
        assert found.error is None
        expanded = await tool.execute(
            {"anchor": _anchor(found.output), "before": 6, "after": 0},
            context=ctx,
        )
        assert expanded.error is None
        assert "new root" in expanded.output
        assert "old root" not in expanded.output
        assert "old child" not in expanded.output
    finally:
        store.close(update_status=False)


@pytest.mark.asyncio
async def test_duplicate_legacy_ids_navigate_with_physical_refs(tmp_path):
    store = SessionStore(tmp_path / "ambiguous.kohakutr")
    store.init_meta("s", "agent", "", str(tmp_path), ["alice"])
    store.append_event(
        "alice",
        "user_input",
        {"content": "needle root"},
        turn_index=1,
        branch_id=1,
    )
    for suffix in ("first", "second"):
        store.append_event(
            "alice",
            "user_input",
            {"content": f"legacy {suffix}", "event_id": 80},
            turn_index=2,
            branch_id=1,
        )
    store.state["alice:branch_view"] = {"1": 1, "2": 1}
    try:
        ctx, tool = _context(store), SearchMemoryTool()
        found = await tool.execute({"query": "needle", "mode": "fts"}, context=ctx)
        assert found.error is None
        expanded = await tool.execute(
            {"anchor": _anchor(found.output), "before": 0, "after": 2},
            context=ctx,
        )
        assert expanded.error is None
        assert "legacy first" in expanded.output
        assert "legacy second" in expanded.output
    finally:
        store.close(update_status=False)


@pytest.mark.asyncio
async def test_context_scan_limit_never_returns_incomplete_window(
    tmp_path, monkeypatch
):
    from kohakuterrarium.session.memory import context as context_mod

    store = SessionStore(tmp_path / "scan.kohakutr")
    store.init_meta("s", "agent", "", str(tmp_path), ["alice"])
    _append(store, 1, "user_input", {"content": "needle root"})
    for _ in range(4):
        _append(store, 1, "processing_end", {})
    _append(store, 2, "user_input", {"content": "target after"})
    try:
        ctx, tool = _context(store), SearchMemoryTool()
        found = await tool.execute({"query": "needle", "mode": "fts"}, context=ctx)
        token = _anchor(found.output)
        monkeypatch.setattr(context_mod, "MAX_SCANNED_PER_SIDE", 2)
        result = await tool.execute(
            {"anchor": token, "before": 0, "after": 2}, context=ctx
        )
        assert "CONTEXT_SCAN_LIMIT" in result.error
    finally:
        store.close(update_status=False)


@pytest.mark.asyncio
async def test_zero_sized_window_retains_continuity_flags(tmp_path):
    store = SessionStore(tmp_path / "zero.kohakutr")
    _seed_two_turns(store)
    try:
        ctx, tool = _context(store), SearchMemoryTool()
        found = await tool.execute(
            {"query": "traceback-needle", "mode": "fts"}, context=ctx
        )
        expanded = await tool.execute(
            {"anchor": _anchor(found.output), "before": 0, "after": 0},
            context=ctx,
        )
        assert expanded.error is None
        assert "Memory context (1 event" in expanded.output
        assert "has_before=true has_after=true" in expanded.output
    finally:
        store.close(update_status=False)


@pytest.mark.asyncio
async def test_explicit_other_agent_context_keeps_its_own_lineage(tmp_path):
    store = SessionStore(tmp_path / "agents.kohakutr")
    store.init_meta("s", "terrarium", "", str(tmp_path), ["alice", "bob"])
    store.append_event(
        "alice",
        "user_input",
        {"content": "needle-alice"},
        turn_index=1,
        branch_id=1,
        parent_branch_path=[],
    )
    for branch, content in ((1, "needle-bob-old"), (2, "needle-bob-new")):
        store.append_event(
            "bob",
            "user_input",
            {"content": content},
            turn_index=1,
            branch_id=branch,
            parent_branch_path=[],
        )
        store.append_event(
            "bob",
            "text",
            {"content": f"bob branch {branch} answer"},
            turn_index=1,
            branch_id=branch,
            parent_branch_path=[],
        )
    store.state["bob:branch_view"] = {"1": 1}
    try:
        tool, ctx = SearchMemoryTool(), _context(store)
        found = await tool.execute(
            {"query": "needle", "mode": "fts", "agent": "bob"},
            context=ctx,
        )
        assert found.error is None
        assert "needle-bob-old" in found.output
        assert "needle-bob-new" not in found.output
        assert "needle-alice" not in found.output
        read = await tool.execute(
            {"anchor": _anchor(found.output), "before": 2, "after": 5},
            context=ctx,
        )
        assert read.error is None
        assert "bob branch 1 answer" in read.output
        assert "bob branch 2 answer" not in read.output
        assert "needle-bob-new" not in read.output
        assert "needle-alice" not in read.output
    finally:
        store.close(update_status=False)


@pytest.mark.asyncio
async def test_navigation_uses_physical_order_not_event_id_order(tmp_path):
    store = SessionStore(tmp_path / "physical.kohakutr")
    store.init_meta("s", "agent", "", str(tmp_path), ["alice"])
    for kind, content, event_id in (
        ("user_input", "needle beginning", 100),
        ("text", "physically second", 1),
        ("text", "physically third", 50),
    ):
        _append(store, 1, kind, {"content": content, "event_id": event_id})
    try:
        tool, ctx = SearchMemoryTool(), _context(store)
        found = await tool.execute({"query": "needle", "mode": "fts"}, context=ctx)
        read = await tool.execute(
            {"anchor": _anchor(found.output), "before": 0, "after": 2},
            context=ctx,
        )
        assert read.error is None
        assert read.output.index("physically second") < read.output.index(
            "physically third"
        )
    finally:
        store.close(update_status=False)


def test_m3_rebuilds_staged_m2_index_once(tmp_path):
    """A previously deployed all-branch index must gain source fingerprints."""
    from kohakuterrarium.session.memory.blocks import _anchor
    from kohakuterrarium.session.memory.index import SessionMemory

    path = str(tmp_path / "m2-to-m3.kohakutr")
    events = [
        {
            "type": "user_input",
            "content": "needle for navigation",
            "event_id": 42,
            "turn_index": 1,
            "branch_id": 1,
        }
    ]
    old = SessionMemory(path, index_all_branches=True)
    try:
        assert old.index_events("alice", events) == 1
        generation = old._generation("alice")
        # Represent an existing M2 index (version 3), with a valid checkpoint.
        old._state["alice:index_format"] = 3
    finally:
        old.close()

    upgraded = SessionMemory(path, index_all_branches=True)
    try:
        assert upgraded.index_events("alice", events) == 1
        assert upgraded._generation("alice") > generation
        assert upgraded._state["alice:index_format"] == 5
        hits = upgraded.search("needle", mode="fts")
        assert len(hits) == 1
        assert hits[0].source_fingerprint == _anchor(events[0])
        assert upgraded.index_events("alice", events) == 0
        assert upgraded.get_stats()["fts_blocks"] == 1
    finally:
        upgraded.close()


@pytest.mark.asyncio
async def test_long_tool_hit_is_visible_and_offset_is_pageable(tmp_path):
    store = SessionStore(tmp_path / "long-tool.kohakutr")
    store.init_meta("s", "agent", "", str(tmp_path), ["alice"])
    _append(store, 1, "user_input", {"content": "request"})
    payload = "prefix " * 700 + "uniqueerrorneedle" + " suffix" * 800
    _append(store, 1, "tool_result", {"name": "bash", "output": payload})
    tool, ctx = SearchMemoryTool(), _context(store)
    try:
        found = await tool.execute(
            {"query": "uniqueerrorneedle", "mode": "fts"}, context=ctx
        )
        assert found.error is None
        token = _anchor(found.output)
        search_body = found.output.split("anchor: ", 1)[1].split("\n", 1)[1]
        assert "uniqueerrorneedle" in search_body
        assert len(search_body) < 2400

        window = await tool.execute(
            {"anchor": token, "before": 0, "after": 0}, context=ctx
        )
        assert window.error is None
        assert "uniqueerrorneedle" in window.output
        assert "content_slice=" in window.output
        assert len(window.output) < 1500

        beginning = await tool.execute(
            {"anchor": token, "before": 0, "after": 0, "offset": 0},
            context=ctx,
        )
        assert beginning.error is None
        assert "uniqueerrorneedle" not in beginning.output
        assert "next_offset=900" in beginning.output
        next_slice = await tool.execute(
            {"anchor": token, "before": 0, "after": 0, "offset": 900},
            context=ctx,
        )
        assert next_slice.error is None
        assert "content_slice=900:1800/" in next_slice.output

        invalid = await tool.execute(
            {"anchor": token, "offset": len(payload) + 1}, context=ctx
        )
        assert "INVALID_CONTENT_OFFSET" in invalid.error
    finally:
        store.close(update_status=False)


@pytest.mark.asyncio
async def test_injected_inputs_and_zero_turn_bookkeeping_remain_navigable(tmp_path):
    store = SessionStore(tmp_path / "injected.kohakutr")
    store.init_meta("s", "agent", "", str(tmp_path), ["alice"])
    store.append_event(
        "alice",
        "user_input_injected",
        {"content": "pre-turn instruction"},
        turn_index=0,
        branch_id=0,
        parent_branch_path=[],
    )
    _append(store, 1, "user_input", {"content": "original task"})
    store.append_event(
        "alice",
        "user_input_injected",
        {"content": "mid-turn correction"},
        turn_index=1,
        branch_id=1,
        parent_branch_path=[],
    )
    _append(
        store,
        1,
        "tool_result",
        {"name": "bash", "output": "needle output has a problem"},
    )
    tool, ctx = SearchMemoryTool(), _context(store)
    try:
        found = await tool.execute({"query": "needle", "mode": "fts"}, context=ctx)
        assert found.error is None
        expanded = await tool.execute(
            {"anchor": _anchor(found.output), "before": 5, "after": 0},
            context=ctx,
        )
        assert expanded.error is None
        assert "pre-turn instruction" in expanded.output
        assert "mid-turn correction" in expanded.output
    finally:
        store.close(update_status=False)


@pytest.mark.asyncio
async def test_neighbor_text_anchor_keeps_whole_event_offset_range(tmp_path):
    store = SessionStore(tmp_path / "paragraph-neighbor.kohakutr")
    store.init_meta("s", "agent", "", str(tmp_path), ["alice"])
    _append(store, 1, "user_input", {"content": "start"})
    first = "A" * 600
    second = "B" * 1200
    _append(store, 1, "text", {"content": first + "\n\n" + second})
    _append(
        store,
        1,
        "tool_result",
        {"name": "bash", "output": "needle report shows event"},
    )
    tool, ctx = SearchMemoryTool(), _context(store)
    try:
        found = await tool.execute({"query": "needle", "mode": "fts"}, context=ctx)
        assert found.error is None
        window = await tool.execute(
            {"anchor": _anchor(found.output), "before": 1, "after": 0},
            context=ctx,
        )
        assert window.error is None
        lines = window.output.splitlines()
        row = next(
            i for i, line in enumerate(lines) if line.startswith("[before] text")
        )
        neighbor = lines[row + 1].split(": ", 1)[1]
        assert "next_offset=900" in window.output
        page = await tool.execute(
            {"anchor": neighbor, "offset": 900, "before": 0, "after": 0},
            context=ctx,
        )
        assert page.error is None
        assert "B" * 100 in page.output
        assert "content_slice=900:1800/" in page.output
    finally:
        store.close(update_status=False)


@pytest.mark.asyncio
async def test_unstamped_text_uses_opener_ancestry_across_interleaved_root(tmp_path):
    from kohakuterrarium.session.history.branch_paths import (
        index_parent_paths_by_position,
    )
    from kohakuterrarium.session.memory.blocks import extract_batch

    store = SessionStore(tmp_path / "inherited-ancestry.kohakutr")
    store.init_meta("s", "agent", "", str(tmp_path), ["alice"])
    for turn, branch, kind, content, path in [
        (1, 1, "user_input", "root old", []),
        (2, 1, "user_input", "child opener needle", [[1, 1]]),
        (1, 2, "user_input", "root sibling", []),
        (2, 1, "text", "child continuation", None),
    ]:
        store.append_event(
            "alice",
            kind,
            {"content": content},
            turn_index=turn,
            branch_id=branch,
            parent_branch_path=path,
        )
    store.state["alice:branch_view"] = {"1": 1, "2": 1}
    tool, ctx = SearchMemoryTool(), _context(store)
    try:
        events = store.get_memory_events("alice")
        paths = index_parent_paths_by_position(events)
        blocks, _ = extract_batch("alice", events, all_branches=True)
        assert paths[-1] == ((1, 1),)
        assert (
            next(
                b for b in blocks if b.content == "child continuation"
            ).parent_branch_path
            == paths[-1]
        )

        found = await tool.execute(
            {"query": "child opener needle", "mode": "fts"}, context=ctx
        )
        assert found.error is None
        expanded = await tool.execute(
            {"anchor": _anchor(found.output), "before": 0, "after": 3},
            context=ctx,
        )
        assert expanded.error is None
        assert "child continuation" in expanded.output
        assert "root sibling" not in expanded.output
    finally:
        store.close(update_status=False)


@pytest.mark.asyncio
async def test_navigation_accepts_decimal_text_from_bracket_or_xml_tools(tmp_path):
    store = SessionStore(tmp_path / "text-args.kohakutr")
    _seed_two_turns(store)
    tool, ctx = SearchMemoryTool(), _context(store)
    try:
        search = await tool.execute(
            {"query": "traceback-needle", "mode": "fts"}, context=ctx
        )
        assert search.error is None
        token = _anchor(search.output)
        same = await tool.execute(
            {"anchor": token, "before": "2", "after": "3", "offset": "0"},
            context=ctx,
        )
        assert same.error is None
        assert "original request" in same.output
        assert "traceback-needle" in same.output

        for invalid in (
            {"before": True},
            {"before": "2.0"},
            {"before": "-1"},
            {"after": "11"},
            {"offset": "not-a-number"},
        ):
            result = await tool.execute({"anchor": token, **invalid}, context=ctx)
            assert result.error is not None, invalid
            assert (
                "INVALID_WINDOW" in result.error
                or "INVALID_CONTENT_OFFSET" in result.error
            )
    finally:
        store.close(update_status=False)


@pytest.mark.parametrize(
    ("prefix", "query", "needle"),
    [
        ("straße " * 500, "needle", "needle"),
        ("İ" * 1000, "needle", "needle"),
        ("e\u0301" * 1000, "needle", "needle"),
        ("straße " * 500, "STRASSE", "straße"),
    ],
)
def test_focus_preview_offsets_track_original_unicode_text(prefix, query, needle):
    from kohakuterrarium.session.memory.preview import preview_text

    text = prefix + "needle" + "z" * 5000
    excerpt = preview_text(text, 900, focus=query)
    original = text.casefold().find(needle.casefold())
    assert original >= 0
    if query == "needle":
        assert excerpt.start <= text.index("needle") < excerpt.end
        assert "needle" in excerpt.content
    else:
        assert needle in excerpt.content.casefold() or needle in excerpt.content
        assert excerpt.start <= 0 < excerpt.end

    assert excerpt.total == len(text)
    assert excerpt.end - excerpt.start <= 900
    next_page = preview_text(text, 900, offset=excerpt.end)
    assert next_page.start == excerpt.end
