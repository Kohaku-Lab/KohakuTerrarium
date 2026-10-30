"""Restore-local parent-index budgets and projection boundaries."""

from copy import deepcopy

import pytest

from kohakuterrarium.core.agent.history import message_history as agent_message_history
from kohakuterrarium.core.agent.history.message_history import (
    replay_with_settled_tools,
    snapshot_needs_settled_tool_replay,
)
from kohakuterrarium.core.conversation.snapshot import prepare_snapshot
from kohakuterrarium.session import history, resume_branch
from kohakuterrarium.session.snapshot import load_records
from kohakuterrarium.session.store import SessionStore


@pytest.fixture
def checkpoint(tmp_path):
    store = SessionStore(str(tmp_path / "index.kohakutr"))
    events = [
        {
            "event_id": 1,
            "type": "user_message",
            "content": "old",
            "turn_index": 1,
            "branch_id": 1,
        },
        {
            "event_id": 2,
            "type": "user_message",
            "content": "new",
            "turn_index": 1,
            "branch_id": 2,
        },
        {
            "event_id": 3,
            "type": "user_message",
            "content": "child",
            "turn_index": 2,
            "branch_id": 1,
        },
    ]
    store.save_branch_snapshot(
        "alice",
        [
            {
                "role": "user",
                "content": "old",
                "metadata": {"turn_index": 1, "branch_id": 1},
            }
        ],
        branch={"turn_index": 1, "branch_id": 1, "parent_branch_path": []},
        watermark=1,
    )
    try:
        yield store, events
    finally:
        store.close()


def count_indexes(monkeypatch):
    calls = []
    original = history.index_parent_paths

    def counted(events):
        calls.append(tuple(event.get("event_id") for event in events))
        return original(events)

    monkeypatch.setattr(history, "index_parent_paths", counted)
    monkeypatch.setattr(resume_branch, "index_parent_paths", counted)
    monkeypatch.setattr(agent_message_history, "index_parent_paths", counted)
    return calls


def test_saved_snapshot_indexes_owned_raw_once(checkpoint, monkeypatch):
    store, events = checkpoint
    before = deepcopy(events)
    calls = count_indexes(monkeypatch)
    assert resume_branch.prepare_saved_snapshot(store, "alice", events) is None
    assert calls == [(1, 2, 3)]
    assert events == before


@pytest.mark.parametrize("supplied", [False, True])
def test_prepare_snapshot_targeted_index_budget(checkpoint, monkeypatch, supplied):
    store, events = checkpoint
    paths = history.index_parent_paths(events)
    original = deepcopy(paths)
    calls = count_indexes(monkeypatch)
    kwargs = {"parent_paths": paths} if supplied else {}
    assert (
        prepare_snapshot(
            store,
            "alice",
            events,
            {1: 2, 2: 1},
            replay=replay_with_settled_tools,
            needs_replay=snapshot_needs_settled_tool_replay,
            **kwargs,
        )
        is None
    )
    assert calls == ([] if supplied else [(1, 2, 3), (1, 2, 3)])
    assert paths == original


@pytest.mark.parametrize(
    "helper",
    [
        history.select_live_event_ids,
        history.compact_covered_event_ids,
        history.compact_replacement_rules,
    ],
)
@pytest.mark.parametrize("supplied", [False, True])
def test_ancestry_helpers_supplied_or_omitted(
    checkpoint, monkeypatch, helper, supplied
):
    _, events = checkpoint
    paths = history.index_parent_paths(events)
    expected = helper(events, branch_view={1: 2, 2: 1})
    calls = count_indexes(monkeypatch)
    kwargs = {"parent_paths": paths} if supplied else {}
    assert helper(events, branch_view={1: 2, 2: 1}, **kwargs) == expected
    assert calls == ([] if supplied else [(1, 2, 3)])


@pytest.mark.parametrize("records", [[], [None, {"path": "bad"}], "malformed"])
def test_missing_checkpoint_does_not_index(checkpoint, monkeypatch, records):
    store, events = checkpoint
    store.state["alice:branch_snapshots"] = records
    calls = count_indexes(monkeypatch)
    assert resume_branch.prepare_saved_snapshot(store, "alice", events) is None
    assert (
        prepare_snapshot(
            store,
            "alice",
            events,
            {1: 2, 2: 1},
            records=[],
            replay=replay_with_settled_tools,
            needs_replay=snapshot_needs_settled_tool_replay,
        )
        is None
    )
    assert calls == []


def test_projection_reindexes_inferred_ancestry(checkpoint, monkeypatch):
    _, events = checkpoint
    paths = history.index_parent_paths(events)
    projected = events[2:]
    assert paths[3] == ((1, 2),)
    assert history.index_parent_paths(projected)[3] == ()
    calls = count_indexes(monkeypatch)
    assert history.select_live_event_ids(projected, branch_view={1: 1, 2: 1}) == {3}
    assert calls == [(3,)]


