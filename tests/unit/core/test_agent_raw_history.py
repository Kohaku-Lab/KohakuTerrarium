"""Tests for reseating an agent on uncompacted persisted history."""

from types import SimpleNamespace

from kohakuterrarium.core.agent_raw_history import reload_raw_prefix_for_target
from kohakuterrarium.core.conversation import Conversation
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


def _agent(events):
    conversation = Conversation()
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
