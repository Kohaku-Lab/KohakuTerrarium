"""Checkpoint selection and dependency-safe prefix reconstruction tests."""

import copy
from pathlib import Path
from types import SimpleNamespace

import pytest

from kohakuterrarium.core import snapshot_history
from kohakuterrarium.core.agent_message_history import (
    replay_with_settled_tools,
    snapshot_needs_settled_tool_replay,
)
from kohakuterrarium.core.snapshot_repair import repair_gap_messages
from kohakuterrarium.core.snapshot_history import prepare_snapshot
from kohakuterrarium.session.snapshot import load_records
from kohakuterrarium.session.store import SessionStore

_EVENTS = [
    {
        "event_id": 1,
        "type": "user_message",
        "content": "raw_u1",
        "turn_index": 1,
        "branch_id": 1,
    },
    {
        "event_id": 2,
        "type": "text",
        "content": "raw_a1",
        "turn_index": 1,
        "branch_id": 1,
    },
    {
        "event_id": 3,
        "type": "user_message",
        "content": "raw_u2_a",
        "turn_index": 2,
        "branch_id": 1,
        "parent_branch_path": [[1, 1]],
    },
    {
        "event_id": 4,
        "type": "text",
        "content": "raw_a2_a",
        "turn_index": 2,
        "branch_id": 1,
        "parent_branch_path": [[1, 1]],
    },
    {
        "event_id": 5,
        "type": "user_message",
        "content": "raw_u2_b",
        "turn_index": 2,
        "branch_id": 2,
        "parent_branch_path": [[1, 1]],
    },
    {
        "event_id": 6,
        "type": "text",
        "content": "raw_a2_b",
        "turn_index": 2,
        "branch_id": 2,
        "parent_branch_path": [[1, 1]],
    },
]

_CONSUMED_EVENTS = _EVENTS + [
    {
        "event_id": 7,
        "type": "tool_call",
        "call_id": "ancestor",
        "name": "echo",
        "args": {},
        "turn_index": 1,
        "branch_id": 1,
    },
    {
        "event_id": 8,
        "type": "tool_result",
        "call_id": "ancestor",
        "name": "echo",
        "output": "old inherited result",
        "turn_index": 2,
        "branch_id": 2,
        "parent_branch_path": [[1, 1]],
    },
]

_CONTINUED_EVENTS = _CONSUMED_EVENTS + [
    {
        "event_id": 9,
        "type": "user_message",
        "content": "NEW1",
        "turn_index": 3,
        "branch_id": 1,
        "parent_branch_path": [[1, 1], [2, 1]],
    },
    {
        "event_id": 10,
        "type": "text_chunk",
        "content": "new answer",
        "turn_index": 3,
        "branch_id": 1,
        "parent_branch_path": [[1, 1], [2, 1]],
    },
    {
        "event_id": 11,
        "type": "user_message",
        "content": "NEW2",
        "turn_index": 4,
        "branch_id": 1,
        "parent_branch_path": [[1, 1], [2, 1], [3, 1]],
    },
]

_LATER_TOOL_EVENTS = _CONTINUED_EVENTS + [
    {
        "event_id": 12,
        "type": "tool_call",
        "call_id": "newjob",
        "name": "echo",
        "args": {},
        "turn_index": 3,
        "branch_id": 1,
        "parent_branch_path": [[1, 1], [2, 1]],
    },
    {
        "event_id": 13,
        "type": "tool_result",
        "call_id": "newjob",
        "name": "echo",
        "output": "new final result",
        "turn_index": 4,
        "branch_id": 2,
        "parent_branch_path": [[1, 1], [2, 1], [3, 1]],
    },
]

_TARGET_JOB_EVENTS = _LATER_TOOL_EVENTS + [
    {
        "event_id": 14,
        "type": "tool_call",
        "call_id": "target-job",
        "name": "echo",
        "args": {},
        "turn_index": 4,
        "branch_id": 1,
        "parent_branch_path": [[1, 1], [2, 1], [3, 1]],
    },
    {
        "event_id": 15,
        "type": "tool_result",
        "call_id": "target-job",
        "name": "echo",
        "output": "excluded target result",
        "turn_index": 4,
        "branch_id": 1,
        "parent_branch_path": [[1, 1], [2, 1], [3, 1]],
    },
]

_MSGS_A = [
    {
        "role": "user",
        "content": "snap_u1",
        "metadata": {"turn_index": 1, "branch_id": 1},
    },
    {"role": "assistant", "content": "snap_a1"},
    {
        "role": "user",
        "content": "snap_u2_a",
        "metadata": {"turn_index": 2, "branch_id": 1},
    },
    {"role": "assistant", "content": "snap_a2"},
]

_EMPTY_SYSTEM = [{"role": "system", "content": "system"}]

_RETAINED_CUTOFF_MESSAGES = _EMPTY_SYSTEM + [
    {"role": "user", "content": "NEW1", "metadata": {"turn_index": 3, "branch_id": 1}},
    {"role": "assistant", "content": "new answer"},
    {"role": "user", "content": "NEW2", "metadata": {"turn_index": 4, "branch_id": 1}},
]


def _prepare(
    store: SessionStore,
    events: list[dict],
    selected: dict[int, int],
    before_turn: int | None = None,
    namespace: str = "alice",
) -> dict | None:
    return prepare_snapshot(
        store,
        namespace,
        events,
        selected,
        before_turn=before_turn,
        replay=replay_with_settled_tools,
        needs_replay=snapshot_needs_settled_tool_replay,
    )


def _save_branch_msgs_a(store: SessionStore) -> None:
    store.save_branch_snapshot(
        "alice",
        copy.deepcopy(_MSGS_A),
        branch={"turn_index": 2, "branch_id": 1, "parent_branch_path": [[1, 1]]},
        watermark=4,
    )


def _save_cutoff_ack_snapshot(store: SessionStore, pending: bool) -> None:
    ack = _RETAINED_CUTOFF_MESSAGES + (
        [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "newjob",
                        "type": "function",
                        "function": {"name": "echo", "arguments": "{}"},
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "newjob",
                "name": "echo",
                "content": "pending",
                "metadata": {"job_id": "newjob", "tool_result_phase": "pending"},
            },
        ]
        if pending
        else []
    )
    store.save_branch_snapshot(
        "alice",
        ack,
        branch={
            "turn_index": 4,
            "branch_id": 1,
            "parent_branch_path": [[1, 1], [2, 1], [3, 1]],
            "context_cutoff_event_id": 8,
        },
        watermark=13,
    )


@pytest.fixture
def store(tmp_path: Path):
    s = SessionStore(str(tmp_path / "s.kohakutr"))
    try:
        yield s
    finally:
        s.close()


@pytest.fixture
def base_events() -> list[dict]:
    return copy.deepcopy(_EVENTS)


@pytest.fixture
def cleared_context(store: SessionStore) -> tuple[SessionStore, list[dict], list[dict]]:
    store.save_branch_snapshot(
        "alice",
        copy.deepcopy(_RETAINED_CUTOFF_MESSAGES),
        branch={
            "turn_index": 4,
            "branch_id": 1,
            "parent_branch_path": [[1, 1], [2, 1], [3, 1]],
            "context_cutoff_event_id": 8,
        },
        watermark=11,
    )
    return (
        store,
        copy.deepcopy(_CONTINUED_EVENTS),
        copy.deepcopy(_RETAINED_CUTOFF_MESSAGES),
    )


def test_prepare_snapshot_exact_checkpoint_ignores_latest_mirror(
    store: SessionStore, base_events: list[dict]
) -> None:
    _save_branch_msgs_a(store)
    store.conversation["alice"] = [{"role": "user", "content": "latest_B"}]

    exact = _prepare(store, base_events, selected={1: 1, 2: 1})
    assert exact is not None and exact["selected"] == {1: 1, 2: 1}
    assert [m["content"] for m in exact["messages"]] == [
        "snap_u1",
        "snap_a1",
        "snap_u2_a",
        "snap_a2",
    ]


