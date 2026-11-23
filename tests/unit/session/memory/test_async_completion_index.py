"""Late tool completion is an indexed source event, not an open-round message."""

import re
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

import pytest

from kohakuterrarium.builtins.tools.search_memory import SearchMemoryTool
from kohakuterrarium.core.session import Session
from kohakuterrarium.modules.tool.base import ToolContext
from kohakuterrarium.session.memory.blocks import _anchor, extract_batch
from kohakuterrarium.session.memory.index import SessionMemory
from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.session.store.lifecycle.version import FORMAT_VERSION


def _event(event_id, kind, *, branch=1, parent=None, **data):
    row = {
        "event_id": event_id,
        "type": kind,
        "turn_index": 1,
        "branch_id": branch,
    }
    if parent is not None:
        row["parent_branch_path"] = parent
    return {**row, **data}


def _late_history():
    return [
        _event(1, "user_input", content="start slow bash", parent=[]),
        _event(
            2,
            "tool_call",
            name="bash",
            call_id="slow-job-1",
            args={"command": "launch benchmark"},
            parent=[],
        ),
        _event(3, "processing_end", parent=[]),
        _event(4, "text", content="not a new round", parent=[]),
        _event(
            5,
            "tool_result",
            name="bash",
            call_id="slow-job-1",
            output="COMPLETED_BG_MARKER_19.50610779999988",
            parent=[],
        ),
        _event(6, "user_input", branch=2, content="sibling", parent=[]),
        _event(7, "processing_end", branch=2, parent=[]),
        _event(
            8,
            "tool_result",
            branch=2,
            name="bash",
            output="SIBLING_BG_MARKER_7.1001",
            parent=[],
        ),
    ]


@pytest.mark.parametrize("background", [False, True])
def test_identical_bash_output_searches_before_or_after_processing_end(
    tmp_path, background
):
    value = "SAME_BASH_RESULT_19.50610779999988"
    launch = [
        _event(1, "user_input", content="benchmark", parent=[]),
        _event(
            2,
            "tool_call",
            name="bash",
            call_id="same-bash",
            args={"command": "run benchmark without result in args"},
            parent=[],
        ),
    ]
    completion = _event(
        3,
        "tool_result",
        name="bash",
        call_id="same-bash",
        output=f"benchmark output {value}",
        parent=[],
    )
    end = _event(4, "processing_end", parent=[])
    events = [*launch, *([end, completion] if background else [completion, end])]
    with closing(
        SessionMemory(
            str(tmp_path / f"completion-{background}.kohakutr"),
            index_all_branches=True,
        )
    ) as memory:
        memory.index_events("alice", events)
        result = memory.search(value, mode="fts", branch_view={1: 1}, k=3)
        assert len(result) == 1
        assert result[0].tool_name == "bash"
        assert value in result[0].content


@pytest.mark.parametrize("step", [1, 2, 3, 5, 8])
def test_background_completion_full_incremental_parity_with_branch_scope(step):
    rows = _late_history()
    complete, _ = extract_batch("alice", rows, all_branches=True)
    cursor, appended = None, []
    for stop in range(step, len(rows) + step, step):
        batch, cursor = extract_batch("alice", rows[:stop], cursor, all_branches=True)
        appended.extend(batch)
    assert appended == complete
    assert not any("not a new round" in block.content for block in complete)
    late = [block for block in complete if "COMPLETED_BG_MARKER" in block.content]
    assert len(late) == 1
    assert late[0].block_type == "tool"
    assert late[0].tool_name == "bash"
    assert (late[0].turn_index, late[0].branch_id) == (1, 1)
    assert late[0].parent_branch_path == ()
    assert cursor["open_branches"] == {}


def test_unstamped_or_invalid_orphan_cannot_invent_a_visible_round():
    prefix = [
        _event(1, "user_input", content="start", parent=[]),
        _event(2, "processing_end", parent=[]),
    ]
    suffix = [
        _event(3, "tool_result", name="bash", output="UNSTAMPED_ORPHAN"),
        _event(4, "tool_result", name="bash", output="WRONG_PATH", parent=[[1, 1]]),
        {
            "type": "tool_result",
            "event_id": 5,
            "turn_index": True,
            "branch_id": 1,
            "parent_branch_path": [],
            "name": "bash",
            "output": "INVALID_COORDS",
        },
        {
            "type": "tool_result",
            "event_id": 6,
            "name": "bash",
            "output": "LEGACY_ORPHAN",
        },
    ]
    full, _ = extract_batch("alice", prefix + suffix, all_branches=True)
    assert [block.content for block in full] == ["start"]


def test_late_completion_on_sibling_branch_is_not_exposed_elsewhere(tmp_path):
    with closing(
        SessionMemory(str(tmp_path / "late-branches.kohakutr"), index_all_branches=True)
    ) as memory:
        memory.index_events("alice", _late_history())
        old = memory.search("BG_MARKER", mode="fts", branch_view={1: 1})
        sibling = memory.search("BG_MARKER", mode="fts", branch_view={1: 2})
        assert len(old) == len(sibling) == 1
        assert "COMPLETED_BG_MARKER" in old[0].content
        assert "SIBLING_BG_MARKER" in sibling[0].content
        assert old[0].event_position != sibling[0].event_position


