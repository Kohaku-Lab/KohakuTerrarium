"""R2 invariants: round handoffs, source coordinates, and safe index migration."""

from contextlib import closing
from pathlib import Path

import pytest

from kohakuterrarium.session.history.semantics import BranchScope, classify_branch_scope
from kohakuterrarium.session.memory.blocks import extract_batch
from kohakuterrarium.session.memory.index import SessionMemory


def _event(n, kind, content=None, *, coords=None, **data):
    event = {"type": kind, "event_id": n}
    if content is not None:
        event["content"] = content
    if coords is not None:
        event["turn_index"], event["branch_id"] = coords
    return {**event, **data}


def _mixed_round():
    return [
        _event(1, "user_input", "needle legacy prompt"),
        _event(2, "text", "needle stamped reply", coords=(1, 1), parent_branch_path=[]),
        _event(
            3,
            "tool_call",
            coords=(1, 1),
            name="bash",
            args={"command": "echo needle"},
            parent_branch_path=[],
        ),
        _event(
            4,
            "tool_result",
            coords=(1, 1),
            name="bash",
            output="needle tool output is long enough",
            parent_branch_path=[],
        ),
        _event(5, "processing_end", coords=(1, 1)),
        _event(6, "text", "orphan after close"),
    ]


def _zero_events():
    samples = [
        (None, "legacy"),
        ((0, 0), "bookkeeping"),
        ((0, 1), "invalid 0-1"),
        ((0, 2), "invalid 0-2"),
        ((1, 0), "invalid 1-0"),
        ((None, 1), "partial missing"),
        (("bad", "bad"), "invalid strings"),
        ((1, 1), "proper"),
    ]
    return [
        _event(
            i,
            "user_input",
            f"needle {label}",
            coords=coords,
            **({"parent_branch_path": []} if coords == (1, 1) else {}),
        )
        for i, (coords, label) in enumerate(samples, start=1)
    ]


def test_legacy_opener_to_stamped_continuation_preserves_round_and_blocks(tmp_path):
    events = _mixed_round()
    blocks, _ = extract_batch("alice", events, all_branches=True)
    selected, _ = extract_batch("alice", events)
    expected = [
        "needle legacy prompt",
        "needle stamped reply",
        "[tool:bash] command=echo needle",
        "[result:bash] needle tool output is long enough",
    ]
    assert [b.content for b in blocks] == [b.content for b in selected] == expected
    assert [(b.round_num, b.block_num) for b in blocks] == [
        (1, 0),
        (1, 1),
        (1, 2),
        (1, 3),
    ]
    assert [(b.turn_index, b.branch_id) for b in blocks] == [
        (None, None),
        (1, 1),
        (1, 1),
        (1, 1),
    ]
    with closing(
        SessionMemory(str(tmp_path / "mixed.kohakutr"), index_all_branches=True)
    ) as memory:
        memory.index_events("alice", events)
        assert {
            h.content for h in memory.search("needle", mode="fts", branch_view={1: 1})
        } == set(expected)


@pytest.mark.parametrize("batch_size", [1, 2, 3, 6])
def test_legacy_opener_incremental_extraction_matches_full(batch_size):
    events = _mixed_round()
    complete, _ = extract_batch("alice", events, all_branches=True)
    cursor, incremental = None, []
    for end in range(batch_size, len(events) + batch_size, batch_size):
        batch, cursor = extract_batch("alice", events[:end], cursor, all_branches=True)
        incremental.extend(batch)
    assert incremental == complete
    assert cursor["in_round"] is False
    assert cursor["open_branches"] == {}


def test_stamped_tail_does_not_inherit_ambiguous_or_closed_round():
    ambiguous = [
        _event(1, "user_input", "legacy prompt"),
        _event(2, "user_input", "other scope", coords=(2, 1), parent_branch_path=[]),
        _event(3, "text", "no matching opener", coords=(1, 1)),
    ]
    blocks, _ = extract_batch("alice", ambiguous, all_branches=True)
    assert [b.content for b in blocks] == ["legacy prompt", "other scope"]

    closed = [
        _event(1, "user_input", "legacy prompt"),
        _event(2, "processing_end"),
        _event(3, "text", "late text", coords=(1, 1)),
    ]
    blocks, _ = extract_batch("alice", closed, all_branches=True)
    assert [b.content for b in blocks] == ["legacy prompt"]