@pytest.mark.parametrize("empty", [False, True])
def test_prepare_snapshot_uses_preloaded_records_without_reading_store(
    store, base_events, monkeypatch, empty
):
    _save_branch_msgs_a(store)
    records = [] if empty else load_records(store, "alice")
    original_records = copy.deepcopy(records)

    def forbidden(*args, **kwargs):
        raise AssertionError("Preloaded records must not trigger another read")

    monkeypatch.setattr(snapshot_history, "load_records", forbidden)
    outcome = prepare_snapshot(
        store,
        "alice",
        base_events,
        {1: 1, 2: 1},
        records=records,
        replay=replay_with_settled_tools,
        needs_replay=snapshot_needs_settled_tool_replay,
    )
    if empty:
        assert outcome is None
    else:
        assert outcome["messages"] == _MSGS_A
        outcome["messages"][0]["content"] = "changed result"
    assert records == original_records
    assert store.state["alice:branch_snapshots"][0]["messages"] == _MSGS_A


@pytest.mark.parametrize(
    "kind",
    [
        "text_chunk",
        "text",
        "assistant_reasoning",
        "assistant_message",
        "assistant_image",
    ],
)
def test_preloaded_records_preserve_late_same_turn_invalidation(
    store, base_events, kind
):
    _save_branch_msgs_a(store)
    records = load_records(store, "alice")
    later = base_events + [
        {
            "event_id": 7,
            "type": kind,
            "content": "late",
            "reasoning_content": "late reasoning",
            "turn_index": 2,
            "branch_id": 1,
            "parent_branch_path": [[1, 1]],
        }
    ]
    assert (
        prepare_snapshot(
            store,
            "alice",
            later,
            {1: 1, 2: 1},
            records=records,
            replay=replay_with_settled_tools,
            needs_replay=snapshot_needs_settled_tool_replay,
        )
        is None
    )


def test_prepare_snapshot_compatible_prefix_trim(
    store: SessionStore, base_events: list[dict]
) -> None:
    _save_branch_msgs_a(store)
    trimmed = _prepare(store, base_events, selected={1: 1, 2: 2}, before_turn=2)
    assert trimmed is not None
    assert [m["content"] for m in trimmed["messages"]] == ["snap_u1", "snap_a1"]


@pytest.mark.parametrize("kind", ["text_chunk", "text", "assistant_reasoning"])
def test_prepare_snapshot_same_turn_late_invalidation_and_replay_fallback(
    store: SessionStore, base_events: list[dict], kind: str
) -> None:
    _save_branch_msgs_a(store)
    later = base_events + [
        {
            "event_id": 7,
            "type": kind,
            "content": "later fragment",
            "reasoning_content": "later reasoning",
            "turn_index": 2,
            "branch_id": 1,
            "parent_branch_path": [[1, 1]],
        }
    ]
    assert _prepare(store, later, {1: 1, 2: 1}) is None

    replayed = replay_with_settled_tools(later, branch_view={1: 1, 2: 1})
    if kind == "assistant_reasoning":
        assert replayed[-1]["reasoning_content"] == "later reasoning"
    else:
        assert replayed[-1]["content"] == "raw_a2_alater fragment"


def test_prepare_snapshot_cleared_system_accepted(store: SessionStore) -> None:
    store.save_branch_snapshot(
        "alice",
        _EMPTY_SYSTEM,
        branch={"turn_index": 2, "branch_id": 1, "parent_branch_path": [[1, 1]]},
        watermark=8,
    )
    outcome = _prepare(store, _CONSUMED_EVENTS, {1: 1, 2: 1})
    assert outcome is not None
    assert outcome["messages"] == _EMPTY_SYSTEM


def test_prepare_snapshot_cleared_exact_empty_accepted(store: SessionStore) -> None:
    store.save_branch_snapshot(
        "alice",
        [],
        branch={"turn_index": 2, "branch_id": 1, "parent_branch_path": [[1, 1]]},
        watermark=8,
    )
    outcome = _prepare(store, _CONSUMED_EVENTS, {1: 1, 2: 1})
    assert outcome is not None
    assert outcome["messages"] == []


def test_prepare_snapshot_cleared_foreign_message_rejected(store: SessionStore) -> None:
    foreign = _EMPTY_SYSTEM + [
        {
            "role": "user",
            "content": "foreign",
            "metadata": {"turn_index": 2, "branch_id": 2},
        }
    ]
    store.save_branch_snapshot(
        "alice",
        foreign,
        branch={"turn_index": 2, "branch_id": 1, "parent_branch_path": [[1, 1]]},
        watermark=8,
    )
    assert _prepare(store, _CONSUMED_EVENTS, {1: 1, 2: 1}) is None


def test_prepare_snapshot_cleared_cross_branch_prefix_rejected(
    store: SessionStore,
) -> None:
    store.save_branch_snapshot(
        "alice",
        _EMPTY_SYSTEM,
        branch={"turn_index": 2, "branch_id": 1, "parent_branch_path": [[1, 1]]},
        watermark=8,
    )
    assert _prepare(store, _CONSUMED_EVENTS, {1: 1, 2: 2}, before_turn=2) is None


@pytest.mark.parametrize(
    ("before_turn", "expected_messages", "expected_cutoff"),
    [
        (3, _EMPTY_SYSTEM, 8),
        (4, _RETAINED_CUTOFF_MESSAGES[:-1], 8),
        (1, None, None),
        (2, None, None),
    ],
)
def test_prepare_snapshot_boundary_before_turn(
    cleared_context: tuple[SessionStore, list[dict], list[dict]],
    before_turn: int,
    expected_messages: list[dict] | None,
    expected_cutoff: int | None,
) -> None:
    store, events, _ = cleared_context
    outcome = _prepare(store, events, {1: 1, 2: 1, 3: 1, 4: 1}, before_turn=before_turn)
    if expected_messages is None:
        assert outcome is None
    else:
        assert outcome is not None
        assert outcome["messages"] == expected_messages
        assert outcome["context_cutoff_event_id"] == expected_cutoff


@pytest.mark.parametrize("before_turn", [3, 4])
def test_prepare_snapshot_cache_miss_preserves_compatible_boundary(
    cleared_context, before_turn
):
    store, events, retained = cleared_context
    store.save_branch_snapshot(
        "alice",
        [
            {
                "role": "assistant",
                "content": "[Previous context summary (compact round 1)] opaque",
                "metadata": {"compact_summary": True},
            }
        ]
        + retained,
        branch={
            "turn_index": 4,
            "branch_id": 1,
            "parent_branch_path": [[1, 1], [2, 1], [3, 1]],
            "context_cutoff_event_id": 8,
        },
        watermark=11,
    )
    prepared = _prepare(store, events, {1: 1, 2: 1, 3: 1, 4: 1}, before_turn)
    expected = retained[:-1]
    expected[1]["metadata"]["event_id"] = 9
    assert prepared["messages"] == (_EMPTY_SYSTEM if before_turn == 3 else expected)
    assert prepared["context_cutoff_event_id"] == 8


def test_prepare_snapshot_rejects_conflicting_prefloor_ancestor_branch(
    cleared_context: tuple[SessionStore, list[dict], list[dict]],
) -> None:
    store, events, _ = cleared_context
    outcome = _prepare(store, events, {1: 1, 2: 2, 3: 1, 4: 1}, before_turn=4)
    assert outcome is None


@pytest.mark.parametrize("pending", [False, True])
def test_prepare_snapshot_late_tool_result_repair(
    store: SessionStore, pending: bool
) -> None:
    _save_cutoff_ack_snapshot(store, pending)
    repaired = _prepare(store, _LATER_TOOL_EVENTS, {1: 1, 2: 1, 3: 1, 4: 1})
    assert repaired is not None
    assert [m["content"] for m in repaired["messages"] if m["role"] == "user"] == [
        "NEW1",
        "NEW2",
    ]
    assert [m["content"] for m in repaired["messages"] if m["role"] == "tool"] == [
        "new final result"
    ]
    assert repaired["context_cutoff_event_id"] == 8


