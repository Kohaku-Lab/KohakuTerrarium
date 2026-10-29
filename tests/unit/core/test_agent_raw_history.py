"""Tests for canonical raw-history mutation prefixes."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from kohakuterrarium.core.agent_raw_history import (
    _accepted_native_prefix,
    prepare_raw_prefix_for_target,
    reload_raw_prefix_for_target,
)
from kohakuterrarium.core.conversation import Conversation, ConversationConfig
from kohakuterrarium.core.message_locator import user_message_indices_for_turn
from kohakuterrarium.session.raw_history import (
    UserMessageSelector,
    select_raw_history_prefix,
)


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


def test_prepare_and_adopt_are_distinct_and_nested_source_is_detached():
    events = [
        _event(1, "user_message", turn=1, content="original"),
        _event(2, "text_chunk", turn=1, content="answer"),
        _event(3, "user_message", turn=2, content="target", path=[[1, 1]]),
    ]
    agent = _agent(events)
    original = agent.controller.conversation
    original.get_messages()[0].metadata["nested"] = {"values": [1]}
    before = deepcopy(original.to_messages(include_metadata=True))
    prepared = prepare_raw_prefix_for_target(agent, UserMessageSelector(3, 2, 1))
    assert agent.controller.conversation is original
    assert original.to_messages(include_metadata=True) == before
    assert (agent._turn_index, agent._branch_id, agent._parent_branch_path) == (
        9,
        4,
        [(8, 4)],
    )
    source = deepcopy(prepared.conversation.to_messages(include_metadata=True))
    original.get_messages()[0].metadata["nested"]["values"].append(2)
    assert prepared.conversation.to_messages(include_metadata=True) == source
    adopted = prepared.adopt(agent)
    adopted.get_messages()[-1].metadata["branch_id"] = 99
    adopted.truncate_from(2)
    assert prepared.conversation.to_messages(include_metadata=True) == source
    assert prepared.view == {1: 1, 2: 1}
    with pytest.raises(ValueError):
        prepare_raw_prefix_for_target(agent, UserMessageSelector(3, 2, 99))
    assert agent.controller.conversation is adopted
    assert adopted.get_messages()[-1].content == "original"


def test_reload_restores_missing_system_prompt_and_canonical_user_metadata():
    events = [
        _event(1, "user_message", turn=1, content="same"),
        _event(2, "text_chunk", turn=1, content="first reply"),
        _event(3, "user_message", turn=2, content="same", path=[[1, 1]]),
        _event(4, "text_chunk", turn=2, content="discarded tail", path=[[1, 1]]),
    ]
    agent = _agent(events)

    reload_raw_prefix_for_target(
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

    reload_raw_prefix_for_target(
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

    reload_raw_prefix_for_target(
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

    reload_raw_prefix_for_target(
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

    reload_raw_prefix_for_target(
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

    reload_raw_prefix_for_target(
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

    reload_raw_prefix_for_target(
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
    reload_raw_prefix_for_target(
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
    reload_raw_prefix_for_target(
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
    reload_raw_prefix_for_target(
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
    reload_raw_prefix_for_target(
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


def _accepted_summary_agent():
    events = [
        _event(1, "user_message", turn=1, content="original"),
        {
            **_event(3, "compact_complete", turn=1),
            "round": 1,
            "summary": "accepted summary",
            "compact_source_path": None,
            "compact_result_event_ids": [2],
        },
        _event(5, "user_message", turn=2, content="target", path=[[1, 1]]),
    ]
    agent = _agent(events)
    agent._turn_index, agent._branch_id = 2, 1
    agent._parent_branch_path = [(1, 1)]
    agent._context_cutoff_event_id = 1
    agent.compact_manager = SimpleNamespace(_compact_count=1, _last_compact_time=42.0)
    conversation = agent.controller.conversation
    conversation.truncate_from(1)
    conversation.append(
        "assistant",
        "[Previous context summary (compact round 1)]\n\naccepted summary",
        metadata={
            "compact_summary": True,
            "compact_source_path": None,
            "compact_result_event_ids": [2],
        },
    )
    conversation.append(
        "user", "target", metadata={"turn_index": 2, "branch_id": 1, "event_id": 5}
    )
    conversation.append("assistant", "discarded response")
    return agent, events, UserMessageSelector(5, 2, 1)


@pytest.mark.parametrize("retained_event_id", [4, 5])
def test_reload_preserves_detached_unknown_summary_idempotently(retained_event_id):
    agent, events, selector = _accepted_summary_agent()
    agent.controller.conversation.get_messages()[2].metadata[
        "event_id"
    ] = retained_event_id
    original = agent.controller.conversation
    expected = deepcopy(original.to_messages(include_metadata=True)[:3])
    saved_events = deepcopy(events)
    for _ in range(2):
        reload_raw_prefix_for_target(agent, selector, branch_view={1: 1, 2: 1})
        assert (
            agent.controller.conversation.to_messages(include_metadata=True) == expected
        )
        assert agent.controller.conversation is not original
        assert agent.controller.conversation.config is original.config
        assert agent._context_cutoff_event_id == 1
        assert agent.compact_manager._compact_count == 1
        assert agent.compact_manager._last_compact_time == 42.0
    agent.controller.conversation.get_messages()[1].metadata[
        "compact_result_event_ids"
    ].append(99)
    assert original.get_messages()[1].metadata["compact_result_event_ids"] == [2]
    assert len(original.get_messages()) == 4
    assert events == saved_events


@pytest.mark.parametrize(
    "missing",
    [
        "completion",
        "later_completion",
        "ambiguous_completion",
        "summary_text",
        "summary_metadata",
        "ancestor_branch",
        "target_branch",
        "cutoff",
        "missing_target",
        "duplicate_target",
        "duplicate_persisted_target",
    ],
)
def test_native_prefix_falls_back_without_unambiguous_earlier_evidence(missing):
    agent, events, selector = _accepted_summary_agent()
    messages = agent.controller.conversation.get_messages()
    if missing == "completion":
        events.pop(1)
    elif missing == "later_completion":
        # The target remains after the summary in memory, as with reattachment.
        events[1]["event_id"] = 6
        events.sort(key=lambda event: event["event_id"])
    elif missing == "ambiguous_completion":
        events.insert(2, {**events[1], "event_id": 4})
    elif missing == "summary_text":
        events[1]["summary"] = "different summary"
    elif missing == "summary_metadata":
        events[1].pop("compact_source_path")
    elif missing == "ancestor_branch":
        agent._parent_branch_path = [(1, 2)]
    elif missing == "target_branch":
        agent._branch_id = 2
    elif missing == "cutoff":
        agent._context_cutoff_event_id = selector.event_id
    elif missing == "missing_target":
        messages[2].metadata.clear()
    elif missing == "duplicate_target":
        agent.controller.conversation.append_message(deepcopy(messages[2]))
    elif missing == "duplicate_persisted_target":
        events.append({**events[-1], "event_id": 6})
    prefix = select_raw_history_prefix(events, selector=selector)
    before = deepcopy(agent.controller.conversation.to_messages(include_metadata=True))
    assert _accepted_native_prefix(agent, prefix, events) is None
    assert agent.controller.conversation.to_messages(include_metadata=True) == before