def test_all_branch_index_preserves_raw_coordinates_and_filters_invalid(tmp_path):
    events = _zero_events()
    blocks, _ = extract_batch("alice", events, all_branches=True)
    assert [(b.turn_index, b.branch_id) for b in blocks] == [
        (None, None),
        (0, 0),
        (0, 1),
        (0, 2),
        (1, 0),
        (None, 1),
        ("bad", "bad"),
        (1, 1),
    ]
    selected, _ = extract_batch("alice", events)
    expected = {"needle legacy", "needle bookkeeping", "needle proper"}
    assert {b.content for b in selected} == expected
    with closing(
        SessionMemory(str(tmp_path / "zero.kohakutr"), index_all_branches=True)
    ) as memory:
        memory.index_events("alice", events)
        # FTS metadata retains even invalid raw coordinates for provenance.
        raw = {
            h.content: (h.turn_index, h.branch_id)
            for h in memory.search("needle", mode="fts")
        }
        assert raw["needle invalid 0-1"] == (0, 1)
        assert raw["needle invalid strings"] == ("bad", "bad")
        assert {
            h.content for h in memory.search("needle", mode="fts", branch_view={1: 1})
        } == expected


def test_history_coordinate_classification_preserves_missing_and_invalid():
    assert [
        classify_branch_scope(e.get("turn_index"), e.get("branch_id"))
        for e in _zero_events()
    ] == [
        BranchScope.LEGACY,
        BranchScope.BOOKKEEPING,
        *([BranchScope.INVALID] * 5),
        BranchScope.EXPLICIT,
    ]
    for coords in [(True, 1), (1, False), (1, None)]:
        assert classify_branch_scope(*coords) is BranchScope.INVALID


@pytest.mark.parametrize("branch", [1, 2])
def test_scoped_search_never_reveals_other_or_malformed_zero_branches(branch, tmp_path):
    events = [
        _event(i, "user_input", f"needle {label}", coords=pair)
        for i, (label, pair) in enumerate(
            [
                ("invalid-zero-1", (0, 1)),
                ("invalid-zero-2", (0, 2)),
                ("valid-1", (1, 1)),
                ("valid-2", (1, 2)),
            ],
            start=1,
        )
    ]
    with closing(
        SessionMemory(
            str(tmp_path / f"zero-{branch}.kohakutr"), index_all_branches=True
        )
    ) as memory:
        memory.index_events("alice", events)
        assert {
            h.content
            for h in memory.search("needle", mode="fts", branch_view={1: branch})
        } == {f"needle valid-{branch}"}


def test_legacy_index_is_invalidated_before_recovering_original_coordinates(tmp_path):
    """Legacy selected-lineage rows are never trusted as scoped provenance."""
    events = _zero_events()
    path = str(tmp_path / "legacy-selected.kohakutr")
    with closing(SessionMemory(path)) as old:
        old.index_events("alice", events)
        old._fts.insert(
            "needle old normalized orphan",
            {
                "agent": "alice",
                "round": 1,
                "block": 0,
                "type": "user",
                "turn_index": None,
                "branch_id": None,
            },
        )
        assert old._manifest()["format"] == 1
        assert old._fts.count() >= 2

    with closing(SessionMemory(path, index_all_branches=True)) as upgraded:
        with pytest.raises(ValueError, match="index.*(invalid|building|rebuild)"):
            upgraded.search("needle", mode="fts", branch_view={1: 1})
        upgraded.index_events("alice", events)
        assert upgraded._manifest()["format"] == 2
        assert upgraded.get_stats()["fts_blocks"] == len(events)
        assert {
            h.content for h in upgraded.search("needle", mode="fts", branch_view={1: 1})
        } == {"needle legacy", "needle bookkeeping", "needle proper"}


def test_invalid_coordinates_cannot_open_or_close_a_legacy_round():
    invalid_opener = [
        _event(1, "user_input", "invalid opener", coords=(0, 1)),
        _event(2, "text", "must not inherit invalid round", coords=(1, 1)),
    ]
    blocks, _ = extract_batch("alice", invalid_opener, all_branches=True)
    assert [b.content for b in blocks] == ["invalid opener"]

    invalid_end = [
        _event(1, "user_input", "valid legacy opener"),
        _event(2, "processing_end", coords=(0, 1)),
        _event(3, "text", "still in valid legacy round"),
    ]
    blocks, _ = extract_batch("alice", invalid_end, all_branches=True)
    assert [b.content for b in blocks] == [
        "valid legacy opener",
        "still in valid legacy round",
    ]


