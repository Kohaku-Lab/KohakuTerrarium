"""Memory identity, lineage, migration, and resource-budget invariants."""

import tracemalloc

import pytest

from kohakuterrarium.session.history import replay
from kohakuterrarium.session.memory import blocks
from kohakuterrarium.session.memory.context import (
    MemoryContextNavigator,
    _Location,
)
from kohakuterrarium.session.memory.index import SessionMemory
from kohakuterrarium.session.store import SessionStore


def _duplicate_legacy_events():
    return [
        {
            "type": "user_input",
            "content": "old-root",
            "event_id": 1,
            "turn_index": 1,
            "branch_id": 1,
        },
        {
            "type": "user_input",
            "content": "old-child-needle",
            "event_id": 20,
            "turn_index": 2,
            "branch_id": 1,
        },
        {
            "type": "user_input",
            "content": "new-root",
            "event_id": 3,
            "turn_index": 1,
            "branch_id": 2,
        },
        {
            "type": "user_input",
            "content": "new-child-needle",
            "event_id": 20,
            "turn_index": 2,
            "branch_id": 2,
        },
    ]


def test_duplicate_logical_ids_preserve_per_event_ancestry(tmp_path):
    rows = _duplicate_legacy_events()
    memory = SessionMemory(str(tmp_path / "branches.kohakutr"), index_all_branches=True)
    try:
        memory.index_events("alice", rows)
        for view, expected in (
            ({1: 1, 2: 1}, {"old-child-needle"}),
            ({1: 2, 2: 2}, {"new-child-needle"}),
            ({1: 2, 2: 1}, set()),
        ):
            result = memory.search("needle", mode="fts", k=4, branch_view=view)
            assert {r.content for r in result} == expected
        # Replay and search must select the same lineage, even if event_id is
        # reused by a mirror. This API returns positions, not colliding IDs.
        mask = replay.select_live_event_positions(rows, branch_view={1: 1, 2: 1})
        assert mask == {0, 1}
    finally:
        memory.close()


def test_source_ref_survives_unreadable_middle_record(tmp_path, monkeypatch):
    from kohakuterrarium.session.store.events import read as reader

    store = SessionStore(tmp_path / "holes.kohakutr")
    store.init_meta("s", "agent", "", str(tmp_path), ["alice"])
    for content in ("first", "unreadable", "tail-needle"):
        store.append_event(
            "alice",
            "user_input",
            {"content": content},
            turn_index=1,
            branch_id=1,
            parent_branch_path=[],
        )
    real = reader.read_values

    def unreadable(table, keys, **kwargs):
        keys = [key for key in keys if "e000001" not in str(key)]
        yield from real(table, keys, **kwargs)

    monkeypatch.setattr(reader, "read_values", unreadable)
    try:
        rows = store.get_memory_events("alice")
        mem = SessionMemory(store.path, index_all_branches=True)
        try:
            mem.index_events("alice", rows)
            results = mem.search("tail-needle", mode="fts", branch_view={1: 1})
            assert len(results) == 1
            assert results[0].source_ref == "alice:e000002"
            navigator = MemoryContextNavigator(store)
            navigator.register_results(results, {"alice": {1: 1}})
            assert navigator.expand(results[0].anchor, 0, 0).events[0].content == (
                "tail-needle"
            )
        finally:
            mem.close()
    finally:
        store.close(update_status=False)


def test_event_fingerprint_is_hashed_once_for_many_paragraphs(monkeypatch):
    original = blocks._anchor
    calls = []

    def counted(event):
        calls.append(event)
        return original(event)

    monkeypatch.setattr(blocks, "_anchor", counted)
    events = [
        {"type": "user_input", "content": "start", "event_id": 1},
        {
            "type": "text",
            "content": "\n\n".join(f"part {i}" for i in range(400)),
            "event_id": 2,
        },
    ]
    indexed, _ = blocks.extract_batch("alice", events, all_branches=True)
    assert len(indexed) == 401
    assert len(calls) <= len(events) + 1