@pytest.mark.parametrize("pending", [False, True])
def test_prepare_snapshot_target_exclusion_preserves_ancestor_result(
    store: SessionStore, pending: bool
) -> None:
    _save_cutoff_ack_snapshot(store, pending)
    prior = _prepare(store, _TARGET_JOB_EVENTS, {1: 1, 2: 1, 3: 1, 4: 1}, before_turn=4)
    assert prior is not None
    assert [m["content"] for m in prior["messages"] if m["role"] == "user"] == ["NEW1"]
    assert [m["content"] for m in prior["messages"] if m["role"] == "tool"] == [
        "new final result"
    ]
    assert prior["context_cutoff_event_id"] == 8


@pytest.mark.parametrize(
    ("compact_source", "before_turn", "selected", "expect_ok"),
    [
        (None, None, {1: 1, 2: 1}, True),
        (None, 2, {1: 1, 2: 2}, False),
        ([[1, 1]], 2, {1: 1, 2: 2}, True),
        ([[1, 1], [2, 1]], 2, {1: 1, 2: 2}, False),
    ],
)
def test_prepare_snapshot_summary_lineage_matrix(
    store: SessionStore, compact_source, before_turn, selected, expect_ok
) -> None:
    meta = {"compact_summary": True}
    if compact_source is not None:
        meta["compact_source_path"] = compact_source
    msgs = [
        {
            "role": "assistant",
            "content": "[Previous context summary (compact round 1)] s",
            "metadata": meta,
        },
        {
            "role": "user",
            "content": "snap_u2_a",
            "metadata": {"turn_index": 2, "branch_id": 1},
        },
    ]
    store.save_branch_snapshot(
        "alice",
        msgs,
        branch={"turn_index": 2, "branch_id": 1, "parent_branch_path": [[1, 1]]},
        watermark=4,
    )
    res = _prepare(store, _EVENTS, selected=selected, before_turn=before_turn)
    assert (res is not None) == expect_ok


_SUMMARY_BODY = "proven summary body"
_SUMMARY_MESSAGE = {
    "role": "assistant",
    "content": f"[Previous context summary (compact round 1)]\n\n{_SUMMARY_BODY}",
    "metadata": {
        "compact_summary": True,
        "compact_source_path": None,
        "compact_result_event_ids": [],
    },
}


def _completion(
    event_id: int,
    *,
    path=((1, 1), (2, 1)),
    replaced=(1, 2),
    result_ids=(),
) -> dict:
    """One persisted compact completion whose source path stayed unknown."""
    return {
        "event_id": event_id,
        "type": "compact_complete",
        "round": 1,
        "summary": _SUMMARY_BODY,
        "compact_path": [[turn, branch] for turn, branch in path],
        "compact_source_path": None,
        "compact_result_event_ids": list(result_ids),
        "replaced_from_event_id": replaced[0],
        "replaced_to_event_id": replaced[1],
        "turn_index": 2,
        "branch_id": 1,
    }


def _unknown_summary_events(
    *,
    completions: tuple[dict, ...] | None = None,
    target_id: int = 6,
    hidden_job: bool = False,
) -> list[dict]:
    """A compacted turns 1-2 lineage, then an original and an edited turn 3."""
    zone = (
        [
            {
                "event_id": 1,
                "type": "user_message",
                "content": "raw_u1",
                "turn_index": 1,
                "branch_id": 1,
            },
            {
                "event_id": 2,
                "type": "tool_call",
                "call_id": "old-job",
                "name": "echo",
                "args": {},
                "turn_index": 1,
                "branch_id": 1,
            },
            {
                "event_id": 3,
                "type": "user_message",
                "content": "raw_u2",
                "turn_index": 2,
                "branch_id": 1,
                "parent_branch_path": [[1, 1]],
            },
            {
                "event_id": 4,
                "type": "tool_result",
                "call_id": "old-job",
                "name": "echo",
                "output": "old summarized result",
                "turn_index": 2,
                "branch_id": 1,
                "parent_branch_path": [[1, 1]],
            },
        ]
        if hidden_job
        else [
            {
                "event_id": 1,
                "type": "user_message",
                "content": "raw_u1",
                "turn_index": 1,
                "branch_id": 1,
            },
            {
                "event_id": 2,
                "type": "text",
                "content": "raw_a1",
                "turn_index": 1,
                "branch_id": 1,
            },
            {
                "event_id": 3,
                "type": "user_message",
                "content": "raw_u2",
                "turn_index": 2,
                "branch_id": 1,
                "parent_branch_path": [[1, 1]],
            },
            {
                "event_id": 4,
                "type": "text",
                "content": "raw_a2",
                "turn_index": 2,
                "branch_id": 1,
                "parent_branch_path": [[1, 1]],
            },
        ]
    )
    if completions is None:
        completions = (_completion(5, replaced=(1, zone[-1]["event_id"])),)
    events = copy.deepcopy(zone) + [copy.deepcopy(event) for event in completions]
    events.extend(
        [
            {
                "event_id": target_id,
                "type": "user_message",
                "content": "orig_u3",
                "turn_index": 3,
                "branch_id": 1,
                "parent_branch_path": [[1, 1], [2, 1]],
            },
            {
                "event_id": target_id + 1,
                "type": "text",
                "content": "orig_a3",
                "turn_index": 3,
                "branch_id": 1,
                "parent_branch_path": [[1, 1], [2, 1]],
            },
            {
                "event_id": target_id + 2,
                "type": "user_message",
                "content": "edited_u3",
                "turn_index": 3,
                "branch_id": 2,
                "parent_branch_path": [[1, 1], [2, 1]],
            },
        ]
    )
    return events


def _save_unknown_summary(
    store: SessionStore, watermark: int, messages: list[dict] | None = None
) -> None:
    store.save_branch_snapshot(
        "alice",
        copy.deepcopy(messages if messages is not None else [_SUMMARY_MESSAGE]),
        branch={"turn_index": 2, "branch_id": 1, "parent_branch_path": [[1, 1]]},
        watermark=watermark,
    )


def test_prepare_snapshot_reuses_proven_unknown_summary_tail(
    store: SessionStore,
) -> None:
    _save_unknown_summary(store, 5)
    outcome = _prepare(
        store, _unknown_summary_events(), {1: 1, 2: 1, 3: 1}, before_turn=3
    )
    assert outcome is not None
    assert outcome["messages"] == [_SUMMARY_MESSAGE]


def test_prepare_snapshot_reuse_covers_summarized_call_and_result(
    store: SessionStore,
) -> None:
    events = _unknown_summary_events(
        completions=(_completion(5, replaced=(1, 4), result_ids=[4]),),
        hidden_job=True,
    )
    _save_unknown_summary(store, 5)
    outcome = _prepare(store, events, {1: 1, 2: 1, 3: 1}, before_turn=3)
    assert outcome is not None
    assert outcome["messages"] == [_SUMMARY_MESSAGE]


def test_prepare_snapshot_reuse_covers_explicitly_consumed_result(
    store: SessionStore,
) -> None:
    events = [
        {
            "event_id": 1,
            "type": "user_message",
            "content": "raw_u1",
            "turn_index": 1,
            "branch_id": 1,
        },
        {
            "event_id": 2,
            "type": "tool_call",
            "call_id": "late-job",
            "name": "echo",
            "args": {},
            "turn_index": 1,
            "branch_id": 1,
        },
        {
            "event_id": 3,
            "type": "user_message",
            "content": "raw_u2",
            "turn_index": 2,
            "branch_id": 1,
            "parent_branch_path": [[1, 1]],
        },
        {
            "event_id": 4,
            "type": "tool_result",
            "call_id": "late-job",
            "name": "echo",
            "output": "consumed result",
            "turn_index": 2,
            "branch_id": 1,
            "parent_branch_path": [[1, 1]],
        },
        _completion(5, replaced=(1, 4), result_ids=[4]),
        {
            "event_id": 6,
            "type": "user_message",
            "content": "orig_u3",
            "turn_index": 3,
            "branch_id": 1,
            "parent_branch_path": [[1, 1], [2, 1]],
        },
        {
            "event_id": 7,
            "type": "user_message",
            "content": "edited_u3",
            "turn_index": 3,
            "branch_id": 2,
            "parent_branch_path": [[1, 1], [2, 1]],
        },
    ]
    messages = [
        _SUMMARY_MESSAGE,
        {
            "role": "user",
            "content": "raw_u2",
            "metadata": {"turn_index": 2, "branch_id": 1},
        },
    ]
    _save_unknown_summary(store, 5, messages)
    outcome = _prepare(store, events, {1: 1, 2: 1, 3: 1}, before_turn=3)
    assert outcome is not None
    assert outcome["messages"] == messages


