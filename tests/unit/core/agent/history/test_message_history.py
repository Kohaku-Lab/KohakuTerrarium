"""Settled-result replay membership, first-call lookup and work budgets."""

import copy

import pytest

from kohakuterrarium.core.agent.history import message_history as history
from kohakuterrarium.session.history import replay as session_history


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


def test_same_turn_result_after_assistant_round_is_projected_to_call():
    events = [
        _event(1, "tool_call", "job", 3),
        _event(2, "assistant_round_start", "", 3),
        _event(3, "assistant_message", "", 3, content="intermediate"),
        _event(4, "tool_result", "job", 3, output="done"),
    ]
    actual = history.include_settled_tool_results(events, {1, 2, 3, 4})
    assert actual == [events[0], _projected(events[3]), events[1], events[2]]


def test_same_turn_projection_preserves_single_explicit_announcement():
    events = [
        _event(1, "user_message", "", 3, branch_id=1, content="go"),
        _event(
            2,
            "tool_call",
            "job",
            3,
            branch_id=1,
            name="work",
            tool_call_id="call1",
            args={},
        ),
        _event(
            3,
            "assistant_tool_calls",
            "",
            3,
            branch_id=1,
            tool_calls=[
                {
                    "id": "call1",
                    "type": "function",
                    "function": {"name": "work", "arguments": "{}"},
                }
            ],
        ),
        _event(4, "assistant_round_start", "", 3, branch_id=1),
        _event(5, "tool_result", "job", 3, branch_id=1, output="done"),
    ]
    messages = history.replay_with_settled_tools(events, branch_view={3: 1})
    announcements = [
        call["id"] for message in messages for call in message.get("tool_calls", [])
    ]
    assert announcements == ["call1"]
    call_index = next(
        i for i, message in enumerate(messages) if message.get("tool_calls")
    )
    assert messages[call_index + 1]["role"] == "tool"
    assert messages[call_index + 1]["content"] == "done"


@pytest.mark.parametrize("canonical", [False, True])
def test_same_turn_grouped_late_results_keep_one_announcement_and_text(canonical):
    calls = [
        {
            "id": f"call{i}",
            "type": "function",
            "function": {"name": "work", "arguments": "{}"},
        }
        for i in (1, 2)
    ]
    events = [
        _event(1, "user_message", "", 3, branch_id=1, content="go"),
        _event(9, "assistant_round_start", "", 3, branch_id=1),
        _event(
            2, "tool_call", "job1", 3, branch_id=1, name="work", tool_call_id="call1"
        ),
        _event(
            3, "tool_call", "job2", 3, branch_id=1, name="work", tool_call_id="call2"
        ),
        _event(
            4,
            "assistant_message" if canonical else "assistant_tool_calls",
            "",
            3,
            branch_id=1,
            **(
                {
                    "round_start_event_id": 9,
                    "message": {
                        "role": "assistant",
                        "content": "preserve this",
                        "tool_calls": calls,
                    },
                }
                if canonical
                else {"content": "preserve this", "tool_calls": calls}
            ),
        ),
        _event(5, "assistant_round_start", "", 3, branch_id=1),
        _event(
            6,
            "assistant_message",
            "",
            3,
            branch_id=1,
            round_start_event_id=5,
            message={"role": "assistant", "content": "intermediate"},
        ),
        _event(7, "tool_result", "job2", 3, branch_id=1, output="second"),
        _event(8, "tool_result", "job1", 3, branch_id=1, output="first"),
    ]
    messages = history.replay_with_settled_tools(events, branch_view={3: 1})
    announcements = [m for m in messages if m.get("tool_calls")]
    assert len(announcements) == 1
    assert announcements[0]["content"] == "preserve this"
    assert [c["id"] for c in announcements[0]["tool_calls"]] == ["call1", "call2"]
    idx = messages.index(announcements[0])
    assert {
        (m.get("tool_call_id"), m["content"]) for m in messages[idx + 1 : idx + 3]
    } == {("call1", "first"), ("call2", "second")}
    assert any(m.get("content") == "intermediate" for m in messages)