@pytest.mark.parametrize("cutoff", [0, 1])
def test_late_tool_and_projected_tail_parity(checkpoint, monkeypatch, cutoff):
    store, events = checkpoint
    events = [events[0]] + [
        {
            "event_id": 2,
            "type": "tool_call",
            "call_id": "job",
            "name": "echo",
            "args": {},
            "turn_index": 1,
            "branch_id": 1,
        },
        {
            "event_id": 3,
            "type": "user_message",
            "content": "child",
            "turn_index": 2,
            "branch_id": 1,
        },
        {
            "event_id": 4,
            "type": "tool_result",
            "call_id": "job",
            "name": "echo",
            "output": "settled",
            "turn_index": 2,
            "branch_id": 2,
        },
    ]
    records = load_records(store, "alice")
    records[0]["context_cutoff_event_id"] = cutoff
    before = deepcopy(events)
    kwargs = dict(
        records=records,
        replay=replay_with_settled_tools,
        needs_replay=snapshot_needs_settled_tool_replay,
    )
    baseline = prepare_snapshot(store, "alice", events, {1: 1, 2: 1}, **kwargs)
    paths = history.index_parent_paths(events)
    calls = count_indexes(monkeypatch)
    candidate = prepare_snapshot(
        store, "alice", events, {1: 1, 2: 1}, parent_paths=paths, **kwargs
    )
    assert candidate == baseline
    assert events == before
    if cutoff:
        assert candidate["context_cutoff_event_id"] == 1
        assert any(
            m.get("tool_call_id") == "job" and m["content"] == "settled"
            for m in candidate["messages"]
        )
        assert any(ids != (1, 2, 3, 4) for ids in calls)
    else:
        assert candidate is None


@pytest.mark.parametrize(
    "helper",
    [
        history.select_live_event_ids,
        history.compact_covered_event_ids,
        history.compact_replacement_rules,
    ],
)
def test_valid_empty_parent_index_is_not_recomputed(monkeypatch, helper):
    events = [{"type": "text", "content": "unidentified"}]
    assert history.index_parent_paths(events) == {}
    expected = helper(events)
    calls = count_indexes(monkeypatch)
    assert helper(events, parent_paths={}) == expected
    assert calls == []


@pytest.mark.parametrize(
    "view, expected, covered",
    [({1: 1}, "old summary", {1}), ({1: 2}, "new summary", {2, 3})],
)
def test_compact_rules_respect_inferred_ancestry(
    checkpoint, monkeypatch, view, expected, covered
):
    _, events = checkpoint
    events += [
        {
            "event_id": 4,
            "type": "compact_replace",
            "summary": "old summary",
            "replaced_from_event_id": 1,
            "replaced_to_event_id": 3,
            "compact_path": [[1, 1]],
        },
        {
            "event_id": 5,
            "type": "compact_replace",
            "summary": "new summary",
            "replaced_from_event_id": 1,
            "replaced_to_event_id": 3,
            "compact_path": [[1, 2], [2, 1]],
        },
    ]
    paths = history.index_parent_paths(events)
    assert paths[3] == ((1, 2),)
    baseline_rules = history.compact_replacement_rules(events, view)
    baseline_covered = history.compact_covered_event_ids(events, view)
    calls = count_indexes(monkeypatch)
    rules = history.compact_replacement_rules(events, view, parent_paths=paths)
    assert rules == baseline_rules
    assert [rule.summary for rule in rules] == [expected]
    assert (
        history.compact_covered_event_ids(events, view, parent_paths=paths)
        == baseline_covered
        == covered
    )
    assert calls == []


def test_snapshot_tail_never_receives_raw_parent_index(checkpoint, monkeypatch):
    store, events = checkpoint
    events = [events[0], events[2]]
    selected = {1: 1, 2: 1}
    paths = history.index_parent_paths(events)
    observed = []

    def replay_tail(tail, **kwargs):
        assert tail == [events[1]]
        assert tail is not events
        assert "parent_paths" not in kwargs
        assert history.index_parent_paths(tail)[3] == ()
        observed.append(tail)
        return replay_with_settled_tools(tail, **kwargs)

    calls = count_indexes(monkeypatch)
    baseline = prepare_snapshot(
        store,
        "alice",
        events,
        selected,
        replay=replay_tail,
        needs_replay=snapshot_needs_settled_tool_replay,
    )
    baseline_calls = list(calls)
    calls.clear()
    observed.clear()
    result = prepare_snapshot(
        store,
        "alice",
        events,
        selected,
        parent_paths=paths,
        replay=replay_tail,
        needs_replay=snapshot_needs_settled_tool_replay,
    )
    assert result == baseline
    assert baseline_calls == [(1, 3), (1, 3), *calls]
    assert calls.count((1, 3)) == 1
    assert calls.count((3,)) == 3
    assert paths[3] == ((1, 1),)
    assert [message["content"] for message in result["messages"]] == ["old", "child"]
    assert observed == [[events[1]]]