@pytest.mark.parametrize(
    "injected",
    [None, {"turn_index": 2, "branch_id": 2}, {"turn_index": 3, "branch_id": 1}],
)
def test_prepare_snapshot_rejects_unproven_summary_injection(
    store: SessionStore, injected: dict | None
) -> None:
    messages = [
        _SUMMARY_MESSAGE,
        {
            "role": "user",
            "content": "raw_u2",
            "metadata": {"turn_index": 2, "branch_id": 1},
        },
    ]
    if injected is not None:
        messages.append(
            {"role": "assistant", "content": "injected", "metadata": dict(injected)}
        )
    _save_unknown_summary(store, 5, messages)
    outcome = _prepare(
        store, _unknown_summary_events(), {1: 1, 2: 1, 3: 1}, before_turn=3
    )
    assert (outcome is not None) == (injected is None)


def test_prepare_snapshot_rejects_changed_ancestor_of_unknown_summary(
    store: SessionStore,
) -> None:
    _save_unknown_summary(store, 5)
    events = _unknown_summary_events()
    events[3] = {**events[3], "branch_id": 2}
    assert _prepare(store, events, {1: 1, 2: 2, 3: 1}, before_turn=3) is None


def test_prepare_snapshot_rejects_unknown_summary_from_sibling_branch(
    store: SessionStore,
) -> None:
    events = _unknown_summary_events(
        completions=(_completion(5, path=((1, 1), (2, 2)), replaced=(1, 4)),)
    )
    _save_unknown_summary(store, 5)
    assert _prepare(store, events, {1: 1, 2: 1, 3: 1}, before_turn=3) is None


def test_prepare_snapshot_rejects_unknown_summary_completed_after_target(
    store: SessionStore,
) -> None:
    events = _unknown_summary_events(completions=(), target_id=5)
    events.append(_completion(8, replaced=(1, 4)))
    _save_unknown_summary(store, 8)
    assert _prepare(store, events, {1: 1, 2: 1, 3: 1}, before_turn=3) is None


def test_prepare_snapshot_rejects_ambiguous_unknown_summary_completion(
    store: SessionStore,
) -> None:
    events = _unknown_summary_events(
        completions=(
            _completion(5, replaced=(1, 4)),
            _completion(6, replaced=(1, 4)),
        ),
        target_id=7,
    )
    _save_unknown_summary(store, 6)
    assert _prepare(store, events, {1: 1, 2: 1, 3: 1}, before_turn=3) is None


@pytest.mark.parametrize("consumed", [True, False])
def test_prepare_snapshot_keeps_proven_call_for_late_unconsumed_result(
    store: SessionStore, consumed: bool
) -> None:
    """A covered call keeps ownership of a later result the proof did not consume.

    The checkpoint sits on a real turn-2 lineage, so the record passes raw-pairs
    admission and the proof is actually consulted instead of being dropped
    before it runs. ``compact_result_event_ids`` is the single changed fact:
    consumed, the later result is exempt and the checkpoint is reused;
    unconsumed, the late result stays visible to ``needs_replay`` and the
    checkpoint is refused rather than silently losing its output.
    """
    events = [
        {
            "event_id": 1,
            "type": "user_message",
            "content": "raw_u1",
            "turn_index": 1,
            "branch_id": 1,
        },
        {
            "event_id": 2,
            "type": "tool_call",
            "call_id": "late",
            "name": "echo",
            "args": {},
            "turn_index": 1,
            "branch_id": 1,
        },
        {
            "event_id": 3,
            "type": "user_message",
            "content": "raw_u2",
            "turn_index": 2,
            "branch_id": 1,
            "parent_branch_path": [[1, 1]],
        },
        {
            "event_id": 4,
            "type": "tool_result",
            "call_id": "late",
            "name": "echo",
            "output": "arrived a turn later",
            "turn_index": 2,
            "branch_id": 1,
            "parent_branch_path": [[1, 1]],
        },
        _completion(5, replaced=(1, 2), result_ids=[4] if consumed else []),
        {
            "event_id": 6,
            "type": "user_message",
            "content": "orig_u3",
            "turn_index": 3,
            "branch_id": 1,
            "parent_branch_path": [[1, 1], [2, 1]],
        },
        {
            "event_id": 7,
            "type": "user_message",
            "content": "edited_u3",
            "turn_index": 3,
            "branch_id": 2,
            "parent_branch_path": [[1, 1], [2, 1]],
        },
    ]
    _save_unknown_summary(store, 5)
    outcome = _prepare(store, events, {1: 1, 2: 1, 3: 1}, before_turn=3)
    if consumed:
        assert outcome is not None
        assert outcome["messages"] == [_SUMMARY_MESSAGE]
    else:
        assert outcome is None


@pytest.mark.parametrize("orphan", [True, False])
def test_prepare_snapshot_rejects_uncovered_unfinished_call_with_proof(
    store: SessionStore, orphan: bool
) -> None:
    """A distill proof never exempts an unrelated call that has no result.

    The turn-2 user message makes the record admissible so the proof path runs.
    The orphan call is the single changed fact: present, it is neither covered
    by the proof nor proved consumed, so the checkpoint is refused; removed,
    the same checkpoint is reused.
    """
    events = [
        {
            "event_id": 1,
            "type": "user_message",
            "content": "raw_u1",
            "turn_index": 1,
            "branch_id": 1,
        },
        {
            "event_id": 2,
            "type": "text",
            "content": "raw_a1",
            "turn_index": 1,
            "branch_id": 1,
        },
        {
            "event_id": 3,
            "type": "user_message",
            "content": "raw_u2",
            "turn_index": 2,
            "branch_id": 1,
            "parent_branch_path": [[1, 1]],
        },
        {
            "event_id": 4,
            "type": "text",
            "content": "raw_a2",
            "turn_index": 2,
            "branch_id": 1,
            "parent_branch_path": [[1, 1]],
        },
        _completion(5, replaced=(1, 4)),
    ]
    if orphan:
        events.append(
            {
                "event_id": 6,
                "type": "tool_call",
                "call_id": "orphan",
                "name": "echo",
                "args": {},
                "turn_index": 2,
                "branch_id": 1,
                "parent_branch_path": [[1, 1]],
            }
        )
    events.extend(
        [
            {
                "event_id": 7,
                "type": "user_message",
                "content": "orig_u3",
                "turn_index": 3,
                "branch_id": 1,
                "parent_branch_path": [[1, 1], [2, 1]],
            },
            {
                "event_id": 8,
                "type": "user_message",
                "content": "edited_u3",
                "turn_index": 3,
                "branch_id": 2,
                "parent_branch_path": [[1, 1], [2, 1]],
            },
        ]
    )
    _save_unknown_summary(store, 6)
    outcome = _prepare(store, events, {1: 1, 2: 1, 3: 1}, before_turn=3)
    if orphan:
        assert outcome is None
    else:
        assert outcome is not None
        assert outcome["messages"] == [_SUMMARY_MESSAGE]