def test_mismatched_canonical_announcement_is_not_projected():
    calls = [
        {
            "id": "other",
            "type": "function",
            "function": {"name": "work", "arguments": "{}"},
        }
    ]
    events = [
        _event(1, "user_message", "", 3, branch_id=1, content="go"),
        _event(2, "assistant_round_start", "", 3, branch_id=1),
        _event(3, "tool_call", "job1", 3, branch_id=1, tool_call_id="call1"),
        _event(
            4,
            "assistant_message",
            "",
            3,
            branch_id=1,
            round_start_event_id=2,
            message={"role": "assistant", "content": "canonical", "tool_calls": calls},
        ),
        _event(5, "assistant_round_start", "", 3, branch_id=1),
        _event(6, "tool_result", "job1", 3, branch_id=1, output="late"),
    ]
    actual = history.include_settled_tool_results(events, {1, 2, 3, 4, 5, 6})
    assert [e["type"] for e in actual] == [e["type"] for e in events]
    assert actual[-1]["output"] == "late"


def test_same_turn_result_with_mismatched_provider_id_is_not_projected():
    events = [
        _event(1, "user_message", "", 3, branch_id=1, content="go"),
        _event(
            2,
            "tool_call",
            "job1",
            3,
            branch_id=1,
            name="work",
            tool_call_id="call1",
        ),
        _event(
            3,
            "assistant_tool_calls",
            "",
            3,
            branch_id=1,
            tool_calls=[
                {
                    "id": "call1",
                    "type": "function",
                    "function": {"name": "work", "arguments": "{}"},
                }
            ],
        ),
        _event(4, "assistant_round_start", "", 3, branch_id=1),
        _event(
            5,
            "tool_result",
            "job1",
            3,
            branch_id=1,
            tool_call_id="other",
            output="late",
        ),
    ]
    actual = history.include_settled_tool_results(events, {1, 2, 3, 4, 5})
    assert actual == events


def test_same_turn_canonical_announcement_preserves_text_and_result():
    calls = [
        {
            "id": "call1",
            "type": "function",
            "function": {"name": "work", "arguments": "{}"},
        }
    ]
    events = [
        _event(1, "user_message", "", 3, branch_id=1, content="go"),
        _event(2, "assistant_round_start", "", 3, branch_id=1),
        _event(
            3, "tool_call", "job1", 3, branch_id=1, name="work", tool_call_id="call1"
        ),
        _event(
            4,
            "assistant_message",
            "",
            3,
            branch_id=1,
            round_start_event_id=2,
            message={
                "role": "assistant",
                "content": "canonical text",
                "tool_calls": calls,
            },
        ),
        _event(5, "assistant_round_start", "", 3, branch_id=1),
        _event(
            6,
            "assistant_message",
            "",
            3,
            branch_id=1,
            round_start_event_id=5,
            message={"role": "assistant", "content": "later text"},
        ),
        _event(7, "tool_result", "job1", 3, branch_id=1, output="done"),
    ]
    messages = history.replay_with_settled_tools(events, branch_view={3: 1})
    index = next(i for i, m in enumerate(messages) if m.get("tool_calls"))
    assert messages[index]["content"] == "canonical text"
    assert messages[index + 1]["role"] == "tool"
    assert messages[index + 1]["content"] == "done"
    assert any(m.get("content") == "later text" for m in messages)


