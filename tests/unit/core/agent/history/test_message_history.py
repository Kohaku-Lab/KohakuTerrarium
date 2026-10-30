"""Settled-result replay membership, first-call lookup and work budgets."""

import copy

import pytest

from kohakuterrarium.core import agent_message_history as history
from kohakuterrarium.session import history as session_history


def _event(event_id, kind, identity, turn, **extra):
    return {
        "event_id": event_id,
        "type": kind,
        "call_id": identity,
        "turn_index": turn,
        **extra,
    }


def _projected(event):
    return {
        **{key: value for key, value in event.items() if key != "event_id"},
        "_source_event_id": event.get("event_id"),
        "_settled_from_call": True,
    }


@pytest.mark.parametrize("operation", ["replay", "snapshot"])
def test_settled_selection_and_coverage_share_raw_path_index(monkeypatch, operation):
    events = [
        _event(1, "user_message", "", 1, branch_id=1, content="start"),
        _event(2, "tool_call", "job", 1, branch_id=1, name="read", args={}),
        _event(3, "user_message", "", 2, branch_id=1, content="later"),
        _event(4, "tool_result", "job", 2, branch_id=1, output="done"),
    ]
    indexed = []
    original = session_history.index_parent_paths

    def observe(projection):
        indexed.append(tuple(map(id, projection)))
        return original(projection)

    monkeypatch.setattr(session_history, "index_parent_paths", observe)
    monkeypatch.setattr(history, "index_parent_paths", observe, raising=False)
    if operation == "replay":
        messages = history.replay_with_settled_tools(events, resumable=True)
        assert any(message.get("content") == "done" for message in messages)
    else:
        assert history.snapshot_needs_settled_tool_replay(events, None, snapshot=[])
    assert indexed.count(tuple(map(id, events))) == 1


@pytest.mark.parametrize("count", [16, 64])
def test_settled_lookup_identity_visits_are_linear(monkeypatch, count):
    calls = [_event(i, "tool_call", f"job-{i}", 1) for i in range(count)]
    results = [
        _event(count + i, "tool_result", f"job-{i}", 2, output={"value": i})
        for i in range(count)
    ]
    events = calls + results
    before = copy.deepcopy(events)
    visits = 0
    original = history._tool_identity

    def counted(event):
        nonlocal visits
        visits += 1
        return original(event)

    monkeypatch.setattr(history, "_tool_identity", counted)
    actual = history.include_settled_tool_results(events, set(range(2 * count)))
    expected = [item for pair in zip(calls, map(_projected, results)) for item in pair]
    assert actual == expected
    assert events == before
    for index, call in enumerate(calls):
        assert actual[2 * index] is call
        assert actual[2 * index + 1]["output"] is results[index]["output"]
    assert visits <= 6 * count, f"Repeated call scan: {visits} identity visits"


@pytest.mark.parametrize(
    ("first_fields", "result_turn", "project"),
    [
        ({}, 3, False),
        ({"turn_index": None}, 3, False),
        ({"turn_index": "1"}, 3, False),
        ({"turn_index": 1.0}, 3, False),
        ({"turn_index": False}, 1, True),
        ({"turn_index": True}, 1, False),
        ({"turn_index": True}, 2, True),
        ({"turn_index": 0}, True, True),
        ({"turn_index": 1}, "3", False),
        ({"turn_index": 1}, None, False),
        ({"turn_index": 2}, 2, False),
        ({"turn_index": 2}, 1, False),
        ({"turn_index": 1}, 3, True),
    ],
)
def test_first_matching_call_turn_is_not_replaced(first_fields, result_turn, project):
    first = {"event_id": 1, "type": "tool_call", "call_id": "same", **first_fields}
    second = _event(2, "tool_call", "same", 0)
    result = _event(3, "tool_result", "same", result_turn, output=[{"text": "done"}])
    events = [first, second, result]
    before = copy.deepcopy(events)
    actual = history.include_settled_tool_results(events, {1, 2, 3})
    if project:
        projected = _projected(result)
        assert actual == [first, projected, second, projected]
        assert actual[1] is actual[3]
        assert actual[1] is not result
        assert actual[1]["output"] is result["output"]
        assert "event_id" not in actual[1]
    else:
        assert actual == events
        assert actual[2] is result
    assert actual[0] is first
    assert actual[2 if project else 1] is second
    assert events == before