def _store_context(store, tmp_path):
    agent = SimpleNamespace(
        session_store=store,
        config=SimpleNamespace(name="alice", memory=None),
        _turn_index=1,
        _branch_id=1,
        _parent_branch_path=[],
    )
    return ToolContext(
        agent_name="alice",
        agent=agent,
        session=Session(key="s"),
        working_dir=tmp_path,
    )


def _append(store, kind, data):
    store.append_event(
        "alice",
        kind,
        data,
        turn_index=1,
        branch_id=1,
        parent_branch_path=[],
    )


@pytest.mark.asyncio
async def test_real_live_search_indexes_late_background_result_on_append(tmp_path):
    store = SessionStore(Path(tmp_path) / "live-late.kohakutr")
    store.init_meta("s", "agent", "", str(tmp_path), ["alice"])
    ctx = _store_context(store, tmp_path)
    tool = SearchMemoryTool()
    try:
        _append(store, "user_input", {"content": "launch background bash"})
        _append(
            store,
            "tool_call",
            {
                "name": "bash",
                "call_id": "bg-1",
                "args": {
                    "command": "run hidden-output benchmark",
                    "run_in_background": True,
                },
            },
        )
        _append(store, "processing_end", {})
        before = await tool.execute(
            {"query": "19.50610779999988", "mode": "fts"},
            context=ctx,
        )
        assert before.error is None
        assert before.output == "No results found."
        memory = await store.run(lambda: store._live_memory._memory)
        previous = await store.run(memory.get_stats)

        _append(
            store,
            "tool_result",
            {
                "name": "bash",
                "call_id": "bg-1",
                "output": "benchmark results "
                + "A" * 1800
                + " 19.50610779999988 tokens/s LATE_BG_UNIQUE",
            },
        )
        after = await tool.execute(
            {"query": "19.50610779999988", "mode": "fts"},
            context=ctx,
        )
        assert after.error is None
        assert "[round 1] tool:bash" in after.output
        assert "19.50610779999988" in after.output
        assert "LATE_BG_UNIQUE" in after.output
        token = re.search(r"^anchor: ([A-Za-z0-9_-]+)$", after.output, re.M)
        assert token
        context = await tool.execute(
            {
                "anchor": token.group(1),
                "before": 0,
                "after": 0,
                "offset": 1800,
            },
            context=ctx,
        )
        assert context.error is None
        assert "[match] tool_result" in context.output
        assert "19.50610779999988" in context.output
        stats = await store.run(memory.get_stats)
        assert stats["fts_blocks"] == previous["fts_blocks"] + 1

        again = await tool.execute(
            {"query": "19.50610779999988", "mode": "fts"},
            context=ctx,
        )
        assert again.error is None
        assert "Found 1 result(s)" in again.output
        assert (await store.run(memory.get_stats))["fts_blocks"] == stats["fts_blocks"]
    finally:
        live = getattr(ctx.session, "_memory", None)
        if live is not None:
            live.close()
        store.close(update_status=False)


@pytest.mark.asyncio
async def test_preexisting_v2_cursor_with_missing_late_result_rebuilds(tmp_path):
    """A READY v2 frontier may certify a skipped completion; bump must recapture."""
    store = SessionStore(Path(tmp_path) / "old-v2.kohakutr")
    store.init_meta("s", "agent", "", str(tmp_path), ["alice"])
    ctx = _store_context(store, tmp_path)
    tool = SearchMemoryTool()
    try:
        _append(store, "user_input", {"content": "launch job"})
        _append(
            store,
            "tool_call",
            {"name": "bash", "call_id": "old-bg", "args": {"command": "run"}},
        )
        _append(store, "processing_end", {})
        before = await tool.execute(
            {"query": "ORIGINAL_OLD_BG_19.50610779999988", "mode": "fts"},
            context=ctx,
        )
        assert before.error is None
        old_memory = await store.run(lambda: store._live_memory._memory)
        # The previous code considered this final event scanned, but did not
        # publish its FTS row. Recreate that persisted READY v2 checkpoint.
        _append(
            store,
            "tool_result",
            {
                "name": "bash",
                "call_id": "old-bg",
                "output": "ORIGINAL_OLD_BG_19.50610779999988",
            },
        )
        rows = store.get_memory_events("alice")

        def mark_old():
            state = old_memory._state
            checkpoint = dict(state["alice:cursor:keywords"])
            checkpoint.update(count=len(rows), anchor=_anchor(rows[-1]))
            state["alice:cursor:keywords"] = checkpoint
            state["alice:indexed_keywords"] = len(rows)
            manifest = dict(state["memory:index_manifest"])
            manifest["format"] = 2
            state["memory:index_manifest"] = manifest

        await store.run(mark_old)
        assert (await store.run(old_memory.get_stats))["fts_blocks"] > 0
        store._live_memory.close()
        store._live_memory = None
        ctx.session._memory = None
        assert FORMAT_VERSION == 2  # Original SessionStore was not upgraded.

        found = await tool.execute(
            {"query": "ORIGINAL_OLD_BG_19.50610779999988", "mode": "fts"},
            context=ctx,
        )
        assert found.error is None, found.error
        assert "ORIGINAL_OLD_BG_19.50610779999988" in found.output
        memory = await store.run(lambda: store._live_memory._memory)
        manifest = await store.run(memory._manifest)
        assert manifest["format"] == 3
        assert manifest["fts"] == "ready"
        assert manifest["epoch"] >= 2
    finally:
        live = getattr(ctx.session, "_memory", None)
        if live is not None:
            live.close()
        store.close(update_status=False)