def test_completed_stamped_turn_advances_legacy_round_cursor():
    """Legacy fallbacks must not restart at round one after a stamped turn."""
    rows = [
        _event(1, "user_input", "first stamped", coords=(1, 1), parent_branch_path=[]),
        _event(2, "processing_end", coords=(1, 1)),
        _event(3, "user_input", "second legacy"),
        _event(4, "text", "second stamped reply", coords=(2, 1), parent_branch_path=[]),
        _event(
            5, "tool_call", coords=(2, 1), name="bash", args={"command": "echo second"}
        ),
        _event(
            6,
            "tool_result",
            coords=(2, 1),
            name="bash",
            output="second tool output long enough",
        ),
        _event(7, "processing_end", coords=(2, 1)),
    ]
    expected = [
        "first stamped",
        "second legacy",
        "second stamped reply",
        "[tool:bash] command=echo second",
        "[result:bash] second tool output long enough",
    ]
    selected, _ = extract_batch("alice", rows, all_branches=False)
    all_blocks, _ = extract_batch("alice", rows, all_branches=True)
    assert [b.content for b in selected] == expected
    assert [b.content for b in all_blocks] == expected
    assert [b.round_num for b in all_blocks] == [1, 2, 2, 2, 2]
    state, partial = None, []
    for end in range(1, len(rows) + 1):
        batch, state = extract_batch("alice", rows[:end], state, all_branches=True)
        partial.extend(batch)
    assert partial == all_blocks
    assert state["round"] == 2


def test_invalid_event_does_not_poison_index_or_navigation_ancestry(tmp_path):
    from kohakuterrarium.session.memory.context import MemoryContextNavigator
    from kohakuterrarium.session.memory.visibility import resolve_search_view
    from kohakuterrarium.session.store import SessionStore

    store = SessionStore(tmp_path / "ancestry-eligibility.kohakutr")
    try:
        store.init_meta("s", "agent", "", str(tmp_path), ["alice"])
        for kind, content, turn, branch, path in [
            ("user_input", "invalid bool opener", True, 2, None),
            ("user_input", "valid first", 1, 1, []),
            ("user_input", "valid second", 2, 1, None),
            ("text", "needle valid descendant", 2, 1, None),
        ]:
            store.append_event(
                "alice",
                kind,
                {"content": content},
                turn_index=turn,
                branch_id=branch,
                parent_branch_path=path,
            )
        rows = store.get_memory_events("alice")
        view = resolve_search_view(rows)
        assert view == {1: 1, 2: 1}
        with closing(SessionMemory(str(store.path), index_all_branches=True)) as memory:
            memory.index_events("alice", rows)
            hits = memory.search("needle", mode="fts", branch_view=view)
            assert [hit.content for hit in hits] == ["needle valid descendant"]
            assert hits[0].event_position == 3
            assert hits[0].parent_branch_path == ((1, 1),)
        navigator = MemoryContextNavigator(store)
        assert navigator._legacy_paths("alice")[rows[3].source_ref] == ((1, 1),)
        # An invalid coordinate must be rejected before missing ancestry is
        # interpreted as corrupted source history.
        from kohakuterrarium.session.memory.visibility import BranchVisibility

        assert not navigator._visible(
            {"type": "user_input", "turn_index": 2, "branch_id": -1},
            "invalid-source-ref",
            BranchVisibility(view),
            navigator._legacy_paths("alice"),
        )
    finally:
        store.close(update_status=False)


def _mirrored_child_rounds():
    return [
        _event(1, "user_input", "old root", coords=(1, 1), parent_branch_path=[]),
        _event(
            2,
            "user_input",
            "old child",
            coords=(2, 1),
            parent_branch_path=[[1, 1]],
        ),
        _event(3, "user_input", "new root", coords=(1, 2), parent_branch_path=[]),
        _event(
            4,
            "user_input",
            "new child",
            coords=(2, 1),
            parent_branch_path=[[1, 2]],
        ),
        _event(5, "processing_end", coords=(2, 1), parent_branch_path=[[1, 1]]),
        _event(
            6,
            "text",
            "new child needle reply",
            coords=(2, 1),
            parent_branch_path=[[1, 2]],
        ),
        _event(
            7,
            "tool_call",
            coords=(2, 1),
            name="bash",
            args={"command": "echo newer"},
            parent_branch_path=[[1, 2]],
        ),
        _event(8, "processing_end", coords=(2, 1), parent_branch_path=[[1, 2]]),
    ]


