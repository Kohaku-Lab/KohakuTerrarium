"""R2 invariants: round handoffs, source coordinates, and safe index migration."""

from contextlib import closing

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


def test_pre_r2_index_is_invalidated_before_recovering_original_coordinates(tmp_path):
    """Old v5 indexes may contain irreversibly normalized physical rows."""
    events = _zero_events()
    path = str(tmp_path / "legacy-v5.kohakutr")
    with closing(SessionMemory(path, index_all_branches=True)) as old:
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
        manifest = dict(old._manifest(), format=5)
        old._state["memory:index_manifest"] = manifest
        assert old._fts.count() == len(events) + 1

    with closing(SessionMemory(path, index_all_branches=True)) as upgraded:
        with pytest.raises(ValueError, match="index.*(invalid|building|rebuild)"):
            upgraded.search("needle", mode="fts", branch_view={1: 1})
        upgraded.index_events("alice", events)
        assert upgraded._manifest()["format"] == 6
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
