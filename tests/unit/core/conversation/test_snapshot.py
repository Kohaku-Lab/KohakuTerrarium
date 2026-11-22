"""Checkpoint selection and dependency-safe prefix reconstruction tests."""

import copy
from pathlib import Path

import pytest

from kohakuterrarium.core.conversation import snapshot as snapshot_history
from kohakuterrarium.core.agent.history.message_history import (
    replay_with_settled_tools,
    snapshot_needs_settled_tool_replay,
)
from kohakuterrarium.core.conversation.snapshot import prepare_snapshot
from kohakuterrarium.session.store.records.snapshot import load_records
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


def _trigger_turn_events() -> list[dict]:
    """Turn 1 is typed; turn 2 is opened by a channel trigger (no user_message)."""
    return [
        {"event_id": 1, "type": "processing_start", "turn_index": 0, "branch_id": 0},
        {
            "event_id": 2,
            "type": "user_message",
            "content": "u1",
            "turn_index": 1,
            "branch_id": 1,
        },
        {
            "event_id": 3,
            "type": "text",
            "content": "a1",
            "turn_index": 1,
            "branch_id": 1,
        },
        {
            "event_id": 4,
            "type": "trigger_fired",
            "turn_index": 2,
            "branch_id": 1,
            "parent_branch_path": [[1, 1]],
        },
        {
            "event_id": 5,
            "type": "text",
            "content": "a2",
            "turn_index": 2,
            "branch_id": 1,
            "parent_branch_path": [[1, 1]],
        },
    ]


_TRIGGER_TURN_MSGS = [
    {
        "role": "user",
        "content": "summary of earlier work",
        "metadata": {"turn_index": 1, "branch_id": 1},
    },
    {"role": "assistant", "content": "a1"},
    {
        "role": "user",
        "content": "[channel] ping",
        "metadata": {"turn_index": 2, "branch_id": 1},
    },
    {"role": "assistant", "content": "a2"},
]


def test_checkpoint_through_a_trigger_opened_turn_is_restored(
    store: SessionStore,
) -> None:
    store.save_branch_snapshot(
        "alice",
        copy.deepcopy(_TRIGGER_TURN_MSGS),
        branch={"turn_index": 2, "branch_id": 1, "parent_branch_path": [[1, 1]]},
        watermark=5,
    )

    prepared = _prepare(store, _trigger_turn_events(), selected={0: 0, 1: 1, 2: 1})

    assert prepared is not None
    assert [m["content"] for m in prepared["messages"]] == [
        m["content"] for m in _TRIGGER_TURN_MSGS
    ]