@pytest.mark.parametrize("batch_size", [1, 2, 4, 8])
def test_mirrored_round_completion_is_ancestry_scoped(batch_size):
    """An older completion cannot close a newer mirrored (turn, branch)."""
    events = _mirrored_child_rounds()
    full, _ = extract_batch("alice", events, all_branches=True)
    assert [block.content for block in full] == [
        "old root",
        "old child",
        "new root",
        "new child",
        "new child needle reply",
        "[tool:bash] command=echo newer",
    ]
    assert [(block.round_num, block.block_num) for block in full[-2:]] == [
        (2, 1),
        (2, 2),
    ]
    assert [block.parent_branch_path for block in full[-2:]] == [
        ((1, 2),),
        ((1, 2),),
    ]

    cursor, incremental = None, []
    for end in range(batch_size, len(events) + batch_size, batch_size):
        batch, cursor = extract_batch("alice", events[:end], cursor, all_branches=True)
        incremental.extend(batch)
    assert incremental == full
    assert set(cursor["open_branches"]) == {"1:1:[]", "1:2:[]"}


def _persist_rows(store, rows):
    store.init_meta("s", "agent", "", str(Path(store.path).parent), ["alice"])
    for event in rows:
        data = {
            key: val
            for key, val in event.items()
            if key
            not in {
                "type",
                "event_id",
                "turn_index",
                "branch_id",
                "parent_branch_path",
            }
        }
        store.append_event(
            "alice",
            event["type"],
            data,
            turn_index=event.get("turn_index"),
            branch_id=event.get("branch_id"),
            parent_branch_path=event.get("parent_branch_path"),
        )
    return store.get_memory_events("alice")


def test_mirrored_round_search_and_navigation_agree(tmp_path):
    from kohakuterrarium.session.memory.context import MemoryContextNavigator
    from kohakuterrarium.session.memory.visibility import resolve_search_view
    from kohakuterrarium.session.store import SessionStore

    store = SessionStore(tmp_path / "mirrored-rounds.kohakutr")
    try:
        rows = _persist_rows(store, _mirrored_child_rounds())
        selected = {1: 2, 2: 1}
        assert resolve_search_view(rows, selected) == selected
        with closing(SessionMemory(str(store.path), index_all_branches=True)) as memory:
            memory.index_events("alice", rows)
            hits = memory.search("needle", mode="fts", branch_view=selected)
            assert [hit.content for hit in hits] == ["new child needle reply"]
            assert hits[0].parent_branch_path == ((1, 2),)
            assert memory.search("needle", mode="fts", branch_view={1: 1, 2: 1}) == []
            navigator = MemoryContextNavigator(store)
            navigator.register_results(hits, {"alice": selected})
            window = navigator.expand(hits[0].anchor, before=2, after=2)
            contents = [event.content for event in window.events]
            assert "new child" in contents
            assert "new child needle reply" in contents
            assert "old child" not in contents
    finally:
        store.close(update_status=False)