def test_anchor_registry_shares_unchanged_lineage_snapshots():
    store = object()
    navigator = MemoryContextNavigator(store)
    view = {n: 1 for n in range(1, 1001)}
    # Test the registry's standalone lineage intern operation without reading
    # source records; each call represents an independent repeated search.
    tracemalloc.start()
    snapshots = [navigator._freeze_view("alice", view) for _ in range(1000)]
    current, _ = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert all(snapshots[0] is value for value in snapshots)
    assert current < 2 * 1024 * 1024


@pytest.mark.asyncio
async def test_live_search_upgrades_same_version_index_without_source_refs(tmp_path):
    """A direct caller may have indexed old-style row dictionaries at format 5."""
    from types import SimpleNamespace

    from kohakuterrarium.builtins.tools.search_memory import SearchMemoryTool
    from kohakuterrarium.core.session import Session
    from kohakuterrarium.modules.tool.base import ToolContext

    store = SessionStore(tmp_path / "upgrade-sources.kohakutr")
    store.init_meta("s", "agent", "", str(tmp_path), ["alice"])
    store.append_event(
        "alice",
        "user_input",
        {"content": "needle"},
        turn_index=1,
        branch_id=1,
        parent_branch_path=[],
    )
    memory = SessionMemory(store.path, index_all_branches=True)
    try:
        assert memory.index_events("alice", store.get_events("alice")) == 1
        assert memory._state["alice:cursor:keywords"]["source_refs_complete"] is False
    finally:
        memory.close()

    context = ToolContext(
        agent_name="alice",
        session=Session(key="s"),
        working_dir=tmp_path,
        agent=SimpleNamespace(
            session_store=store,
            config=SimpleNamespace(name="alice"),
            _turn_index=1,
            _branch_id=1,
            _parent_branch_path=(),
        ),
    )
    try:
        found = await SearchMemoryTool().execute(
            {"query": "needle", "mode": "fts"}, context=context
        )
        assert found.error is None
        assert "anchor:" in found.output
        live = await store.run(lambda: store._live_memory._memory)
        assert (
            await store.run(
                lambda: live._state["alice:cursor:keywords"]["source_refs_complete"]
            )
        ) is True
    finally:
        store.close(update_status=False)


def test_distinct_lineage_snapshots_evict_by_memory_budget(monkeypatch):
    from kohakuterrarium.session.memory import context as context_mod
    from kohakuterrarium.session.memory.context import MemoryContextError

    monkeypatch.setattr(context_mod, "MAX_VIEW_PAIRS", 150)
    navigator = MemoryContextNavigator(object())
    anchors = []
    for branch in (1, 2, 3):
        view = tuple((turn, branch) for turn in range(1, 61))
        anchors.append(
            navigator._remember(_Location("alice", f"alice:e{branch:06d}", "f", view))
        )
    assert anchors[0] not in navigator._anchors
    assert anchors[1] in navigator._anchors
    assert anchors[2] in navigator._anchors
    assert navigator._view_pairs == 120
    assert len(navigator._view_refs) == 2

    with pytest.raises(MemoryContextError, match="budget"):
        navigator._remember(
            _Location(
                "alice",
                "alice:e000004",
                "f",
                tuple((turn, 1) for turn in range(1, 152)),
            )
        )
    navigator.close()
    assert not navigator._anchors
    assert navigator._view_pairs == 0


def test_branch_view_strict_and_metadata_use_physical_ancestry():
    from kohakuterrarium.session.history.branch_paths import (
        InvalidBranchViewError,
        resolve_branch_view_strict,
    )

    rows = _duplicate_legacy_events()
    assert resolve_branch_view_strict(rows, {1: 1, 2: 1}) == {1: 1, 2: 1}
    assert resolve_branch_view_strict(rows, {1: 2, 2: 2}) == {1: 2, 2: 2}
    with pytest.raises(InvalidBranchViewError):
        resolve_branch_view_strict(rows, {1: 2, 2: 1})

    metadata = replay.project_branch_metadata(rows, branch_view={1: 1, 2: 1})
    child_paths = {
        tuple(tuple(pair) for pair in path)
        for branch in metadata[2]["branches"]
        for path in branch["parent_branch_paths"]
    }
    assert ((1, 1),) in child_paths
    assert ((1, 2),) in child_paths