def test_prepare_snapshot_consumed_result_exempts_call_outside_raw_range(
    store: SessionStore,
) -> None:
    """An explicitly consumed result justifies its producer call's absence."""
    events = [
        {
            "event_id": 1,
            "type": "user_message",
            "content": "raw_u1",
            "turn_index": 1,
            "branch_id": 1,
        },
        {
            "event_id": 2,
            "type": "text",
            "content": "raw_a1",
            "turn_index": 1,
            "branch_id": 1,
        },
        {
            "event_id": 3,
            "type": "user_message",
            "content": "raw_u2",
            "turn_index": 2,
            "branch_id": 1,
            "parent_branch_path": [[1, 1]],
        },
        {
            "event_id": 4,
            "type": "text",
            "content": "raw_a2",
            "turn_index": 2,
            "branch_id": 1,
            "parent_branch_path": [[1, 1]],
        },
        {
            "event_id": 5,
            "type": "tool_call",
            "call_id": "outside-job",
            "name": "echo",
            "args": {},
            "turn_index": 1,
            "branch_id": 1,
        },
        {
            "event_id": 6,
            "type": "tool_result",
            "call_id": "outside-job",
            "name": "echo",
            "output": "consumed outside the raw range",
            "turn_index": 2,
            "branch_id": 1,
            "parent_branch_path": [[1, 1]],
        },
        _completion(7, replaced=(1, 2), result_ids=[6]),
        {
            "event_id": 8,
            "type": "user_message",
            "content": "orig_u3",
            "turn_index": 3,
            "branch_id": 1,
            "parent_branch_path": [[1, 1], [2, 1]],
        },
        {
            "event_id": 9,
            "type": "user_message",
            "content": "edited_u3",
            "turn_index": 3,
            "branch_id": 2,
            "parent_branch_path": [[1, 1], [2, 1]],
        },
    ]
    _save_unknown_summary(store, 7)
    outcome = _prepare(store, events, {1: 1, 2: 1, 3: 1}, before_turn=3)
    assert outcome is not None
    assert outcome["messages"] == [_SUMMARY_MESSAGE]


def test_prepare_snapshot_rejects_truncated_record_path(
    store: SessionStore,
) -> None:
    """A record path missing an earlier turn cannot weaken the summary proof."""
    events = _unknown_summary_events(
        completions=(_completion(5, path=((2, 1),), replaced=(1, 4)),)
    )
    store.save_branch_snapshot(
        "alice",
        copy.deepcopy([_SUMMARY_MESSAGE]),
        branch={"turn_index": 2, "branch_id": 1, "parent_branch_path": []},
        watermark=5,
    )
    assert _prepare(store, events, {1: 1, 2: 1, 3: 1}, before_turn=3) is None


@pytest.mark.parametrize("covers_extra_turn", [False, True])
def test_prepare_snapshot_completion_must_stay_inside_record_pretarget(
    store: SessionStore, covers_extra_turn: bool
) -> None:
    """A short checkpoint may not borrow a completion from turns it never held.

    The record only captured turns 1-2 (watermark 4) yet is reused for a turn-4
    edit. ``compact_path`` is the single changed fact: inside the captured
    ancestry the summary is proven and the turn-3 tail replays; one turn
    further it is unprovable, otherwise the proof's ``last_turn`` would drop the
    intervening turn-3 content the record never held.
    """
    compact_path = [(1, 1), (2, 1)] + ([(3, 1)] if covers_extra_turn else [])
    events = [
        {
            "event_id": 1,
            "type": "user_message",
            "content": "raw_u1",
            "turn_index": 1,
            "branch_id": 1,
        },
        {
            "event_id": 2,
            "type": "user_message",
            "content": "raw_u2",
            "turn_index": 2,
            "branch_id": 1,
            "parent_branch_path": [[1, 1]],
        },
        {
            "event_id": 3,
            "type": "user_message",
            "content": "raw_u3",
            "turn_index": 3,
            "branch_id": 1,
            "parent_branch_path": [[1, 1], [2, 1]],
        },
        _completion(4, path=compact_path, replaced=(1, 2)),
        {
            "event_id": 5,
            "type": "user_message",
            "content": "orig_u4",
            "turn_index": 4,
            "branch_id": 1,
            "parent_branch_path": [[1, 1], [2, 1], [3, 1]],
        },
        {
            "event_id": 6,
            "type": "user_message",
            "content": "edited_u4",
            "turn_index": 4,
            "branch_id": 2,
            "parent_branch_path": [[1, 1], [2, 1], [3, 1]],
        },
    ]
    _save_unknown_summary(store, 4)
    outcome = _prepare(store, events, {1: 1, 2: 1, 3: 1, 4: 1}, before_turn=4)
    if covers_extra_turn:
        assert outcome is None
    else:
        assert outcome is not None
        assert [m["content"] for m in outcome["messages"]] == [
            _SUMMARY_MESSAGE["content"],
            "raw_u3",
        ]


def test_prepare_snapshot_rejects_contradictory_completion_parent(
    store: SessionStore,
) -> None:
    """A completion whose parents sit on a sibling branch proves nothing."""
    completion = _completion(5, replaced=(1, 4))
    completion["parent_branch_path"] = [[1, 2]]
    _save_unknown_summary(store, 5)
    events = _unknown_summary_events(completions=(completion,))
    assert _prepare(store, events, {1: 1, 2: 1, 3: 1}, before_turn=3) is None


def test_prepare_snapshot_rejects_completion_with_foreign_coordinates(
    store: SessionStore,
) -> None:
    """Completion coordinates, not just the compact path, must be selected."""
    completion = _completion(5, replaced=(1, 4))
    completion["turn_index"] = 1
    completion["branch_id"] = 2
    _save_unknown_summary(store, 5)
    events = _unknown_summary_events(completions=(completion,))
    assert _prepare(store, events, {1: 1, 2: 1, 3: 1}, before_turn=3) is None


@pytest.mark.parametrize("result_ids", [[0], [2], [9]])
def test_prepare_snapshot_rejects_consumed_id_that_is_not_a_prior_result(
    store: SessionStore, result_ids: list[int]
) -> None:
    """A consumed identity must be a real, prior ``tool_result``."""
    events = _unknown_summary_events(
        completions=(_completion(5, replaced=(1, 4), result_ids=result_ids),)
    )
    _save_unknown_summary(store, 5)
    assert _prepare(store, events, {1: 1, 2: 1, 3: 1}, before_turn=3) is None


def test_prepare_snapshot_rejects_consumed_result_with_sibling_owner(
    store: SessionStore,
) -> None:
    """A consumed result is only valid when its producing call is on lineage."""
    events = _unknown_summary_events(
        completions=(_completion(5, replaced=(1, 4), result_ids=[4]),),
        hidden_job=True,
    )
    events[1] = {**events[1], "branch_id": 2}

    _save_unknown_summary(store, 5)
    assert _prepare(store, events, {1: 1, 2: 1, 3: 1}, before_turn=3) is None


def test_prepare_snapshot_reuses_retained_unknown_origin_pretarget(
    store: SessionStore,
) -> None:
    """A pretarget batch of unknown job origin is carried verbatim, not discarded.

    ``origin_known`` only says the folded job identities are unknown; the
    message's own turn is immutable before the edit, and it can only have folded
    results issued before that turn, so the whole checkpoint is reusable instead
    of being thrown away.
    """
    messages = [
        _SUMMARY_MESSAGE,
        {
            "role": "user",
            "content": "raw_u2",
            "metadata": {"turn_index": 2, "branch_id": 1, "origin_known": False},
        },
    ]
    _save_unknown_summary(store, 5, messages)
    outcome = _prepare(
        store, _unknown_summary_events(), {1: 1, 2: 1, 3: 1}, before_turn=3
    )
    assert outcome is not None
    assert outcome["messages"] == messages


def test_prepare_snapshot_excludes_unknown_origin_edited_target(
    store: SessionStore,
) -> None:
    """The edited target's own unknown origin must not veto the whole prefix.

    The turn-2 message that is being replaced carries ``origin_known`` False.
    It is dropped from the retained prefix by position, so the immutable turn-1
    prefix is still reused instead of the record being rejected outright.
    """
    events = [
        {
            "event_id": 1,
            "type": "user_message",
            "content": "raw_u1",
            "turn_index": 1,
            "branch_id": 1,
        },
        {
            "event_id": 2,
            "type": "text",
            "content": "raw_a1",
            "turn_index": 1,
            "branch_id": 1,
        },
        {
            "event_id": 3,
            "type": "user_message",
            "content": "orig_u2",
            "turn_index": 2,
            "branch_id": 1,
            "parent_branch_path": [[1, 1]],
        },
        {
            "event_id": 4,
            "type": "user_message",
            "content": "edited_u2",
            "turn_index": 2,
            "branch_id": 2,
            "parent_branch_path": [[1, 1]],
        },
    ]
    messages = [
        {
            "role": "user",
            "content": "raw_u1",
            "metadata": {"turn_index": 1, "branch_id": 1},
        },
        {"role": "assistant", "content": "raw_a1"},
        {
            "role": "user",
            "content": "orig_u2",
            "metadata": {"turn_index": 2, "branch_id": 1, "origin_known": False},
        },
    ]
    store.save_branch_snapshot(
        "alice",
        copy.deepcopy(messages),
        branch={"turn_index": 2, "branch_id": 1, "parent_branch_path": [[1, 1]]},
        watermark=3,
    )
    outcome = _prepare(store, events, {1: 1, 2: 2}, before_turn=2)
    assert outcome is not None
    assert outcome["messages"] == messages[:2]