def test_user_message_without_path_continues_existing_scoped_opener(tmp_path):
    """A paired user_message must not reopen the child under a newer root."""
    from kohakuterrarium.session.history.branch_paths import (
        index_eligible_parent_paths_by_position,
    )
    from kohakuterrarium.session.memory.context import MemoryContextNavigator
    from kohakuterrarium.session.memory.visibility import (
        BranchVisibility,
        resolve_search_view,
    )
    from kohakuterrarium.session.store import SessionStore

    events = [
        _event(1, "user_input", "first root", coords=(1, 1), parent_branch_path=[]),
        _event(
            2,
            "user_input",
            "first child",
            coords=(2, 1),
            parent_branch_path=[[1, 1]],
        ),
        _event(3, "user_input", "other root", coords=(1, 2), parent_branch_path=[]),
        _event(4, "user_message", "paired input", coords=(2, 1)),
        _event(5, "text", "needle reply after pair", coords=(2, 1)),
    ]
    paths = index_eligible_parent_paths_by_position(events)
    assert paths[3] == paths[4] == ((1, 1),)
    full, _ = extract_batch("alice", events, all_branches=True)
    assert full[-1].content == "needle reply after pair"
    assert full[-1].parent_branch_path == ((1, 1),)
    cursor, pieces = None, []
    for end in range(1, len(events) + 1):
        part, cursor = extract_batch("alice", events[:end], cursor, all_branches=True)
        pieces.extend(part)
    assert pieces == full

    store = SessionStore(tmp_path / "paired-message.kohakutr")
    try:
        rows = _persist_rows(store, events)
        old, other = {1: 1, 2: 1}, {1: 2, 2: 1}
        assert resolve_search_view(rows, old) == old
        with closing(SessionMemory(str(store.path), index_all_branches=True)) as memory:
            memory.index_events("alice", rows)
            hits = memory.search("needle", mode="fts", branch_view=old)
            assert [hit.content for hit in hits] == ["needle reply after pair"]
            assert memory.search("needle", mode="fts", branch_view=other) == []
            navigator = MemoryContextNavigator(store)
            ancestry = navigator._legacy_paths("alice")
            assert ancestry[rows[3].source_ref] == ((1, 1),)
            assert ancestry[rows[4].source_ref] == ((1, 1),)
            assert navigator._visible(
                rows[4], rows[4].source_ref, BranchVisibility(old), ancestry
            )
            assert not navigator._visible(
                rows[4], rows[4].source_ref, BranchVisibility(other), ancestry
            )
            navigator.register_results(hits, {"alice": old})
            window = navigator.expand(hits[0].anchor, before=2, after=0)
            contents = [event.content for event in window.events]
            assert "paired input" in contents
            assert "other root" not in contents
    finally:
        store.close(update_status=False)


def test_missing_mirrored_completion_cannot_close_latest_child():
    from kohakuterrarium.session.history.branch_paths import (
        index_eligible_parent_paths_by_position,
    )

    events = _mirrored_child_rounds()[:4] + [
        _event(9, "processing_end", coords=(2, 1)),
        _event(10, "text", "new child survives missing end", coords=(2, 1)),
    ]
    paths = index_eligible_parent_paths_by_position(events)
    assert paths[4] is None
    assert paths[5] == ((1, 2),)
    blocks, cursor = extract_batch("alice", events, all_branches=True)
    assert blocks[-1].content == "new child survives missing end"
    assert blocks[-1].parent_branch_path == ((1, 2),)
    assert "2:1:[[1,1]]" in cursor["open_branches"]
    assert "2:1:[[1,2]]" in cursor["open_branches"]


def test_unversioned_index_rebuilds_mirrored_lineage(tmp_path):
    """The pre-feature index had no manifest and cannot authorize v2 reads."""
    rows = _mirrored_child_rounds()
    path = str(tmp_path / "old-mirror-index.kohakutr")
    with closing(SessionMemory(path)) as previous:
        previous.index_events("alice", rows)
        previous._fts.insert(
            "ghost mirrored needle",
            {
                "agent": "alice",
                "round": 2,
                "block": 9,
                "type": "text",
                "turn_index": 2,
                "branch_id": 1,
                "parent_branch_path": [[1, 2]],
            },
        )
        # Mainline's pre-feature memory index had no session-wide manifest.
        # Its physical rows remain until the new v2 builder invalidates them.
        del previous._state["memory:index_manifest"]
        assert previous._manifest() is None

    with closing(SessionMemory(path, index_all_branches=True)) as upgraded:
        with pytest.raises(ValueError, match="(invalid|building|rebuild)"):
            upgraded.search("needle", mode="fts", branch_view={1: 2, 2: 1})
        upgraded.index_events("alice", rows)
        assert upgraded._manifest()["format"] == 2
        assert upgraded.get_stats()["fts_blocks"] == 6
        assert [
            hit.content
            for hit in upgraded.search("needle", mode="fts", branch_view={1: 2, 2: 1})
        ] == ["new child needle reply"]
        assert upgraded.search("ghost", mode="fts") == []


