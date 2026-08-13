"""Tests for reseating an agent on uncompacted persisted history."""

from types import SimpleNamespace

from kohakuterrarium.core.agent_raw_history import reload_raw_prefix_for_target
from kohakuterrarium.core.conversation import Conversation, ConversationConfig
from kohakuterrarium.core.message_locator import user_message_indices_for_turn
from kohakuterrarium.session.raw_history import UserMessageSelector


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


def test_reload_keeps_compact_baseline_for_compacted_target():
    # turn1-2 were compacted (replaced 1..4). Editing turn2 must keep the
    # summary for turn1 and materialize only the target — no resurrection of
    # the compacted prefix.
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
        ("assistant", "[S]"),
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
        _compact_event(7, 1, 4),
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
    # A compact that DOES cover a shared ancestor (e.g. path [[1,1],[2,1]])
    # is still kept — that is the existing baseline-preservation behavior.
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
    # Two compactions on branches that share a prefix (turns 1-2) then diverge
    # at turn 3. Editing branch-2's turn 3 must keep ONLY branch-2's compact
    # baseline — branch-1's summary is a foreign baseline that shares the same
    # ancestor prefix and must not stack on top of branch-2's.
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
        ("assistant", "[S2]"),
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


def test_reload_keeps_v2_compact_complete_baseline():
    # Runtime compaction persists compact_complete; editing a turn covered by
    # it must keep the baseline (summary) instead of resurrecting full history.
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
        ("assistant", "[S]"),
        ("user", "U2"),
    ]
