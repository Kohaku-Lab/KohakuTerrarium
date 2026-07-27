"""Tests for strict raw-event history prefix selection."""

from copy import deepcopy

import pytest

from kohakuterrarium.session.raw_history import (
    InvalidRawHistoryEventError,
    MissingRawHistoryError,
    TargetUserMessageConflictError,
    TargetUserMessageLineageError,
    TargetUserMessageNotFoundError,
    UserMessageSelector,
    select_raw_history_prefix,
)


def _event(
    event_id: int,
    event_type: str,
    *,
    turn: int | None = None,
    branch: int | None = None,
    path: list[list[int]] | None = None,
    content: str = "",
    **extra,
):
    event = {"event_id": event_id, "type": event_type}
    if turn is not None:
        event["turn_index"] = turn
    if branch is not None:
        event["branch_id"] = branch
    if path is not None:
        event["parent_branch_path"] = path
    if content:
        event["content"] = content
    event.update(extra)
    return event


def _branched_events():
    return [
        _event(1, "user_message", turn=1, branch=1, path=[], content="old one"),
        _event(2, "text_chunk", turn=1, branch=1, path=[], content="old reply"),
        _event(3, "user_message", turn=2, branch=1, path=[[1, 1]], content="old two"),
        _event(4, "text_chunk", turn=2, branch=1, path=[[1, 1]], content="old tail"),
        _event(5, "user_message", turn=1, branch=2, path=[], content="new one"),
        _event(6, "text_chunk", turn=1, branch=2, path=[], content="new reply"),
        _event(7, "user_message", turn=2, branch=2, path=[[1, 2]], content="new two"),
        _event(8, "text_chunk", turn=2, branch=2, path=[[1, 2]], content="after"),
    ]


def _select(events, event_id=3, turn=2, branch=1, view=None):
    return select_raw_history_prefix(
        events,
        selector=UserMessageSelector(event_id, turn, branch),
        branch_view=view or {turn: branch},
    )


def test_selects_plain_uncompacted_history_before_target():
    events = [
        _event(1, "system_prompt_set", content="system"),
        _event(2, "user_message", turn=1, branch=1, path=[], content="first"),
        _event(3, "text_chunk", turn=1, branch=1, path=[], content="answer"),
        _event(4, "user_message", turn=2, branch=1, path=[[1, 1]], content="target"),
        _event(5, "text_chunk", turn=2, branch=1, path=[[1, 1]], content="later"),
    ]

    result = _select(events, event_id=4)

    assert [event["event_id"] for event in result.events] == [1, 2, 3]
    assert result.target["content"] == "target"
    assert result.branch_view == {1: 1, 2: 1}


def test_compaction_and_snapshot_markers_do_not_replace_raw_events():
    events = [
        _event(1, "user_message", turn=1, branch=1, path=[], content="verbatim"),
        _event(2, "text_chunk", turn=1, branch=1, path=[], content="original reply"),
        _event(
            3,
            "compact_replace",
            turn=1,
            branch=1,
            path=[],
            summary_text="invented summary",
            replaced_from_event_id=1,
            replaced_to_event_id=2,
        ),
        _event(4, "snapshot", snapshot=[{"role": "user", "content": "summary"}]),
        _event(5, "user_message", turn=2, branch=1, path=[[1, 1]], content="target"),
    ]

    result = _select(events, event_id=5)

    assert result.events[0]["content"] == "verbatim"
    assert result.events[1]["content"] == "original reply"
    assert result.events[2]["type"] == "compact_replace"
    assert result.events[3]["type"] == "snapshot"


def test_selected_branch_prefix_contains_only_its_lineage_and_excludes_target_tail():
    result = _select(_branched_events(), event_id=7, branch=2, view={1: 2, 2: 2})

    assert [event["event_id"] for event in result.events] == [5, 6]
    assert 7 not in {event["event_id"] for event in result.events}
    assert 8 not in {event["event_id"] for event in result.events}


def test_selection_does_not_modify_old_branch_or_inputs():
    events = _branched_events()
    before = deepcopy(events)

    result = _select(events)
    result.events[0]["content"] = "mutated copy"
    result.target["content"] = "mutated target copy"

    assert events == before


def test_missing_target_is_rejected():
    with pytest.raises(TargetUserMessageNotFoundError, match="does not exist"):
        _select(_branched_events(), event_id=99)


def test_target_outside_selected_branch_is_rejected():
    with pytest.raises(TargetUserMessageLineageError, match="selects branch 2"):
        _select(_branched_events(), view={1: 2, 2: 2})


def test_conflicting_selector_coordinates_are_rejected():
    with pytest.raises(TargetUserMessageConflictError, match="turn/branch"):
        _select(_branched_events(), turn=9)


def test_duplicate_event_identity_is_rejected():
    events = _branched_events()
    events.append(_event(3, "user_message", turn=2, branch=1, content="collision"))

    with pytest.raises(InvalidRawHistoryEventError, match="duplicate raw event_id 3"):
        _select(events)


def test_missing_event_identity_is_rejected():
    events = _branched_events()
    events[0].pop("event_id")

    with pytest.raises(InvalidRawHistoryEventError, match="lacks a positive"):
        _select(events)


def test_incomplete_raw_turn_metadata_is_rejected():
    events = _branched_events()
    events.insert(2, _event(9, "text_chunk", turn=1, content="unattributed"))

    with pytest.raises(MissingRawHistoryError, match="incomplete turn/branch"):
        _select(events)