@pytest.mark.parametrize("batch_size", [1, 2, 3, 5])
def test_invalid_coordinates_do_not_poison_dedup_state(batch_size):
    """Memory eligibility precedes both dedup and checkpoint restoration."""
    events = [
        _event(
            1, "user_input", "needle prompt", coords=(True, 1), parent_branch_path=[]
        ),
        _event(2, "user_input", "needle prompt", coords=(1, 1), parent_branch_path=[]),
        _event(3, "text", "needle valid reply", coords=(1, 1), parent_branch_path=[]),
        _event(
            4,
            "tool_call",
            coords=(1, 1),
            parent_branch_path=[],
            name="bash",
            args={"command": "echo valid"},
        ),
        _event(5, "processing_end", coords=(1, 1), parent_branch_path=[]),
    ]
    full, _ = extract_batch("alice", events, all_branches=True)
    assert [(b.content, type(b.turn_index)) for b in full] == [
        ("needle prompt", bool),  # Kept as physical provenance, never visible in scope
        ("needle prompt", int),
        ("needle valid reply", int),
        ("[tool:bash] command=echo valid", int),
    ]

    incremental, checkpoint = [], None
    for end in range(batch_size, len(events) + batch_size, batch_size):
        blocks, checkpoint = extract_batch(
            "alice", events[:end], checkpoint, all_branches=True
        )
        incremental.extend(blocks)
    assert incremental == full

    # Selected-only compatibility also must not let a malformed opener
    # suppress the only legitimate user event.
    selected, _ = extract_batch("alice", events)
    assert [b.content for b in selected] == [
        "needle prompt",
        "needle valid reply",
        "[tool:bash] command=echo valid",
    ]


@pytest.mark.parametrize("batch_size", [1, 2, 4])
def test_dedup_ignores_invalid_intervening_events_across_checkpoint(batch_size):
    """Invalid rows do not become dedup predecessors or split eligible adjacency."""
    events = [
        _event(
            1, "user_input", "first valid needle", coords=(1, 1), parent_branch_path=[]
        ),
        _event(
            2,
            "user_input",
            "ignored invalid needle",
            coords=(True, 1),
            parent_branch_path=[],
        ),
        _event(
            3, "user_input", "first valid needle", coords=(1, 1), parent_branch_path=[]
        ),
        _event(
            4, "text", "valid followup needle", coords=(1, 1), parent_branch_path=[]
        ),
    ]
    full, _ = extract_batch("alice", events, all_branches=True)
    assert [b.content for b in full] == [
        "first valid needle",
        "ignored invalid needle",
        "valid followup needle",
    ]
    checkpoint, incremental = None, []
    for end in range(batch_size, len(events) + batch_size, batch_size):
        blocks, checkpoint = extract_batch(
            "alice", events[:end], checkpoint, all_branches=True
        )
        incremental.extend(blocks)
    assert incremental == full


def test_invalid_dedup_predecessor_cannot_hide_scoped_search(tmp_path):
    events = [
        _event(
            1, "user_input", "needle prompt", coords=(True, 1), parent_branch_path=[]
        ),
        _event(2, "user_input", "needle prompt", coords=(1, 1), parent_branch_path=[]),
        _event(
            3, "text", "needle legitimate answer", coords=(1, 1), parent_branch_path=[]
        ),
    ]
    with closing(
        SessionMemory(str(tmp_path / "dedup-invalid.kohakutr"), index_all_branches=True)
    ) as memory:
        memory.index_events("alice", events)
        assert {
            hit.content
            for hit in memory.search("needle", mode="fts", branch_view={1: 1})
        } == {"needle prompt", "needle legitimate answer"}


@pytest.mark.parametrize(
    "invalid",
    [
        [["corrupt", 2]],
        [[True, 1]],
        [[0, 2]],
        [[1, -1]],
        [[1, 1], [1, 2]],
        [[2, 1]],
        {"bad": "path"},
        "malformed",
    ],
)
def test_malformed_explicit_ancestry_cannot_match_both_roots(tmp_path, invalid):
    from kohakuterrarium.session.history.branch_paths import (
        index_eligible_parent_paths_by_position,
    )
    from kohakuterrarium.session.memory.visibility import resolve_search_view

    events = [
        _event(1, "user_input", "root one", coords=(1, 1), parent_branch_path=[]),
        _event(2, "user_input", "root two", coords=(1, 2), parent_branch_path=[]),
        _event(
            3,
            "user_input",
            "untrusted-needle child",
            coords=(2, 1),
            parent_branch_path=invalid,
        ),
        _event(
            4,
            "text",
            "untrusted-needle reply",
            coords=(2, 1),
            parent_branch_path=invalid,
        ),
    ]
    assert index_eligible_parent_paths_by_position(events)[2] is None
    assert index_eligible_parent_paths_by_position(events)[3] is None
    with closing(
        SessionMemory(str(tmp_path / "invalid-path.kohakutr"), index_all_branches=True)
    ) as memory:
        memory.index_events("alice", events)
        for root in (1, 2):
            view = {1: root, 2: 1}
            assert memory.search("untrusted-needle", mode="fts", branch_view=view) == []
            # The malformed opener cannot authorize a branch candidate.
            assert 2 not in resolve_search_view(events, {1: root})