def test_replay_conversation_does_not_merge_reused_ids_from_siblings():
    rows = [
        {
            "type": "user_message",
            "event_id": 1,
            "content": "old-root",
            "turn_index": 1,
            "branch_id": 1,
        },
        {
            "type": "text",
            "event_id": 2,
            "content": "OLD ANSWER",
            "turn_index": 1,
            "branch_id": 1,
        },
        {
            "type": "user_message",
            "event_id": 20,
            "content": "old-child",
            "turn_index": 2,
            "branch_id": 1,
        },
        {
            "type": "text",
            "event_id": 30,
            "content": "OLD CHILD ANSWER",
            "turn_index": 2,
            "branch_id": 1,
        },
        {
            "type": "user_message",
            "event_id": 3,
            "content": "new-root",
            "turn_index": 1,
            "branch_id": 2,
        },
        {
            "type": "text",
            "event_id": 4,
            "content": "NEW ANSWER",
            "turn_index": 1,
            "branch_id": 2,
        },
        {
            "type": "user_message",
            "event_id": 20,
            "content": "new-child",
            "turn_index": 2,
            "branch_id": 2,
        },
        {
            "type": "text",
            "event_id": 31,
            "content": "NEW CHILD ANSWER",
            "turn_index": 2,
            "branch_id": 2,
        },
    ]
    for view, chosen, excluded in [
        (
            {1: 1, 2: 1},
            ["old-root", "OLD ANSWER", "old-child", "OLD CHILD ANSWER"],
            ["new-root", "NEW ANSWER", "new-child", "NEW CHILD ANSWER"],
        ),
        (
            {1: 2, 2: 2},
            ["new-root", "NEW ANSWER", "new-child", "NEW CHILD ANSWER"],
            ["old-root", "OLD ANSWER", "old-child", "OLD CHILD ANSWER"],
        ),
    ]:
        messages = replay.replay_conversation(rows, branch_view=view)
        contents = [str(m.get("content", "")) for m in messages]
        for text in chosen:
            assert text in contents, (view, contents)
        for text in excluded:
            assert text not in contents, (view, contents)


def test_reused_child_branch_pair_keeps_distinct_ancestor_candidates():
    from kohakuterrarium.session.history.branch_paths import (
        index_parent_paths_by_position,
        resolve_branch_view_strict,
    )
    from kohakuterrarium.session.history.replay import resolve_selected_branches
    from kohakuterrarium.session.memory.visibility import resolve_search_view

    rows = [
        {
            "type": "user_input",
            "event_id": 1,
            "turn_index": 1,
            "branch_id": 1,
            "content": "old root",
            "parent_branch_path": [],
        },
        {
            "type": "user_input",
            "event_id": 2,
            "turn_index": 2,
            "branch_id": 1,
            "content": "old child",
            "parent_branch_path": [[1, 1]],
        },
        {
            "type": "user_input",
            "event_id": 3,
            "turn_index": 1,
            "branch_id": 2,
            "content": "new root",
            "parent_branch_path": [],
        },
        {
            "type": "user_input",
            "event_id": 4,
            "turn_index": 2,
            "branch_id": 1,
            "content": "new child",
            "parent_branch_path": [[1, 2]],
        },
    ]
    paths = index_parent_paths_by_position(rows)
    assert resolve_selected_branches(rows, paths, {1: 2, 2: 1}) == {1: 2, 2: 1}
    assert resolve_search_view(rows, {1: 2, 2: 1}) == {1: 2, 2: 1}
    assert resolve_branch_view_strict(rows, {1: 2, 2: 1}) == {1: 2, 2: 1}
    assert resolve_branch_view_strict(rows, {1: 1, 2: 1}) == {1: 1, 2: 1}