@pytest.mark.parametrize(
    ("first_identity", "second_identity", "result_identity", "matched"),
    [
        ({"call_id": 7}, {"call_id": "7"}, {"job_id": 7}, True),
        ({"call_id": "", "job_id": "j"}, {"call_id": "j"}, {"call_id": "j"}, True),
        ({"call_id": 0, "job_id": 9}, {"call_id": "9"}, {"job_id": "9"}, True),
        ({"call_id": False, "job_id": "j"}, {"call_id": "j"}, {"job_id": "j"}, True),
        ({"call_id": [1]}, {"call_id": "[1]"}, {"job_id": [1]}, True),
        (
            {"call_id": "primary", "job_id": "j"},
            {"call_id": "j"},
            {"job_id": "j"},
            False,
        ),
        ({"call_id": "", "job_id": 0}, {"call_id": None}, {"job_id": ""}, False),
    ],
)
def test_identity_aliases_preserve_first_match(
    first_identity, second_identity, result_identity, matched
):
    first = {"event_id": 1, "type": "tool_call", "turn_index": None, **first_identity}
    second = {"event_id": 2, "type": "tool_call", "turn_index": 1, **second_identity}
    result = {"event_id": 3, "type": "tool_result", "turn_index": 2, **result_identity}
    events = [first, second, result]
    before = copy.deepcopy(events)
    actual = history.include_settled_tool_results(events, {1, 2, 3})
    if matched or not history._tool_identity(result):
        assert actual == events
        assert all(left is right for left, right in zip(actual, events))
    else:
        assert actual == [first, second, _projected(result)]
        assert actual[0] is first and actual[1] is second
    assert events == before


def test_live_call_membership_and_result_coverage_are_distinct():
    stale = _event(1, "tool_call", "same", None)
    unrelated = _event(2, "assistant_message", "same", None)
    covered_call = _event(3, "tool_call", "same", 1)
    later_duplicate = _event(4, "tool_call", "same", 0)
    covered_result = _event(5, "tool_result", "same", 2, output="covered")
    result = _event(6, "tool_result", "same", 3, output={"data": ["final"]})
    duplicate_result = _event(7, "tool_result", "same", 4, output="not selected")
    orphan = _event(8, "tool_result", "orphan", 4, output="orphan")
    events = [
        stale,
        unrelated,
        covered_call,
        later_duplicate,
        covered_result,
        result,
        duplicate_result,
        orphan,
    ]
    before = copy.deepcopy(events)
    live = {2, 3, 4, 6, 7, 8}
    covered = {3, 5}
    actual = history.include_settled_tool_results(
        events, live, covered_event_ids=covered
    )
    projection = _projected(result)
    assert actual == [
        unrelated,
        covered_call,
        projection,
        later_duplicate,
        projection,
        orphan,
    ]
    assert actual[0] is unrelated and actual[1] is covered_call
    assert actual[2] is actual[4]
    assert actual[3] is later_duplicate and actual[5] is orphan
    assert actual[2]["output"] is result["output"]
    assert events == before
    assert live == {2, 3, 4, 6, 7, 8}
    assert covered == {3, 5}


@pytest.mark.parametrize("call_id", [None, "legacy", True])
def test_missing_late_result_projects_once_without_live_integer_id(call_id):
    call = _event(call_id, "tool_call", "job", None, metadata={"origin": "call"})
    result = _event(10, "tool_result", "job", 5, output=[{"text": "done"}], branch_id=4)
    duplicate = _event(11, "tool_result", "job", 6, output="duplicate")
    events = [call, result, duplicate]
    before = copy.deepcopy(events)
    live = {1} if call_id is True else set()
    actual = history.include_settled_tool_results(events, live)
    assert actual == [call, _projected(result)]
    assert actual[0] is call
    assert actual[1]["output"] is result["output"]
    assert events == before
    blocked = history.include_settled_tool_results(
        events, live, covered_event_ids={10, 11}
    )
    assert blocked == [call]
    assert blocked[0] is call


@pytest.mark.parametrize(
    ("first_turn", "duplicate_turns", "project"),
    [(1, [], True), (1, [None], True), (None, [1], False)],
)
def test_covered_live_call_still_owns_the_first_match(
    first_turn, duplicate_turns, project
):
    call = _event(1, "tool_call", "job", first_turn)
    duplicates = [_event(2, "tool_call", "job", turn) for turn in duplicate_turns]
    result = _event(3, "tool_result", "job", 3, output={"nested": ["done"]})
    events = [call, *duplicates, result]
    before = copy.deepcopy(events)
    actual = history.include_settled_tool_results(
        events, {1, 2, 3}, covered_event_ids={1}
    )
    projection = _projected(result)
    expected = (
        [item for event in [call, *duplicates] for item in (event, projection)]
        if project
        else events
    )
    assert actual == expected
    assert actual[0] is call
    if project:
        assert actual[1]["output"] is result["output"]
        if duplicates:
            assert actual[1] is actual[3]
    else:
        assert actual[-1] is result
    assert events == before


def test_already_settled_same_turn_and_unidentified_results_stay_in_order():
    call = _event(1, "tool_call", "job", 2)
    result = _event(2, "tool_result", "job", 2, output="same turn")
    empty_call = _event(3, "tool_call", "", 1)
    empty_result = _event(4, "tool_result", "", 3, output="unidentified")
    events = [call, result, empty_call, empty_result]
    actual = history.include_settled_tool_results(
        events, {1, 2, 3, 4}, covered_event_ids={2}
    )
    assert actual == events
    assert all(left is right for left, right in zip(actual, events))
    assert history.include_settled_tool_results([], set()) == []