@pytest.mark.parametrize("batch_size", [1, 2, 3, 6])
def test_bookkeeping_continuations_reuse_and_close_only_open_scoped_round(batch_size):
    """Global (0,0) scope is visible, but not a separate conversational Round."""
    events = [
        _event(1, "user_input", "prompt", coords=(1, 1), parent_branch_path=[]),
        _event(2, "text", "bookkeeping reply", coords=(0, 0)),
        _event(
            3, "tool_call", coords=(0, 0), name="bash", args={"command": "echo needle"}
        ),
        _event(
            4,
            "tool_result",
            coords=(0, 0),
            name="bash",
            output="needle tool output is long enough",
        ),
        _event(5, "processing_end", coords=(0, 0)),
        _event(6, "text", "must remain orphan after closure", coords=(0, 0)),
    ]
    expected = [
        "prompt",
        "bookkeeping reply",
        "[tool:bash] command=echo needle",
        "[result:bash] needle tool output is long enough",
    ]
    full, full_state = extract_batch("alice", events, all_branches=True)
    selected, _ = extract_batch("alice", events)
    assert [block.content for block in selected] == expected
    assert [block.content for block in full] == expected
    assert [(b.round_num, b.block_num) for b in full] == [
        (1, 0),
        (1, 1),
        (1, 2),
        (1, 3),
    ]
    assert [(b.turn_index, b.branch_id) for b in full] == [
        (1, 1),
        (0, 0),
        (0, 0),
        (0, 0),
    ]
    assert full_state["open_branches"] == {}
    checkpoint, incremental = None, []
    for stop in range(batch_size, len(events) + batch_size, batch_size):
        new, checkpoint = extract_batch(
            "alice", events[:stop], checkpoint, all_branches=True
        )
        incremental.extend(new)
    assert incremental == full
    assert checkpoint["open_branches"] == {}


def test_bookkeeping_cannot_choose_between_open_sibling_rounds():
    events = [
        _event(1, "user_input", "branch one", coords=(1, 1), parent_branch_path=[]),
        _event(2, "user_input", "branch two", coords=(1, 2), parent_branch_path=[]),
        _event(3, "text", "ambiguous bookkeeping", coords=(0, 0)),
        _event(4, "processing_end", coords=(0, 0)),
    ]
    blocks, cursor = extract_batch("alice", events, all_branches=True)
    assert [b.content for b in blocks] == ["branch one", "branch two"]
    assert len(cursor["open_branches"]) == 2


def test_bookkeeping_continuations_are_searchable_in_published_index(tmp_path):
    """Global event coordinates stay raw, but inherit an unambiguous Round."""
    events = [
        _event(1, "user_input", "prompt", coords=(1, 1), parent_branch_path=[]),
        _event(2, "text", "bookkeeping-needle reply", coords=(0, 0)),
        _event(
            3,
            "tool_result",
            coords=(0, 0),
            name="bash",
            output="bookkeeping-needle tool result long enough",
        ),
        _event(4, "processing_end", coords=(0, 0)),
        _event(5, "text", "bookkeeping-needle orphan", coords=(0, 0)),
    ]
    with closing(
        SessionMemory(
            str(tmp_path / "bookkeeping-memory.kohakutr"), index_all_branches=True
        )
    ) as memory:
        memory.index_events("alice", events)
        hits = memory.search(
            "bookkeeping-needle",
            mode="fts",
            branch_view={1: 1},
            k=10,
        )
        contents = {hit.content for hit in hits}
        assert contents == {
            "bookkeeping-needle reply",
            "[result:bash] bookkeeping-needle tool result long enough",
        }
        assert all((hit.turn_index, hit.branch_id) == (0, 0) for hit in hits)