def test_freshness_uses_the_store_counter_not_a_full_event_scan(
    store: SessionStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    store.append_event(
        "alice", "user_message", {"content": "u1"}, turn_index=1, branch_id=1
    )
    store.append_event("bob", "text", {"content": "later"}, turn_index=1, branch_id=1)
    alice_events = store.get_events("alice")

    def forbidden() -> None:
        raise AssertionError("freshness must not scan every agent's events")

    monkeypatch.setattr(store, "get_all_events", forbidden)

    assert store.global_max_event_id == 2
    assert snapshot_history._fresh(store, "alice", alice_events, 2) is True
    assert snapshot_history._fresh(store, "alice", alice_events, 3) is False


def test_cutoff_replay_retains_compaction_evidence_from_deselected_completion():
    """A foreign completion may still summarize the selected ancestor suffix."""
    from kohakuterrarium.core.conversation.snapshot import _replay_context

    events = [
        {
            "event_id": 1,
            "type": "user_message",
            "content": "protected root",
            "turn_index": 1,
            "branch_id": 1,
            "parent_branch_path": [],
        },
        {
            "event_id": 2,
            "type": "user_message",
            "content": "unchanged user",
            "turn_index": 2,
            "branch_id": 1,
            "parent_branch_path": [[1, 1]],
        },
        {
            "event_id": 3,
            "type": "text",
            "content": "unchanged reply",
            "turn_index": 2,
            "branch_id": 1,
            "parent_branch_path": [[1, 1]],
        },
        {
            "event_id": 4,
            "type": "compact_complete",
            "summary": "REUSED SUMMARY",
            "round": 1,
            "replaced_from_event_id": 2,
            "replaced_to_event_id": 3,
            "compact_path": [[2, 1]],
            "compact_source_path": [[1, 1], [2, 1]],
            "turn_index": 3,
            "branch_id": 1,
            "parent_branch_path": [[1, 1], [2, 1]],
        },
        {
            "event_id": 4,
            "type": "user_message",
            "content": "new sibling",
            "turn_index": 3,
            "branch_id": 2,
            "parent_branch_path": [[1, 1], [2, 1]],
        },
    ]
    messages = _replay_context(
        events, {1: 1, 2: 1, 3: 2}, 1, None, replay_with_settled_tools
    )
    contents = [message["content"] for message in messages]
    assert any("REUSED SUMMARY" in value for value in contents)
    assert "unchanged user" not in contents
    assert "unchanged reply" not in contents
    assert "new sibling" in contents


@pytest.mark.parametrize("source_compatible", [True, False])
def test_prepare_snapshot_cutoff_repair_preserves_reusable_compaction_summary(
    store: SessionStore, source_compatible: bool
):
    """Real cutoff repair retains compatible old-tail summary and settled tools."""
    _save_cutoff_ack_snapshot(store, pending=False)
    events = [
        *_LATER_TOOL_EVENTS,
        {
            "event_id": 16,
            "type": "compact_complete",
            "summary": "inherited compacted work",
            "round": 1,
            "replaced_from_event_id": 9,
            "replaced_to_event_id": 10,
            "compact_path": [[3, 1]],
            "compact_source_path": (
                [[1, 1], [2, 1], [3, 1]]
                if source_compatible
                else [[1, 1], [2, 2], [3, 1]]
            ),
            "turn_index": 4,
            "branch_id": 2,
            "parent_branch_path": [[1, 1], [2, 1], [3, 1]],
        },
    ]
    repaired = _prepare(store, events, {1: 1, 2: 1, 3: 1, 4: 1})
    assert repaired is not None
    contents = [m.get("content", "") for m in repaired["messages"]]
    assert any("inherited compacted work" in v for v in contents) is source_compatible
    assert repaired["compact_state"]["compact_count"] == int(source_compatible)
    assert "new final result" in contents
    assert repaired["context_cutoff_event_id"] == 8


def test_cutoff_repair_rejects_result_with_ambiguous_origins_from_unselected_branch():
    """Cutoff projection must retain global tool-call ownership as evidence.

    A sibling call may reuse internal/provider IDs. The final result from
    that sibling cannot settle a selected call whose original result is absent.
    """
    from kohakuterrarium.core.conversation.snapshot import _replay_context

    events = [
        {
            "event_id": 1,
            "type": "tool_call",
            "turn_index": 1,
            "branch_id": 1,
            "parent_branch_path": [],
            "call_id": "shared",
            "tool_call_id": "same",
            "name": "echo",
            "args": {},
        },
        {
            "event_id": 2,
            "type": "tool_call",
            "turn_index": 1,
            "branch_id": 2,
            "parent_branch_path": [],
            "call_id": "shared",
            "tool_call_id": "same",
            "name": "echo",
            "args": {},
        },
        {
            "event_id": 3,
            "type": "tool_result",
            "turn_index": 1,
            "branch_id": 2,
            "parent_branch_path": [],
            "call_id": "shared",
            "tool_call_id": "same",
            "name": "echo",
            "output": "FOREIGN-RESULT-MUST-NOT-SETTLE",
        },
    ]
    from kohakuterrarium.core.agent.history.message_history import (
        replay_with_settled_tools,
    )

    full = replay_with_settled_tools(
        events, branch_view={1: 1}, include_metadata=True, resumable=True
    )
    repair = _replay_context(events, {1: 1}, 1, None, replay_with_settled_tools)
    # Equivalent to full source replay for tool ownership; branch-2 output
    # may not leak into a branch-1 conversation.
    assert repair == full
    assert all("FOREIGN-RESULT" not in str(msg.get("content", "")) for msg in repair)


def test_cutoff_repair_preserves_uniquely_owned_late_result_from_sibling_branch():
    """A legitimate late result is still reusable when the source proves one caller."""
    from kohakuterrarium.core.conversation.snapshot import _replay_context
    from kohakuterrarium.core.agent.history.message_history import (
        replay_with_settled_tools,
    )

    events = [
        {
            "event_id": 1,
            "type": "tool_call",
            "turn_index": 1,
            "branch_id": 1,
            "parent_branch_path": [],
            "call_id": "unique",
            "tool_call_id": "same",
            "name": "echo",
            "args": {},
        },
        {
            "event_id": 3,
            "type": "tool_result",
            "turn_index": 1,
            "branch_id": 2,
            "parent_branch_path": [],
            "call_id": "unique",
            "tool_call_id": "same",
            "name": "echo",
            "output": "VALID-LATE-RESULT",
        },
    ]
    repaired = _replay_context(events, {1: 1}, 1, None, replay_with_settled_tools)
    assert any(
        msg.get("role") == "tool" and msg.get("content") == "VALID-LATE-RESULT"
        for msg in repaired
    )


@pytest.mark.parametrize("compatible", [True, False])
def test_new_compatible_compaction_invalidates_old_snapshot_candidate(
    tmp_path, compatible
):
    """A newer summary can replace the checkpoint's raw ancestor representation."""
    from kohakuterrarium.session.resume.branch import replayed_messages_for

    store = SessionStore(tmp_path / "snapshot-after-compaction.kohakutr")
    try:
        store.init_meta("sid", "agent", "", str(tmp_path), ["alice"])
        store.append_event(
            "alice",
            "user_message",
            {"content": "ORIGINAL-PROMPT"},
            turn_index=1,
            branch_id=1,
            parent_branch_path=[],
        )
        store.append_event(
            "alice",
            "text",
            {"content": "ORIGINAL-REPLY"},
            turn_index=1,
            branch_id=1,
            parent_branch_path=[],
        )
        store.save_branch_snapshot(
            "alice",
            [
                {
                    "role": "user",
                    "content": "ORIGINAL-PROMPT",
                    "metadata": {"turn_index": 1, "branch_id": 1},
                },
                {"role": "assistant", "content": "ORIGINAL-REPLY"},
            ],
            branch={"turn_index": 1, "branch_id": 1, "parent_branch_path": []},
            watermark=2,
        )
        store.append_event(
            "alice",
            "compact_complete",
            {
                "summary": "REUSABLE-SUMMARY",
                "round": 1,
                "replaced_from_event_id": 1,
                "replaced_to_event_id": 2,
                "compact_path": [[1, 1]],
                "compact_source_path": [[1, 1]] if compatible else [[1, 2]],
            },
            turn_index=2,
            branch_id=1,
            parent_branch_path=[[1, 1]],
        )
        store.append_event(
            "alice",
            "user_message",
            {"content": "SELECTED-TARGET"},
            turn_index=2,
            branch_id=2,
            parent_branch_path=[[1, 1]],
        )
        events = store.get_events("alice")
        view = {1: 1, 2: 2}
        checkpoint = _prepare(store, events, view)
        direct = replay_with_settled_tools(
            events, branch_view=view, include_metadata=True, resumable=True
        )
        if compatible:
            assert (
                checkpoint is None
            ), "A newer selected compact invalidates raw snapshot"
            loaded = replayed_messages_for(store, "alice", events, branch_view=view)
            assert [m["content"] for m in loaded] == [m["content"] for m in direct]
            assert "REUSABLE-SUMMARY" in loaded[0]["content"]
        else:
            assert (
                checkpoint is not None
            ), "Incompatible compaction must not force replay"
            assert [m["content"] for m in checkpoint["messages"]] == [
                m["content"] for m in direct
            ]
    finally:
        store.close(update_status=False)


def test_checkpoint_cannot_certify_nonmonotonic_physical_source_without_compaction(
    store: SessionStore,
):
    """Late imported rows with low unique IDs cannot disappear behind a watermark."""
    store.save_branch_snapshot(
        "alice",
        [
            {
                "role": "user",
                "content": "cached",
                "metadata": {"turn_index": 1, "branch_id": 1},
            }
        ],
        branch={"turn_index": 1, "branch_id": 1, "parent_branch_path": []},
        watermark=51,
    )
    events = [
        {
            "event_id": 50,
            "type": "user_message",
            "content": "cached",
            "turn_index": 1,
            "branch_id": 1,
        },
        {
            "event_id": 51,
            "type": "text",
            "content": "prior answer",
            "turn_index": 1,
            "branch_id": 1,
        },
        {
            "event_id": 10,
            "type": "text",
            "content": "later imported answer",
            "turn_index": 1,
            "branch_id": 1,
        },
    ]
    assert _prepare(store, events, selected={1: 1}) is None
    assert any(
        "later imported answer" in message["content"]
        for message in replay_with_settled_tools(
            events, branch_view={1: 1}, include_metadata=True, resumable=True
        )
    )