def test_prepare_snapshot_rejects_unpositioned_unknown_origin(
    store: SessionStore,
) -> None:
    """Unknown origin with no provable turn is still refused, so the guard stays."""
    messages = [
        _SUMMARY_MESSAGE,
        {"role": "user", "content": "raw_u2", "metadata": {"origin_known": False}},
    ]
    _save_unknown_summary(store, 5, messages)
    assert (
        _prepare(store, _unknown_summary_events(), {1: 1, 2: 1, 3: 1}, before_turn=3)
        is None
    )


def test_prepare_snapshot_rejects_unpositioned_injected_assistant(
    store: SessionStore,
) -> None:
    """An assistant without an open user turn makes no lineage claim."""
    messages = [_SUMMARY_MESSAGE, {"role": "assistant", "content": "injected"}]
    _save_unknown_summary(store, 5, messages)
    assert (
        _prepare(store, _unknown_summary_events(), {1: 1, 2: 1, 3: 1}, before_turn=3)
        is None
    )


def test_prepare_snapshot_accepts_assistant_inheriting_open_user(
    store: SessionStore,
) -> None:
    """A normal assistant continuation inherits the open user's position."""
    messages = [
        _SUMMARY_MESSAGE,
        {
            "role": "user",
            "content": "raw_u2",
            "metadata": {"turn_index": 2, "branch_id": 1, "origin_known": True},
        },
        {"role": "assistant", "content": "snap_a2"},
    ]
    _save_unknown_summary(store, 5, messages)
    outcome = _prepare(
        store, _unknown_summary_events(), {1: 1, 2: 1, 3: 1}, before_turn=3
    )
    assert outcome is not None
    assert outcome["messages"] == messages


def test_prepare_snapshot_invalid_record_does_not_fall_back_to_repair(
    store: SessionStore,
) -> None:
    """A contradictory record is dropped, not repaired from its cutoff."""
    completion = _completion(5, replaced=(1, 4))
    completion["parent_branch_path"] = [[1, 2]]
    events = _unknown_summary_events(completions=(completion,))
    store.save_branch_snapshot(
        "alice",
        copy.deepcopy([_SUMMARY_MESSAGE]),
        branch={
            "turn_index": 2,
            "branch_id": 1,
            "parent_branch_path": [[1, 1]],
            "context_cutoff_event_id": 5,
        },
        watermark=5,
    )
    assert _prepare(store, events, {1: 1, 2: 1, 3: 1}, before_turn=3) is None


def test_ordinary_reload_reuses_unknown_summary_with_a_live_tail(
    store: SessionStore,
) -> None:
    _save_unknown_summary(store, 5)
    outcome = _prepare(store, _unknown_summary_events(), {1: 1, 2: 1, 3: 1})
    assert outcome is not None
    contents = [message.get("content") for message in outcome["messages"]]
    assert _SUMMARY_MESSAGE in outcome["messages"]
    assert "orig_u3" in contents
    assert "raw_u1" not in contents


def test_prepare_snapshot_rejects_a_call_settled_before_the_watermark(
    store: SessionStore,
) -> None:
    """Settled-before-the-snapshot is not a proof of consumed coverage.

    Exempting such a call lets the replayed prompt lose a tool call the turn
    still needs, so the incident's uncovered calls need a real completion.
    """
    events = _unknown_summary_events(completions=(_completion(5, replaced=(1, 4)),))
    events.insert(
        1,
        {
            "event_id": 9,
            "type": "tool_call",
            "call_id": "old-job",
            "name": "echo",
            "args": {},
            "turn_index": 1,
            "branch_id": 1,
        },
    )
    events.insert(
        2,
        {
            "event_id": 10,
            "type": "tool_result",
            "call_id": "old-job",
            "name": "echo",
            "output": "old output",
            "turn_index": 1,
            "branch_id": 1,
        },
    )
    _save_unknown_summary(store, 10)
    assert _prepare(store, events, {1: 1, 2: 1, 3: 1}, before_turn=3) is None


def _two_call_round(start_id: int, end_id: int, *call_ids: str) -> list[dict]:
    lineage = {"turn_index": 1, "branch_id": 1}
    return [
        {"event_id": start_id, "type": "assistant_round_start", **lineage},
        {
            "event_id": end_id,
            "type": "assistant_message",
            "round_start_event_id": start_id,
            "message": {
                "role": "assistant",
                "content": "CHUNKED ANSWER",
                "tool_calls": [
                    {
                        "id": call_id,
                        "type": "function",
                        "function": {"name": "echo", "arguments": "{}"},
                    }
                    for call_id in call_ids
                ],
            },
            **lineage,
        },
    ]


def _sparse_events(rounds: list[list[dict]], result_id: int) -> list[dict]:
    """A compacted turns 1-2 lineage plus caller-supplied canonical rounds."""
    events = [
        {
            "event_id": 1,
            "type": "user_message",
            "content": "raw_u1",
            "turn_index": 1,
            "branch_id": 1,
        },
        {
            "event_id": 2,
            "type": "text",
            "content": "raw_a1",
            "turn_index": 1,
            "branch_id": 1,
        },
        {
            "event_id": 3,
            "type": "user_message",
            "content": "raw_u2",
            "turn_index": 2,
            "branch_id": 1,
            "parent_branch_path": [[1, 1]],
        },
        {
            "event_id": 4,
            "type": "text",
            "content": "raw_a2",
            "turn_index": 2,
            "branch_id": 1,
            "parent_branch_path": [[1, 1]],
        },
        _completion(5, replaced=(1, 4)),
    ]
    for round_events in rounds:
        events.extend(round_events)
    events.extend(
        [
            {
                "event_id": 8,
                "type": "tool_call",
                "call_id": "job-9",
                "tool_call_id": "call-9",
                "name": "echo",
                "args": {},
                "turn_index": 1,
                "branch_id": 1,
            },
            {
                "event_id": result_id,
                "type": "tool_result",
                "call_id": "job-9",
                "name": "echo",
                "output": "RESULT_9",
                "turn_index": 1,
                "branch_id": 1,
            },
            {
                "event_id": 10,
                "type": "user_message",
                "content": "orig_u3",
                "turn_index": 3,
                "branch_id": 1,
                "parent_branch_path": [[1, 1], [2, 1]],
            },
            {
                "event_id": 11,
                "type": "text",
                "content": "orig_a3",
                "turn_index": 3,
                "branch_id": 1,
                "parent_branch_path": [[1, 1], [2, 1]],
            },
            {
                "event_id": 12,
                "type": "user_message",
                "content": "edited_u3",
                "turn_index": 3,
                "branch_id": 2,
                "parent_branch_path": [[1, 1], [2, 1]],
            },
        ]
    )
    return events


def test_sparse_repair_restores_a_missing_round_and_its_result(
    store: SessionStore,
) -> None:
    """A missing group with a unique owner and result is restored, not refused."""
    events = _sparse_events([_two_call_round(6, 7, "call-9")], 9)
    _save_unknown_summary(store, 9)
    outcome = _prepare(store, events, {1: 1, 2: 1, 3: 1}, before_turn=3)
    assert outcome is not None
    contents = [message.get("content") for message in outcome["messages"]]
    assert "CHUNKED ANSWER" in contents
    assert "RESULT_9" in contents
    restored = next(m for m in outcome["messages"] if m.get("content") == "RESULT_9")
    assert restored["tool_call_id"] == "call-9"
    again = _prepare(store, events, {1: 1, 2: 1, 3: 1}, before_turn=3)
    assert again is not None
    assert again["messages"] == outcome["messages"]