def test_same_turn_reused_provider_id_in_later_turn_is_not_suppressed():
    calls = [
        {
            "id": "shared",
            "type": "function",
            "function": {"name": "work", "arguments": "{}"},
        }
    ]
    events = [
        _event(1, "user_message", "", 1, branch_id=1, content="one"),
        _event(
            2, "tool_call", "job1", 1, branch_id=1, name="work", tool_call_id="shared"
        ),
        _event(3, "assistant_tool_calls", "", 1, branch_id=1, tool_calls=calls),
        _event(4, "assistant_round_start", "", 1, branch_id=1),
        _event(
            10,
            "assistant_message",
            "",
            1,
            branch_id=1,
            round_start_event_id=4,
            message={"role": "assistant", "content": "intermediate"},
        ),
        _event(5, "tool_result", "job1", 1, branch_id=1, output="first"),
        _event(6, "user_message", "", 2, branch_id=1, content="two"),
        _event(
            7, "tool_call", "job2", 2, branch_id=1, name="work", tool_call_id="shared"
        ),
        _event(8, "assistant_tool_calls", "", 2, branch_id=1, tool_calls=calls),
        _event(9, "tool_result", "job2", 2, branch_id=1, output="second"),
    ]
    messages = history.replay_with_settled_tools(events, branch_view={1: 1, 2: 1})
    assert len([m for m in messages if m.get("tool_calls")]) == 2
    first = next(i for i, m in enumerate(messages) if m.get("tool_calls"))
    assert messages[first + 1]["role"] == "tool"
    assert messages[first + 1]["content"] == "first"
    assert [m["content"] for m in messages if m["role"] == "tool"] == [
        "first",
        "second",
    ]


def test_same_turn_repeated_job_identity_keeps_each_original_result():
    events = [
        _event(1, "tool_call", "same", 3),
        _event(2, "tool_result", "same", 3, output="first"),
        _event(3, "assistant_round_start", "", 3),
        _event(4, "tool_call", "same", 3),
        _event(5, "assistant_round_start", "", 3),
        _event(6, "tool_result", "same", 3, output="second"),
    ]
    actual = history.include_settled_tool_results(events, {1, 2, 3, 4, 5, 6})
    assert [e["output"] for e in actual if e["type"] == "tool_result"] == [
        "first",
        "second",
    ]
    assert len([e for e in actual if e["type"] == "tool_call"]) == 2


def test_reused_job_id_does_not_attach_discarded_earlier_result():
    events = [
        _event(1, "tool_call", "same", 1, branch_id=1),
        _event(2, "tool_result", "same", 1, branch_id=1, output="old"),
        _event(3, "tool_call", "same", 2, branch_id=1),
    ]
    actual = history.include_settled_tool_results(events, {3})
    assert [event["type"] for event in actual] == ["tool_call"]


def test_same_turn_multiple_results_do_not_disappear():
    events = [
        _event(1, "tool_call", "job", 3),
        _event(2, "assistant_round_start", "", 3),
        _event(3, "tool_result", "job", 3, output="first"),
        _event(4, "tool_result", "job", 3, output="second"),
    ]
    actual = history.include_settled_tool_results(events, {1, 2, 3, 4})
    assert [e["output"] for e in actual if e["type"] == "tool_result"] == [
        "first",
        "second",
    ]


def test_same_turn_projection_does_not_cross_compaction_boundary():
    events = [
        _event(1, "tool_call", "job", 3),
        _event(2, "compact_complete", "", 3),
        _event(3, "assistant_round_start", "", 3),
        _event(4, "tool_result", "job", 3, output="done"),
    ]
    actual = history.include_settled_tool_results(events, {1, 2, 3, 4})
    assert actual == events


def test_same_turn_unidentified_result_is_not_projected():
    events = [
        _event(1, "tool_call", "", 3),
        _event(2, "assistant_round_start", "", 3),
        _event(3, "tool_result", "", 3, output="done"),
    ]
    assert history.include_settled_tool_results(events, {1, 2, 3}) == events


def test_same_turn_unannounced_group_does_not_split_calls():
    events = [
        _event(1, "tool_call", "a", 3),
        _event(2, "tool_call", "b", 3),
        _event(3, "tool_result", "b", 3, output="b"),
        _event(4, "assistant_round_start", "", 3),
        _event(5, "tool_result", "a", 3, output="a"),
    ]
    actual = history.include_settled_tool_results(events, {1, 2, 3, 4, 5})
    assert actual[:2] == events[:2]


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
