"""Tests for reseating an agent on uncompacted persisted history."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

import kohakuterrarium.core.agent_raw_history as raw
from kohakuterrarium.core.agent_raw_history import (
    install_history_candidate,
    prepare_persisted_edit,
    prepare_raw_prefix_candidate,
)
from kohakuterrarium.core.conversation import Conversation, ConversationConfig
from kohakuterrarium.core.message_locator import user_message_indices_for_turn
from kohakuterrarium.errors import ConflictError
from kohakuterrarium.session.raw_history import UserMessageSelector
from kohakuterrarium.session.store import SessionStore


class _Store:
    def __init__(self, events):
        self.events = events

    def get_events(self, agent_name):
        assert agent_name == "worker"
        return self.events


def _event(event_id, event_type, *, turn=None, content="", path=None):
    event = {"event_id": event_id, "type": event_type}
    if turn is not None:
        event.update(
            {
                "turn_index": turn,
                "branch_id": 1,
                "parent_branch_path": path or [],
            }
        )
    if content:
        event["content"] = content
    return event


def _agent(events, *, conversation_config=None):
    conversation = Conversation(conversation_config)
    conversation.append("system", "current runtime prompt")
    conversation.append("user", "compacted context that must be discarded")
    return SimpleNamespace(
        session_store=_Store(events),
        config=SimpleNamespace(name="worker"),
        controller=SimpleNamespace(conversation=conversation),
        _turn_index=9,
        _branch_id=4,
        _parent_branch_path=[(8, 4)],
    )


def _restore_prefix(agent, target, **kwargs):
    candidate = prepare_raw_prefix_candidate(agent, target, **kwargs)
    install_history_candidate(agent, candidate)


def test_reload_restores_missing_system_prompt_and_canonical_user_metadata():
    events = [
        _event(1, "user_message", turn=1, content="same"),
        _event(2, "text_chunk", turn=1, content="first reply"),
        _event(3, "user_message", turn=2, content="same", path=[[1, 1]]),
        _event(4, "text_chunk", turn=2, content="discarded tail", path=[[1, 1]]),
    ]
    agent = _agent(events)

    _restore_prefix(
        agent,
        UserMessageSelector(event_id=3, turn_index=2, branch_id=1),
    )

    messages = agent.controller.conversation.get_messages()
    assert [(message.role, message.content) for message in messages] == [
        ("system", "current runtime prompt"),
        ("user", "same"),
        ("assistant", "first reply"),
        ("user", "same"),
    ]
    assert user_message_indices_for_turn(messages, 1) == [1]
    assert user_message_indices_for_turn(messages, 2) == [3]
    assert messages[1].metadata == {
        "event_id": 1,
        "turn_index": 1,
        "branch_id": 1,
    }
    assert messages[3].metadata == {
        "event_id": 3,
        "turn_index": 2,
        "branch_id": 1,
    }
    assert agent._turn_index == 2
    assert agent._branch_id == 1
    assert agent._parent_branch_path == [(1, 1)]


def test_reload_restores_native_tool_announcement_before_result():
    events = [
        _event(1, "user_message", turn=1, content="run it"),
        {
            "event_id": 2,
            "type": "tool_call",
            "name": "bash",
            "call_id": "bash_replay_1",
            "args": {"cmd": "pwd"},
            "turn_index": 1,
            "branch_id": 1,
            "parent_branch_path": [],
        },
        {
            "event_id": 3,
            "type": "tool_result",
            "name": "bash",
            "call_id": "bash_replay_1",
            "output": "/work",
            "turn_index": 1,
            "branch_id": 1,
            "parent_branch_path": [],
        },
        _event(4, "user_message", turn=2, content="change this", path=[[1, 1]]),
    ]
    agent = _agent(events)

    _restore_prefix(
        agent,
        UserMessageSelector(event_id=4, turn_index=2, branch_id=1),
    )

    messages = agent.controller.conversation.get_messages()
    tool_index = next(i for i, message in enumerate(messages) if message.role == "tool")
    announcement = messages[tool_index - 1]
    assert announcement.role == "assistant"
    assert [call["id"] for call in announcement.tool_calls] == ["bash_replay_1"]
    assert messages[tool_index].tool_call_id == "bash_replay_1"


def test_reload_does_not_duplicate_a_persisted_system_prompt():
    events = [
        _event(1, "system_prompt_set", content="persisted prompt"),
        _event(2, "user_message", turn=1, content="target"),
    ]
    agent = _agent(events)

    _restore_prefix(
        agent,
        UserMessageSelector(event_id=2, turn_index=1, branch_id=1),
    )

    messages = agent.controller.conversation.get_messages()
    assert [(message.role, message.content) for message in messages] == [
        ("system", "persisted prompt"),
        ("user", "target"),
    ]


def test_reload_preserves_the_runtime_conversation_config():
    events = [
        _event(1, "user_message", turn=1, content="target"),
    ]
    config = ConversationConfig(
        max_messages=17,
        keep_system=False,
        sanitize_orphan_tool_calls=False,
    )
    agent = _agent(events, conversation_config=config)

    _restore_prefix(
        agent,
        UserMessageSelector(event_id=1, turn_index=1, branch_id=1),
    )

    assert agent.controller.conversation.config is config


# ── P4: reload preserves the branch's compact baseline ────────────


def _compact_event(event_id, replaced_from, replaced_to, summary="[S]"):
    return {
        "event_id": event_id,
        "type": "compact_replace",
        "summary_text": summary,
        "replaced_from_event_id": replaced_from,
        "replaced_to_event_id": replaced_to,
    }


def test_reload_discards_summary_covering_the_edited_target():
    events = [
        _event(1, "user_message", turn=1, content="U1"),
        _event(2, "text_chunk", turn=1, content="R1"),
        _event(3, "user_message", turn=2, content="U2", path=[[1, 1]]),
        _event(4, "text_chunk", turn=2, content="R2", path=[[1, 1]]),
        _event(5, "user_message", turn=3, content="U3", path=[[1, 1], [2, 1]]),
        _event(6, "text_chunk", turn=3, content="R3", path=[[1, 1], [2, 1]]),
        _compact_event(7, 1, 4),
    ]
    agent = _agent(events)

    _restore_prefix(
        agent,
        UserMessageSelector(event_id=3, turn_index=2, branch_id=1),
    )

    messages = agent.controller.conversation.get_messages()
    assert [(m.role, m.content) for m in messages] == [
        ("system", "current runtime prompt"),
        ("user", "U1"),
        ("assistant", "R1"),
        ("user", "U2"),
    ]


def test_reload_keeps_compact_baseline_for_later_target():
    # Editing turn3 (after the compacted turn1-2) still preserves the
    # compact baseline — turn1-2 stay summarized, turn3 materializes.
    events = [
        _event(1, "user_message", turn=1, content="U1"),
        _event(2, "text_chunk", turn=1, content="R1"),
        _event(3, "user_message", turn=2, content="U2", path=[[1, 1]]),
        _event(4, "text_chunk", turn=2, content="R2", path=[[1, 1]]),
        _event(5, "user_message", turn=3, content="U3", path=[[1, 1], [2, 1]]),
        _event(6, "text_chunk", turn=3, content="R3", path=[[1, 1], [2, 1]]),
        {
            **_compact_event(7, 1, 4),
            "compact_source_path": [[1, 1], [2, 1]],
            "compact_path": [[1, 1], [2, 1], [3, 1]],
        },
    ]
    agent = _agent(events)

    _restore_prefix(
        agent,
        UserMessageSelector(event_id=5, turn_index=3, branch_id=1),
    )

    messages = agent.controller.conversation.get_messages()
    assert [(m.role, m.content) for m in messages] == [
        ("system", "current runtime prompt"),
        ("assistant", "[S]"),
        ("user", "U3"),
    ]


def test_reload_uncompacted_target_unchanged():
    # No compaction -> existing behavior preserved (full raw prefix).
    events = [
        _event(1, "user_message", turn=1, content="U1"),
        _event(2, "text_chunk", turn=1, content="R1"),
        _event(3, "user_message", turn=2, content="U2", path=[[1, 1]]),
        _event(4, "text_chunk", turn=2, content="R2", path=[[1, 1]]),
    ]
    agent = _agent(events)

    _restore_prefix(
        agent,
        UserMessageSelector(event_id=3, turn_index=2, branch_id=1),
    )

    messages = agent.controller.conversation.get_messages()
    assert [(m.role, m.content) for m in messages] == [
        ("system", "current runtime prompt"),
        ("user", "U1"),
        ("assistant", "R1"),
        ("user", "U2"),
    ]


def test_reload_compact_starting_at_target_not_kept():
    # A compact_replace whose range starts at the target has nothing BEFORE
    # the target to preserve; it must not be kept (capping would create an
    # invalid to<from range and a spurious summary).
    events = [
        _event(1, "user_message", turn=1, content="U1"),
        _event(2, "user_message", turn=2, content="U2", path=[[1, 1]]),
        _event(3, "text_chunk", turn=2, content="R2", path=[[1, 1]]),
        _compact_event(4, 2, 4),
    ]
    agent = _agent(events)
    _restore_prefix(
        agent,
        UserMessageSelector(event_id=2, turn_index=2, branch_id=1),
    )
    messages = agent.controller.conversation.get_messages()
    assert [(m.role, m.content) for m in messages] == [
        ("system", "current runtime prompt"),
        ("user", "U1"),
        ("user", "U2"),
    ]


def test_reload_ignores_sibling_branch_compact():
    # A compact recorded on a SIBLING branch whose compact_path shares NO turn
    # with the target lineage (here branch-1 turns 2..3, while the target is
    # branch-2 turn 2 under turn1-branch1) must not be kept as the target
    # branch's baseline — replay would otherwise see a foreign rule and (for
    # legacy pathless data) could resurrect or mis-summarize the prefix.
    events = [
        _event(1, "user_message", turn=1, content="U1"),
        _event(2, "text_chunk", turn=1, content="R1"),
        # branch-2 target turn
        {
            "event_id": 3,
            "type": "user_message",
            "content": "U2",
            "turn_index": 2,
            "branch_id": 2,
            "parent_branch_path": [(1, 1)],
        },
        {
            "event_id": 4,
            "type": "text_chunk",
            "content": "R2",
            "turn_index": 2,
            "branch_id": 2,
            "parent_branch_path": [(1, 1)],
        },
        # Sibling branch-1 compact whose path has NO turn in common with the
        # target lineage (branch-1 turns 2..3 only; turn1-branch1 is absent).
        {
            "event_id": 5,
            "type": "compact_complete",
            "summary": "[SIB]",
            "replaced_from_event_id": 1,
            "replaced_to_event_id": 4,
            "compact_path": [[2, 1], [3, 1]],
            "turn_index": 3,
            "branch_id": 1,
        },
    ]
    agent = _agent(events)
    _restore_prefix(
        agent,
        UserMessageSelector(event_id=3, turn_index=2, branch_id=2),
    )
    messages = agent.controller.conversation.get_messages()
    assert [(m.role, m.content) for m in messages] == [
        ("system", "current runtime prompt"),
        ("user", "U1"),
        ("assistant", "R1"),
        ("user", "U2"),
    ]


def test_reload_ignores_sibling_compact_sharing_ancestor():
    # Both summaries depend on turn 3; neither is safe before regenerating it.
    events = [
        _event(1, "user_message", turn=1, content="U1"),
        _event(2, "text_chunk", turn=1, content="R1"),
        _event(3, "user_message", turn=2, content="U2", path=[[1, 1]]),
        _event(4, "text_chunk", turn=2, content="R2", path=[[1, 1]]),
        _event(5, "user_message", turn=3, content="U3a", path=[[1, 1], [2, 1]]),
        _event(6, "text_chunk", turn=3, content="R3a", path=[[1, 1], [2, 1]]),
        {
            "event_id": 7,
            "type": "compact_complete",
            "summary": "[S1]",
            "replaced_from_event_id": 1,
            "replaced_to_event_id": 4,
            "compact_path": [[1, 1], [2, 1], [3, 1]],
            "turn_index": 3,
            "branch_id": 1,
            "parent_branch_path": [(1, 1), (2, 1)],
        },
        {
            "event_id": 8,
            "type": "user_message",
            "content": "U3b",
            "turn_index": 3,
            "branch_id": 2,
            "parent_branch_path": [(1, 1), (2, 1)],
        },
        {
            "event_id": 9,
            "type": "text_chunk",
            "content": "R3b",
            "turn_index": 3,
            "branch_id": 2,
            "parent_branch_path": [(1, 1), (2, 1)],
        },
        {
            "event_id": 10,
            "type": "compact_complete",
            "summary": "[S2]",
            "replaced_from_event_id": 1,
            "replaced_to_event_id": 9,
            "compact_path": [[1, 1], [2, 1], [3, 2]],
            "turn_index": 3,
            "branch_id": 2,
            "parent_branch_path": [(1, 1), (2, 1)],
        },
    ]
    agent = _agent(events)
    _restore_prefix(
        agent,
        UserMessageSelector(event_id=8, turn_index=3, branch_id=2),
    )
    messages = agent.controller.conversation.get_messages()
    assert [(m.role, m.content) for m in messages] == [
        ("system", "current runtime prompt"),
        ("user", "U1"),
        ("assistant", "R1"),
        ("user", "U2"),
        ("assistant", "R2"),
        ("user", "U3b"),
    ]


@pytest.mark.parametrize("limited_by", ["tokens", "retention"])
def test_expanded_prefix_is_rejected_before_installation(limited_by):
    events = [
        _event(1, "user_message", turn=1, content="RAW" * 200),
        _event(2, "text_chunk", turn=1, content="ANSWER" * 200),
        _event(3, "user_message", turn=2, content="target", path=[[1, 1]]),
    ]
    agent = _agent(events)
    if limited_by == "tokens":
        agent.compact_manager = SimpleNamespace(
            config=SimpleNamespace(max_tokens=40, threshold=0.8),
            _compact_count=8,
            _last_compact_time=123.0,
        )
    else:
        agent.controller.conversation.config.max_messages = 2
    original = agent.controller.conversation
    contents = original.to_messages(include_metadata=True)
    state = (agent._turn_index, agent._branch_id, list(agent._parent_branch_path))
    with pytest.raises(ConflictError, match="[Hh]istory reconstruction"):
        _restore_prefix(agent, UserMessageSelector(3, 2, 1))
    assert agent.controller.conversation is original
    assert original.to_messages(include_metadata=True) == contents
    assert (agent._turn_index, agent._branch_id, agent._parent_branch_path) == state
    assert agent.session_store.events == events
    if limited_by == "tokens":
        assert agent.compact_manager._compact_count == 8
        assert agent.compact_manager._last_compact_time == 123.0


def test_reconstruction_admits_final_replacement_not_the_old_target():
    events = [
        _event(1, "user_message", turn=1, content="prior"),
        _event(2, "user_message", turn=2, content="X" * 8000, path=[[1, 1]]),
    ]
    agent = _agent(events)
    agent.compact_manager = SimpleNamespace(
        config=SimpleNamespace(max_tokens=200, threshold=0.8),
        _compact_count=1,
        _last_compact_time=0,
    )
    _restore_prefix(agent, UserMessageSelector(2, 2, 1), replacement_content="small")
    assert agent.controller.conversation.get_messages()[-1].content == "X" * 8000


def _compact_complete_event(event_id, replaced_from, replaced_to, summary="[S]"):
    # Runtime (v2) compaction persists compact_complete, not compact_replace.
    return {
        "event_id": event_id,
        "type": "compact_complete",
        "summary": summary,
        "replaced_from_event_id": replaced_from,
        "replaced_to_event_id": replaced_to,
        "compact_path": [[1, 1], [2, 1]],
        "turn_index": 3,
        "branch_id": 1,
    }


def test_reload_discards_v2_summary_covering_the_edited_target():
    events = [
        _event(1, "user_message", turn=1, content="U1"),
        _event(2, "text_chunk", turn=1, content="R1"),
        _event(3, "user_message", turn=2, content="U2", path=[[1, 1]]),
        _event(4, "text_chunk", turn=2, content="R2", path=[[1, 1]]),
        _event(5, "user_message", turn=3, content="U3", path=[[1, 1], [2, 1]]),
        _compact_complete_event(6, 1, 4),
    ]
    agent = _agent(events)
    _restore_prefix(
        agent,
        UserMessageSelector(event_id=3, turn_index=2, branch_id=1),
    )
    messages = agent.controller.conversation.get_messages()
    assert [(m.role, m.content) for m in messages] == [
        ("system", "current runtime prompt"),
        ("user", "U1"),
        ("assistant", "R1"),
        ("user", "U2"),
    ]


# ── detached candidate admission ─────────────────────────────────


class _CheckpointStore(_Store):
    """Store stub that also exposes persisted branch checkpoints."""

    def __init__(self, events, records=()):
        super().__init__(events)
        self.state = {"worker:branch_snapshots": [dict(r) for r in records]}


def _checkpoint_agent(events, records=(), *, max_tokens=100):
    agent = _agent(events)
    agent.session_store = _CheckpointStore(events, records)
    agent.compact_manager = SimpleNamespace(
        config=SimpleNamespace(max_tokens=max_tokens, threshold=1.0),
        _compact_count=2,
        _last_compact_time=5.0,
    )
    return agent


_CHECKPOINT_EVENTS = [
    _event(1, "user_message", turn=1, content="U1"),
    _event(2, "user_message", turn=2, content="target", path=[[1, 1]]),
]

_CHECKPOINT_RECORD = {
    "path": [[1, 1]],
    "watermark": 2,
    "messages": [
        {
            "role": "user",
            "content": "U1",
            "metadata": {"turn_index": 1, "branch_id": 1},
        },
        {"role": "assistant", "content": "CHECKPOINT_ANSWER" * 400},
    ],
    "compact_state": {"compact_count": 0, "last_compact_time": 0.0},
}


def _snapshot(agent):
    """The live state admission must leave untouched."""
    conversation = agent.controller.conversation
    return (
        conversation,
        deepcopy(conversation.to_messages(include_metadata=True)),
        agent._turn_index,
        agent._branch_id,
        list(agent._parent_branch_path),
        agent.compact_manager._compact_count,
        agent.compact_manager._last_compact_time,
        deepcopy(agent.session_store.get_events(agent.config.name)),
        deepcopy(dict(agent.session_store.state)),
        getattr(agent, "_context_cutoff_event_id", 0),
    )


@pytest.mark.parametrize("route", ["target", "branch"])
def test_prepare_edit_preserves_live_state_until_explicit_install(route):
    agent = _checkpoint_agent(deepcopy(_CHECKPOINT_EVENTS), max_tokens=10**6)
    before = _snapshot(agent)
    prepared = prepare_persisted_edit(
        agent,
        message_idx=2,
        replacement_content="edited",
        branch_view={1: 1, 2: 1},
        target=UserMessageSelector(2, 2, 1) if route == "target" else None,
    )
    assert prepared is not None
    assert _snapshot(agent) == before
    prepared.install(agent)
    assert agent.controller.conversation is before[0]
    assert (
        agent.controller.conversation.get_messages()[prepared.index].content == "target"
    )
    assert (agent._turn_index, agent._branch_id, agent._parent_branch_path) == (
        2,
        1,
        [(1, 1)],
    )


def test_prepare_candidate_prefers_the_proven_checkpoint():
    agent = _checkpoint_agent(
        _CHECKPOINT_EVENTS, [_CHECKPOINT_RECORD], max_tokens=10**6
    )
    candidate = prepare_raw_prefix_candidate(agent, UserMessageSelector(2, 2, 1))
    assert [message.role for message in candidate.messages] == [
        "system",
        "user",
        "assistant",
        "user",
    ]
    assert candidate.messages[2].content == "CHECKPOINT_ANSWER" * 400
    assert (candidate.turn_index, candidate.branch_id) == (2, 1)
    assert candidate.target_index == 3
    assert candidate.compact_state == {
        "compact_count": 0,
        "last_compact_time": 0.0,
    }


def test_oversized_checkpoint_candidate_falls_back_to_the_raw_prefix():
    # The proven checkpoint carries a far larger answer than the raw events do,
    # so it cannot be admitted; the compatible raw reconstruction of the SAME
    # target is admitted instead of failing the mutation.
    agent = _checkpoint_agent(_CHECKPOINT_EVENTS, [_CHECKPOINT_RECORD], max_tokens=100)
    before = _snapshot(agent)
    candidate = prepare_raw_prefix_candidate(agent, UserMessageSelector(2, 2, 1))
    assert [message.content for message in candidate.messages] == [
        "current runtime prompt",
        "U1",
        "target",
    ]
    assert candidate.target_index == 2
    assert candidate.messages[candidate.target_index].content == "target"
    # Nothing was installed and no budget was widened for the fallback.
    assert _snapshot(agent) == before
    assert agent.compact_manager.config.max_tokens == 100


def test_unprovable_paired_summary_cannot_rescue_an_oversized_prefix():
    # The checkpoint summary has no recorded inputs, so its lineage cannot be
    # proven; the pathless legacy summary in the event stream cannot vouch for
    # the raw ancestors either. Neither route may drop messages, so the
    # oversized raw reconstruction is rejected and nothing is installed.
    events = [
        _event(1, "user_message", turn=1, content="RAW" * 400),
        _event(2, "text_chunk", turn=1, content="ANSWER" * 400),
        _event(3, "user_message", turn=2, content="U2", path=[[1, 1]]),
        {
            "event_id": 4,
            "type": "compact_complete",
            "summary": "UNPROVEN",
            "replaced_from_event_id": 1,
            "replaced_to_event_id": 2,
            "turn_index": 2,
            "branch_id": 1,
        },
        _event(5, "user_message", turn=3, content="target", path=[[1, 1], [2, 1]]),
    ]
    record = {
        "path": [[2, 1]],
        "watermark": 4,
        "messages": [
            {
                "role": "user",
                "content": "U2",
                "metadata": {"turn_index": 2, "branch_id": 1},
            },
            {
                "role": "assistant",
                "content": "[Previous context summary (compact round 1)]\n\nUNPROVEN",
            },
        ],
        "compact_state": {"compact_count": 1, "last_compact_time": 0.0},
    }
    agent = _checkpoint_agent(events, [record], max_tokens=100)
    before = _snapshot(agent)
    with pytest.raises(ConflictError, match="context budget"):
        prepare_raw_prefix_candidate(agent, UserMessageSelector(5, 3, 1))
    assert _snapshot(agent) == before


def test_checkpoint_miss_rebuilds_the_raw_prefix_once(monkeypatch):
    """A miss already produced the raw prefix; it must not be rebuilt lazily."""
    agent = _checkpoint_agent(_CHECKPOINT_EVENTS)
    original = raw.replay_conversation
    replays: list[int] = []

    def observe(events, **kwargs):
        data = list(events)
        if len(data) > 1:
            replays.append(len(data))
        return original(data, **kwargs)

    monkeypatch.setattr(raw, "replay_conversation", observe)
    prepare_raw_prefix_candidate(agent, UserMessageSelector(2, 2, 1))
    assert replays == [2]


def test_unusable_checkpoints_fall_back_to_raw_without_changing_state():
    """Retrying other checkpoints is bounded; none fitting still refuses."""
    events = [
        _event(1, "user_message", turn=1, content="U1"),
        _event(2, "user_message", turn=2, content="target", path=[[1, 1]]),
    ]
    oversized = {
        "path": [[1, 1]],
        "watermark": 2,
        "messages": [
            {
                "role": "user",
                "content": "EXPANDED" * 4000,
                "metadata": {"turn_index": 1, "branch_id": 1},
            }
        ],
        "compact_state": {"compact_count": 0, "last_compact_time": 0.0},
    }
    agent = _checkpoint_agent(events, [oversized], max_tokens=100)
    before = _snapshot(agent)
    candidate = prepare_raw_prefix_candidate(agent, UserMessageSelector(2, 2, 1))
    contents = [message.get_text_content() for message in candidate.messages]
    assert not any("EXPANDED" in content for content in contents)
    assert "U1" in contents
    assert _snapshot(agent) == before


def test_smaller_compatible_checkpoint_is_used_when_the_bigger_one_exceeds_budget():
    """A rejected checkpoint must not send the target to a raw rebuild."""
    events = [
        _event(1, "user_message", turn=1, content="U1"),
        _event(2, "user_message", turn=2, content="target", path=[[1, 1]]),
    ]
    big = dict(_CHECKPOINT_RECORD)
    small = {
        "path": [[1, 1]],
        "watermark": 1,
        "messages": [
            {
                "role": "user",
                "content": "U1",
                "metadata": {"turn_index": 1, "branch_id": 1},
            },
            {"role": "assistant", "content": "SMALL_CHECKPOINT"},
        ],
        "compact_state": {"compact_count": 0, "last_compact_time": 0.0},
    }
    agent = _checkpoint_agent(events, [big, small], max_tokens=100)
    candidate = prepare_raw_prefix_candidate(agent, UserMessageSelector(2, 2, 1))
    contents = [message.get_text_content() for message in candidate.messages]
    assert "SMALL_CHECKPOINT" in contents


def test_cutoff_reconstruction_that_exceeds_budget_refuses():
    """A cutoff reconstruction is one candidate; refusing keeps state intact."""
    cutoff_record = dict(_CHECKPOINT_RECORD)
    cutoff_record["context_cutoff_event_id"] = 1
    agent = _checkpoint_agent(_CHECKPOINT_EVENTS, [cutoff_record], max_tokens=1)
    before = _snapshot(agent)
    with pytest.raises(ConflictError):
        prepare_raw_prefix_candidate(agent, UserMessageSelector(2, 2, 1))
    assert _snapshot(agent) == before


# ── cutoff reconstruction candidates ─────────────────────────────


def _cutoff_store(tmp_path, *, cleared_answer="CLEARED_ANSWER", checkpoint_system=None):
    """Real store with one cleared turn, a cutoff checkpoint, and the target."""
    store = SessionStore(tmp_path / "cutoff.kohakutr")
    store.append_event(
        "worker", "user_message", {"content": "CLEARED_USER"}, turn_index=1, branch_id=1
    )
    store.append_event(
        "worker", "text_chunk", {"content": cleared_answer}, turn_index=1, branch_id=1
    )
    store.append_event("worker", "context_cleared", {}, turn_index=1, branch_id=1)
    cutoff = store.max_event_id("worker")
    messages = [{"role": "assistant", "content": "UNANCHORED_CHECKPOINT"}]
    if checkpoint_system is not None:
        messages.insert(0, {"role": "system", "content": checkpoint_system})
    store.save_branch_snapshot(
        "worker",
        messages,
        branch={
            "turn_index": 1,
            "branch_id": 1,
            "parent_branch_path": [],
            "context_cutoff_event_id": cutoff,
        },
        watermark=cutoff,
        compact_state={"compact_count": 0, "last_compact_time": 0.0},
    )
    store.append_event(
        "worker",
        "user_message",
        {"content": "TARGET"},
        turn_index=2,
        branch_id=1,
        parent_branch_path=[(1, 1)],
    )
    return store, cutoff


def _cutoff_agent(store, *, max_tokens):
    agent = _checkpoint_agent(store.get_events("worker"), max_tokens=max_tokens)
    agent.session_store = store
    return agent


@pytest.mark.parametrize(
    ("cleared_answer", "max_tokens"),
    [("CLEARED_ANSWER", 10_000), ("CLEARED_ANSWER" * 40, 50)],
    ids=["raw-fits", "raw-over-budget"],
)
def test_cutoff_candidate_keeps_cleared_history_cleared(
    tmp_path, cleared_answer, max_tokens
):
    store, cutoff = _cutoff_store(tmp_path, cleared_answer=cleared_answer)
    try:
        events = store.get_events("worker")
        agent = _cutoff_agent(store, max_tokens=max_tokens)
        before = _snapshot(agent)
        candidate = prepare_raw_prefix_candidate(
            agent, UserMessageSelector(events[-1]["event_id"], 2, 1)
        )
        contents = [message.get_text_content() for message in candidate.messages]
        assert candidate.context_cutoff_event_id == cutoff
        assert "CLEARED_USER" not in contents
        assert not any("CLEARED_ANSWER" in content for content in contents)
        assert candidate.messages[candidate.target_index].content == "TARGET"
        assert candidate.compact_state == {"compact_count": 0, "last_compact_time": 0.0}
        assert agent.compact_manager.config.max_tokens == max_tokens
        assert _snapshot(agent) == before
    finally:
        store.close()


def test_rejected_cutoff_candidate_does_not_replay_cleared_history(tmp_path):
    """An oversized cutoff candidate refuses without replaying cleared history."""
    store, cutoff = _cutoff_store(
        tmp_path, checkpoint_system="CHECKPOINT_PREAMBLE" * 100
    )
    try:
        events = store.get_events("worker")
        agent = _cutoff_agent(store, max_tokens=50)
        before = _snapshot(agent)
        with pytest.raises(ConflictError, match="context budget"):
            prepare_raw_prefix_candidate(
                agent, UserMessageSelector(events[-1]["event_id"], 2, 1)
            )
        assert _snapshot(agent) == before
    finally:
        store.close()


# ── rejected cutoff candidates keep their cutoff ──────────────────


def _watermark_cutoff_store(tmp_path, checkpoint_messages):
    """Real store: cleared turn 1, watermark-bearing cutoff3 checkpoint, target."""
    store = SessionStore(tmp_path / "cutoff_watermark.kohakutr")
    store.append_event(
        "worker", "user_message", {"content": "CLEARED_USER"}, turn_index=1, branch_id=1
    )
    store.append_event(
        "worker", "text_chunk", {"content": "CLEARED_ANSWER"}, turn_index=1, branch_id=1
    )
    store.append_event("worker", "context_cleared", {}, turn_index=1, branch_id=1)
    cutoff = store.max_event_id("worker")
    store.append_event(
        "worker",
        "user_message",
        {"content": "U2"},
        turn_index=2,
        branch_id=1,
        parent_branch_path=[(1, 1)],
    )
    store.append_event(
        "worker",
        "text_chunk",
        {"content": "R2"},
        turn_index=2,
        branch_id=1,
        parent_branch_path=[(1, 1)],
    )
    store.save_branch_snapshot(
        "worker",
        checkpoint_messages,
        branch={
            "turn_index": 2,
            "branch_id": 1,
            "parent_branch_path": [(1, 1)],
            "context_cutoff_event_id": cutoff,
        },
        watermark=store.max_event_id("worker"),
        compact_state={"compact_count": 0, "last_compact_time": 0.0},
    )
    target_id = store.append_event(
        "worker",
        "user_message",
        {"content": "TARGET"},
        turn_index=3,
        branch_id=1,
        parent_branch_path=[(1, 1), (2, 1)],
    )[1]
    return store, cutoff, target_id


def test_rejected_watermark_cutoff_checkpoint_never_admits_a_cutoff0_prefix(tmp_path):
    """A rejected watermark checkpoint cannot fall back past its cutoff."""
    checkpoint_messages = [
        {
            "role": "user",
            "content": "U2",
            "metadata": {"turn_index": 2, "branch_id": 1},
        },
        {"role": "assistant", "content": "OVERSIZED" * 1000},
    ]
    store, cutoff, target_id = _watermark_cutoff_store(tmp_path, checkpoint_messages)
    try:
        agent = _cutoff_agent(store, max_tokens=100)
        before = _snapshot(agent)
        with pytest.raises(ConflictError, match="context budget"):
            prepare_raw_prefix_candidate(agent, UserMessageSelector(target_id, 3, 1))
        assert _snapshot(agent) == before
    finally:
        store.close()


def test_same_cutoff_checkpoint_retry_is_admitted_after_a_rejection():
    """A smaller same-cutoff reconstruction preserves retained history."""
    events = [
        _event(1, "user_message", turn=1, content="CLEARED_USER"),
        _event(2, "text_chunk", turn=1, content="CLEARED_ANSWER"),
        _event(3, "context_cleared", turn=1),
        _event(4, "user_message", turn=2, content="U2", path=[[1, 1]]),
        _event(5, "text_chunk", turn=2, content="R2", path=[[1, 1]]),
        _event(6, "user_message", turn=3, content="target", path=[[1, 1], [2, 1]]),
    ]
    big = {
        "path": [[1, 1], [2, 1]],
        "watermark": 5,
        "messages": [
            {
                "role": "user",
                "content": "U2",
                "metadata": {"turn_index": 2, "branch_id": 1},
            },
            {"role": "assistant", "content": "OVERSIZED" * 1000},
        ],
        "compact_state": {"compact_count": 0, "last_compact_time": 0.0},
        "context_cutoff_event_id": 3,
    }
    small = {
        "path": [[1, 1], [2, 1]],
        "watermark": 3,
        "messages": [
            {
                "role": "user",
                "content": "U2",
                "metadata": {"turn_index": 2, "branch_id": 1},
            },
            {"role": "assistant", "content": "SMALL_CUTOFF_ANSWER"},
        ],
        "compact_state": {"compact_count": 0, "last_compact_time": 0.0},
        "context_cutoff_event_id": 3,
    }
    agent = _checkpoint_agent(events, [big, small], max_tokens=100)
    before = _snapshot(agent)
    candidate = prepare_raw_prefix_candidate(agent, UserMessageSelector(6, 3, 1))
    contents = [message.get_text_content() for message in candidate.messages]
    assert candidate.context_cutoff_event_id == 3
    assert "U2" in contents
    assert "R2" in contents
    assert "CLEARED_USER" not in contents
    assert "CLEARED_ANSWER" not in contents
    assert candidate.messages[candidate.target_index].content == "target"
    # Retrying never installs state and never widens the budget.
    assert _snapshot(agent) == before
    assert agent.compact_manager.config.max_tokens == 100


def test_weaker_cutoff_checkpoint_is_not_admitted_after_a_cutoff_rejection():
    """A rejected cutoff checkpoint cannot be replaced by a weaker cutoff."""
    events = [
        _event(1, "user_message", turn=1, content="CLEARED_USER"),
        _event(2, "text_chunk", turn=1, content="CLEARED_ANSWER"),
        _event(3, "context_cleared", turn=1),
        _event(4, "user_message", turn=2, content="U2", path=[[1, 1]]),
        _event(5, "text_chunk", turn=2, content="R2", path=[[1, 1]]),
        _event(6, "user_message", turn=3, content="target", path=[[1, 1], [2, 1]]),
    ]
    cutoff_record = {
        "path": [[1, 1], [2, 1]],
        "watermark": 5,
        "messages": [
            {
                "role": "user",
                "content": "U2",
                "metadata": {"turn_index": 2, "branch_id": 1},
            },
            {"role": "assistant", "content": "OVERSIZED" * 1000},
        ],
        "compact_state": {"compact_count": 0, "last_compact_time": 0.0},
        "context_cutoff_event_id": 3,
    }
    weak_record = {
        "path": [[1, 1], [2, 1]],
        "watermark": 3,
        "messages": [
            {
                "role": "user",
                "content": "U2",
                "metadata": {"turn_index": 2, "branch_id": 1},
            },
            {"role": "assistant", "content": "WEAK_ANSWER"},
        ],
        "compact_state": {"compact_count": 0, "last_compact_time": 0.0},
    }
    agent = _checkpoint_agent(events, [cutoff_record, weak_record], max_tokens=100)
    before = _snapshot(agent)
    with pytest.raises(ConflictError, match="context budget"):
        prepare_raw_prefix_candidate(agent, UserMessageSelector(6, 3, 1))
    assert _snapshot(agent) == before