def test_sparse_repair_refuses_an_ambiguous_round_owner(store: SessionStore) -> None:
    """Both rounds sharing one call predate the watermark and the target.

    Every round member sits below the watermark and the turn-3 target, so the
    only remaining reason to refuse is the duplicated owner.
    """
    events = [
        *copy.deepcopy(_EVENTS[:4]),
        _completion(5, replaced=(1, 4)),
        *_two_call_round(6, 7, "call-9"),
        *_two_call_round(8, 9, "call-9"),
        *_call_pair(10, "call-9", "job-9", "RESULT_9"),
        {
            "event_id": 12,
            "type": "user_message",
            "content": "orig_u3",
            "turn_index": 3,
            "branch_id": 1,
            "parent_branch_path": [[1, 1], [2, 1]],
        },
    ]
    _save_unknown_summary(store, 11)
    assert _prepare(store, events, {1: 1, 2: 1, 3: 1}, before_turn=3) is None


def _call_pair(event_id: int, provider: str, job: str, output: str) -> list[dict]:
    return [
        {
            "event_id": event_id,
            "type": "tool_call",
            "call_id": job,
            "tool_call_id": provider,
            "name": "echo",
            "args": {},
            "turn_index": 1,
            "branch_id": 1,
        },
        {
            "event_id": event_id + 1,
            "type": "tool_result",
            "call_id": job,
            "name": "echo",
            "output": output,
            "turn_index": 1,
            "branch_id": 1,
        },
    ]


def _repair_events(*call_specs: tuple[str, str, str]) -> list[dict]:
    events = _two_call_round(6, 7, *(spec[0] for spec in call_specs))
    for offset, (provider, job, output) in enumerate(call_specs):
        events.extend(_call_pair(8 + 2 * offset, provider, job, output))
    return events


def test_sparse_repair_restores_a_multi_call_round() -> None:
    events = _repair_events(
        ("call-9", "job-9", "RESULT_9"), ("call-10", "job-10", "RESULT_10")
    )
    gaps = [event for event in events if event["type"] == "tool_call"]
    repaired = repair_gap_messages(
        gaps, events, target_event_id=99, present_call_ids=set()
    )
    assert repaired is not None
    assert [message.get("role") for message in repaired] == [
        "assistant",
        "tool",
        "tool",
    ]
    assert [message["tool_call_id"] for message in repaired[1:]] == [
        "call-9",
        "call-10",
    ]


def test_sparse_repair_refuses_a_partially_present_round() -> None:
    events = _repair_events(
        ("call-9", "job-9", "RESULT_9"), ("call-10", "job-10", "RESULT_10")
    )
    gaps = [event for event in events if event["type"] == "tool_call"][:1]
    assert (
        repair_gap_messages(
            gaps, events, target_event_id=99, present_call_ids={"call-10"}
        )
        is None
    )


def test_sparse_repair_refuses_a_duplicated_result() -> None:
    events = _repair_events(("call-9", "job-9", "RESULT_9"))
    events.append(
        {
            "event_id": 20,
            "type": "tool_result",
            "call_id": "job-9",
            "name": "echo",
            "output": "RESULT_9",
            "turn_index": 1,
            "branch_id": 1,
        }
    )
    gaps = [event for event in events if event["type"] == "tool_call"]
    assert (
        repair_gap_messages(gaps, events, target_event_id=99, present_call_ids=set())
        is None
    )


def test_sparse_repair_refuses_a_round_at_or_after_the_target() -> None:
    events = _repair_events(("call-9", "job-9", "RESULT_9"))
    gaps = [event for event in events if event["type"] == "tool_call"]
    assert (
        repair_gap_messages(gaps, events, target_event_id=6, present_call_ids=set())
        is None
    )


def test_sparse_repair_refuses_a_consumed_sibling_call() -> None:
    """A round half-covered by the summary cannot be expanded to its sibling."""
    events = _repair_events(
        ("call-9", "job-9", "RESULT_9"), ("call-10", "job-10", "RESULT_10")
    )
    gaps = [event for event in events if event["type"] == "tool_call"][:1]
    assert (
        repair_gap_messages(
            gaps,
            events,
            target_event_id=99,
            present_call_ids=set(),
            represented_event_ids={10, 11},
        )
        is None
    )


def test_sparse_repair_refuses_a_round_inside_the_cleared_cutoff() -> None:
    events = _repair_events(("call-9", "job-9", "RESULT_9"))
    gaps = [event for event in events if event["type"] == "tool_call"]
    assert (
        repair_gap_messages(
            gaps, events, target_event_id=99, present_call_ids=set(), cutoff=6
        )
        is None
    )


def test_sparse_repair_refuses_a_shared_job() -> None:
    """Two calls naming one job would share a single result."""
    events = _two_call_round(6, 7, "call-9", "call-10")
    events.extend(
        [
            {
                "event_id": 8,
                "type": "tool_call",
                "call_id": "job-shared",
                "tool_call_id": "call-9",
                "name": "echo",
                "args": {},
                "turn_index": 1,
                "branch_id": 1,
            },
            {
                "event_id": 9,
                "type": "tool_call",
                "call_id": "job-shared",
                "tool_call_id": "call-10",
                "name": "echo",
                "args": {},
                "turn_index": 1,
                "branch_id": 1,
            },
            {
                "event_id": 10,
                "type": "tool_result",
                "call_id": "job-shared",
                "name": "echo",
                "output": "RESULT_SHARED",
                "turn_index": 1,
                "branch_id": 1,
            },
        ]
    )
    gaps = [event for event in events if event["type"] == "tool_call"]
    assert (
        repair_gap_messages(gaps, events, target_event_id=99, present_call_ids=set())
        is None
    )


def test_prepare_snapshot_refuses_repair_inside_the_cleared_cutoff() -> None:
    """The production caller must pass the cutoff into the repair."""
    events = _sparse_events([_two_call_round(6, 7, "call-9")], 9)
    store = SimpleNamespace(state={})
    record = {
        "path": [[1, 1], [2, 1]],
        "watermark": 9,
        "messages": [copy.deepcopy(_SUMMARY_MESSAGE)],
        "compact_state": {"compact_count": 0, "last_compact_time": 0.0},
        "context_cutoff_event_id": 6,
    }
    outcome = prepare_snapshot(
        store,
        "alice",
        events,
        {1: 1, 2: 1, 3: 1},
        before_turn=3,
        replay=replay_with_settled_tools,
        needs_replay=snapshot_needs_settled_tool_replay,
        records=[record],
    )
    assert outcome is None or not any(
        message.get("content") == "CHUNKED ANSWER" for message in outcome["messages"]
    ), "a round inside the cleared cutoff must not be restored"


def _covered_end_events() -> tuple[list[dict], dict]:
    """A compact round that claims to have consumed only a canonical end."""
    events = [
        {
            "event_id": 1,
            "type": "user_message",
            "content": "RAW_U1",
            "turn_index": 1,
            "branch_id": 1,
        },
        *_two_call_round(2, 3, "call-9"),
    ]
    dispatch, result = _call_pair(4, "call-9", "job-9", "RESULT_9")
    result["event_id"] = 6
    events.extend(
        [
            dispatch,
            {
                "event_id": 5,
                "type": "compact_complete",
                "turn_index": 1,
                "branch_id": 1,
                "round": 1,
                "summary": _SUMMARY_BODY,
                "compact_path": [[1, 1]],
                "compact_source_path": [[1, 1]],
                "compact_result_event_ids": [],
                "replaced_from_event_id": 3,
                "replaced_to_event_id": 3,
            },
            result,
            {
                "event_id": 7,
                "type": "user_message",
                "content": "TARGET",
                "turn_index": 2,
                "branch_id": 1,
                "parent_branch_path": [[1, 1]],
            },
        ]
    )
    record = {
        "path": [[1, 1]],
        "watermark": 6,
        "messages": [
            {
                "role": "assistant",
                "content": f"[Previous context summary (compact round 1)]\n\n"
                f"{_SUMMARY_BODY}",
                "metadata": {
                    "compact_summary": True,
                    "compact_source_path": [[1, 1]],
                    "compact_result_event_ids": [],
                },
            }
        ],
        "compact_state": {"compact_count": 1, "last_compact_time": 0.0},
    }
    return events, record