def test_replayed_compaction_rule_survives_duplicate_id_projection():
    base = [
        {
            "event_id": 1,
            "type": "user_message",
            "content": "ROOT QUESTION",
            "turn_index": 1,
            "branch_id": 1,
            "parent_branch_path": [],
        },
        {
            "event_id": 2,
            "type": "text",
            "content": "RAW PREFIX",
            "turn_index": 1,
            "branch_id": 1,
            "parent_branch_path": [],
        },
        {
            "event_id": 30,
            "type": "user_message",
            "content": "old child",
            "turn_index": 2,
            "branch_id": 1,
            "parent_branch_path": [[1, 1]],
        },
        {
            "event_id": 4,
            "type": "compact_complete",
            "summary": "REUSABLE SUMMARY",
            "round": 1,
            "replaced_from_event_id": 1,
            "replaced_to_event_id": 2,
            "compact_path": [[1, 1]],
            "compact_source_path": [[1, 1]],
            "turn_index": 2,
            "branch_id": 1,
            "parent_branch_path": [[1, 1]],
        },
        {
            "event_id": 3,
            "type": "user_message",
            "content": "selected child",
            "turn_index": 2,
            "branch_id": 2,
            "parent_branch_path": [[1, 1]],
        },
        {
            "event_id": 5,
            "type": "text",
            "content": "TAIL ANSWER",
            "turn_index": 2,
            "branch_id": 2,
            "parent_branch_path": [[1, 1]],
        },
    ]
    duplicate = [dict(row) for row in base]
    duplicate[2]["event_id"] = 3
    for events in (base, duplicate):
        messages = replay.replay_conversation(events, branch_view={1: 1, 2: 2})
        texts = [str(item.get("content", "")) for item in messages]
        assert any("REUSABLE SUMMARY" in body for body in texts)
        assert not any("RAW PREFIX" in body for body in texts)
        assert "selected child" in texts
        assert "TAIL ANSWER" in texts


def test_repeated_pair_ancestry_resolution_is_order_independent():
    from kohakuterrarium.session.history.branch_paths import resolve_branch_view_strict

    events = [
        {
            "type": "user_input",
            "event_id": 1,
            "turn_index": 1,
            "branch_id": 1,
            "parent_branch_path": [],
        },
        {
            "type": "user_input",
            "event_id": 2,
            "turn_index": 1,
            "branch_id": 2,
            "parent_branch_path": [],
        },
        {
            "type": "user_input",
            "event_id": 3,
            "turn_index": 2,
            "branch_id": 1,
            "parent_branch_path": [[1, 1]],
        },
        {
            "type": "user_input",
            "event_id": 4,
            "turn_index": 2,
            "branch_id": 1,
            "parent_branch_path": [[1, 2]],
        },
        {
            "type": "user_input",
            "event_id": 5,
            "turn_index": 3,
            "branch_id": 1,
            "parent_branch_path": [[1, 1], [2, 1]],
        },
    ]
    assert resolve_branch_view_strict(events, {3: 1, 2: 1}) == {1: 1, 2: 1, 3: 1}
    assert resolve_branch_view_strict(events, {2: 1, 3: 1}) == {1: 1, 2: 1, 3: 1}


def test_explicit_empty_parent_path_is_not_inferred_from_previous_root():
    from kohakuterrarium.session.history.branch_paths import (
        index_parent_paths_by_position,
    )

    events = [
        {
            "type": "user_input",
            "event_id": 1,
            "turn_index": 1,
            "branch_id": 1,
            "parent_branch_path": [],
        },
        {
            "type": "user_input",
            "event_id": 2,
            "turn_index": 1,
            "branch_id": 2,
            "parent_branch_path": [],
        },
        {
            "type": "user_input",
            "event_id": 3,
            "turn_index": 2,
            "branch_id": 1,
            "parent_branch_path": [],
        },
    ]
    assert index_parent_paths_by_position(events)[2] == ()