def test_prepare_snapshot_refuses_repair_of_a_covered_canonical_end() -> None:
    """A summary covering the canonical end forbids restoring its whole round."""
    events, record = _covered_end_events()
    outcome = prepare_snapshot(
        SimpleNamespace(state={}),
        "alice",
        events,
        {1: 1, 2: 1},
        before_turn=2,
        replay=replay_with_settled_tools,
        needs_replay=snapshot_needs_settled_tool_replay,
        records=[record],
    )
    restored = [
        message.get("content") for message in (outcome or {}).get("messages", [])
    ]
    assert outcome is None, f"a covered canonical end must not be restored: {restored}"


def _round_with_display_and_foreign_record() -> tuple[list[dict], list[dict]]:
    """A canonical round with one display piece and covered foreign records."""
    events = _two_call_round(6, 10, "call-9")
    events.insert(
        1,
        {
            "event_id": 7,
            "type": "text_chunk",
            "content": "STREAMED ANSWER",
            "turn_index": 1,
            "branch_id": 1,
        },
    )
    events.insert(
        2,
        {
            "event_id": 8,
            "type": "tool_call",
            "call_id": "job-other",
            "tool_call_id": "call-other",
            "name": "echo",
            "args": {},
            "turn_index": 1,
            "branch_id": 2,
        },
    )
    events.insert(
        3,
        {
            "event_id": 9,
            "type": "text_chunk",
            "content": "SIBLING STREAM",
            "turn_index": 1,
            "branch_id": 2,
        },
    )
    events.extend(_call_pair(11, "call-9", "job-9", "RESULT_9"))
    gaps = [
        event
        for event in events
        if event["type"] == "tool_call" and event.get("tool_call_id") == "call-9"
    ]
    return events, gaps


def test_sparse_repair_refuses_a_covered_canonical_end() -> None:
    """The canonical end is part of the round that must stay uncovered."""
    events = _repair_events(("call-9", "job-9", "RESULT_9"))
    gaps = [event for event in events if event["type"] == "tool_call"]
    assert (
        repair_gap_messages(
            gaps,
            events,
            target_event_id=99,
            present_call_ids=set(),
            represented_event_ids={7},
        )
        is None
    ), "a covered canonical end must not be restored"


def test_sparse_repair_refuses_a_covered_round_display_piece() -> None:
    """A summarized display piece is represented content of the same round."""
    events, gaps = _round_with_display_and_foreign_record()
    assert (
        repair_gap_messages(
            gaps,
            events,
            target_event_id=99,
            present_call_ids=set(),
            represented_event_ids={7},
        )
        is None
    ), "a covered display piece must not be restored as a whole round"


def test_sparse_repair_refuses_a_round_with_an_ineligible_canonical_end() -> None:
    """Both identities of the round must sit on the live set."""
    events = _repair_events(("call-9", "job-9", "RESULT_9"))
    gaps = [event for event in events if event["type"] == "tool_call"]
    live = {event["event_id"] for event in events} - {7}
    assert (
        repair_gap_messages(
            gaps,
            events,
            target_event_id=99,
            present_call_ids=set(),
            eligible_ids=live,
        )
        is None
    )


def test_sparse_repair_ignores_a_covered_interleaved_foreign_record() -> None:
    """Coverage is decided per round record, not per numeric interval."""
    events, gaps = _round_with_display_and_foreign_record()
    first = repair_gap_messages(
        gaps,
        events,
        target_event_id=99,
        present_call_ids=set(),
        represented_event_ids={8},
    )
    assert first is not None
    assert [message.get("content") for message in first] == [
        "CHUNKED ANSWER",
        "RESULT_9",
    ]
    assert first[0]["tool_calls"][0]["id"] == "call-9"
    assert first[1]["tool_call_id"] == "call-9"
    again = repair_gap_messages(
        gaps,
        events,
        target_event_id=99,
        present_call_ids=set(),
        represented_event_ids={8},
    )
    assert again == first


def test_sparse_repair_ignores_a_covered_foreign_display_piece() -> None:
    """A display piece on another branch is not content of this round."""
    events, gaps = _round_with_display_and_foreign_record()
    repaired = repair_gap_messages(
        gaps,
        events,
        target_event_id=99,
        present_call_ids=set(),
        represented_event_ids={9},
    )
    assert repaired is not None
    assert [message.get("content") for message in repaired] == [
        "CHUNKED ANSWER",
        "RESULT_9",
    ]


def test_sparse_repair_refuses_a_dispatch_on_another_turn_or_branch() -> None:
    """A round whose own lineage differs from the dispatch cannot be reattached."""
    events = _repair_events(("call-9", "job-9", "RESULT_9"))
    gaps = [event for event in events if event["type"] == "tool_call"]
    gaps[0]["turn_index"], gaps[0]["branch_id"] = 2, 2
    assert (
        repair_gap_messages(gaps, events, target_event_id=99, present_call_ids=set())
        is None
    ), "a round on another turn/branch must not be reattached to the dispatch"


def _round_with_unstamped_display_piece(
    *, stamp_path: bool, path: list[list[int]] | None = None
) -> tuple[list[dict], list[dict]]:
    """A turn-2 round whose streamed piece may carry no ancestry stamp.

    Both round endpoints carry ``parent_branch_path``; the display piece carries
    only its own turn/branch pair unless ``stamp_path`` adds the ancestry.
    """
    ancestral = path if path is not None else [[1, 1]]
    lineage = {"turn_index": 2, "branch_id": 1, "parent_branch_path": ancestral}
    events = [
        {"event_id": 6, "type": "assistant_round_start", **lineage},
        {
            "event_id": 7,
            "type": "text_chunk",
            "content": "STREAMED ANSWER",
            "turn_index": 2,
            "branch_id": 1,
            **({"parent_branch_path": ancestral} if stamp_path else {}),
        },
        {
            "event_id": 8,
            "type": "tool_call",
            "call_id": "job-9",
            "tool_call_id": "call-9",
            "name": "echo",
            "args": {},
            **lineage,
        },
        {
            "event_id": 10,
            "type": "assistant_message",
            "round_start_event_id": 6,
            "message": {
                "role": "assistant",
                "content": "CHUNKED ANSWER",
                "tool_calls": [
                    {
                        "id": "call-9",
                        "type": "function",
                        "function": {"name": "echo", "arguments": "{}"},
                    }
                ],
            },
            **lineage,
        },
        {
            "event_id": 11,
            "type": "tool_result",
            "call_id": "job-9",
            "name": "echo",
            "output": "RESULT_9",
            **lineage,
        },
    ]
    gaps = [event for event in events if event["type"] == "tool_call"]
    return events, gaps


def test_sparse_repair_coverage_agrees_with_and_without_display_ancestry() -> None:
    """Coverage of the same display piece cannot depend on its ancestry stamp."""
    bare, bare_gaps = _round_with_unstamped_display_piece(stamp_path=False)
    stamped, stamped_gaps = _round_with_unstamped_display_piece(stamp_path=True)
    outcomes = [
        repair_gap_messages(
            gaps,
            events,
            target_event_id=99,
            present_call_ids=set(),
            represented_event_ids={7},
        )
        for events, gaps in ((bare, bare_gaps), (stamped, stamped_gaps))
    ]
    assert outcomes == [None, None]


def test_sparse_repair_restores_a_round_whose_display_piece_is_unstamped() -> None:
    """Unknown ancestry never proves the piece foreign, so an uncovered round fills."""
    events, gaps = _round_with_unstamped_display_piece(stamp_path=False)
    repaired = repair_gap_messages(
        gaps, events, target_event_id=99, present_call_ids=set()
    )
    assert repaired is not None
    assert [message["role"] for message in repaired] == ["assistant", "tool"]
    assert repaired[1]["tool_call_id"] == "call-9"
