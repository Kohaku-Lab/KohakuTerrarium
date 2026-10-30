"""Detached source ownership and branch preparation parity."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from kohakuterrarium.core.agent.history.message_history import (
    apply_prepared_branch_view,
)
from kohakuterrarium.core.conversation import Conversation
from kohakuterrarium.core.history_source import (
    PreparedHistorySource,
    prepare_branch_source,
)


def make_agent(*, elide=False):
    conversation = Conversation()
    conversation.append("system", "prompt", metadata={"nested": [[1, 2]]})
    conversation.append("user", "source", metadata={"nested": {"ids": [3]}})
    return SimpleNamespace(
        controller=SimpleNamespace(
            conversation=conversation, config=SimpleNamespace(elide_tool_results=elide)
        ),
        compact_manager=SimpleNamespace(
            _compact_count=2,
            _last_compact_time=10.0,
            config=SimpleNamespace(enabled=True, max_tokens=100, threshold=0.5),
        ),
        _turn_index=2,
        _branch_id=3,
        _parent_branch_path=[(1, 1)],
        _context_cutoff_event_id=4,
    )


@pytest.mark.parametrize("preserve_identity", [True, False])
def test_capture_and_adoption_detach_nested_contents(preserve_identity):
    agent = make_agent()
    original = agent.controller.conversation
    source = PreparedHistorySource.capture(agent)
    frozen = deepcopy(source.conversation.to_messages(include_metadata=True))
    original.get_messages()[0].metadata["nested"][0][1] = 9
    original.get_messages()[1].metadata["nested"]["ids"].append(9)
    agent._parent_branch_path.append((2, 7))
    agent._context_cutoff_event_id = 99
    agent.compact_manager._compact_count = 99
    assert source.conversation.to_messages(include_metadata=True) == frozen
    assert source.view == {1: 1, 2: 3}
    assert source.cutoff == 4
    assert source.compact_state == {"compact_count": 2, "last_compact_time": 10.0}
    destination = source.adopt(agent, preserve_identity=preserve_identity)
    assert (destination is original) == preserve_identity
    assert destination.to_messages(include_metadata=True) == frozen
    assert destination.config is original.config
    assert agent._parent_branch_path == [(1, 1)]
    assert (agent._turn_index, agent._branch_id, agent._context_cutoff_event_id) == (
        2,
        3,
        4,
    )
    assert agent.compact_manager._compact_count == 2
    destination.get_messages()[0].metadata["nested"][0][1] = 8
    destination.truncate_from(1)
    assert source.conversation.to_messages(include_metadata=True) == frozen


@pytest.mark.parametrize("elide", [False, True])
@pytest.mark.parametrize("selected", [{}, {1: 1, 2: 2}])
def test_prepared_branch_matches_existing_adoption_without_mutating_live(
    elide, selected
):
    agent = make_agent(elide=elide)
    control = make_agent(elide=elide)
    original = agent.controller.conversation
    before = deepcopy(original.to_messages(include_metadata=True))
    messages = [
        {"role": "user", "content": "run"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "c1",
                    "type": "function",
                    "function": {"name": "read", "arguments": "{}"},
                }
            ],
        },
        {
            "role": "tool",
            "content": "x" * 20000,
            "tool_call_id": "c1",
            "name": "read",
            "metadata": {"tool_result_phase": "final"},
        },
        {
            "role": "user",
            "content": "target",
            "metadata": {"turn_index": 2, "branch_id": 2},
        },
    ]
    prepared = {
        "messages": messages,
        "selected": selected,
        "context_cutoff_event_id": 7,
    }
    source = prepare_branch_source(
        agent, PreparedHistorySource.capture(agent), prepared
    )
    assert agent.controller.conversation is original
    assert original.to_messages(include_metadata=True) == before
    assert agent.compact_manager._compact_count == 2
    assert agent._context_cutoff_event_id == 4
    apply_prepared_branch_view(control, prepared)
    assert source.conversation.to_messages(
        include_metadata=True
    ) == control.controller.conversation.to_messages(include_metadata=True)
    source.adopt(agent)
    assert (agent._turn_index, agent._branch_id, agent._parent_branch_path) == (
        control._turn_index,
        control._branch_id,
        control._parent_branch_path,
    )
    assert (
        agent.compact_manager._compact_count == control.compact_manager._compact_count
    )
    assert agent._context_cutoff_event_id == control._context_cutoff_event_id


def test_capture_empty_agent_without_compact_manager():
    agent = SimpleNamespace(controller=SimpleNamespace(conversation=Conversation()))
    source = PreparedHistorySource.capture(agent)
    assert source.view == {}
    assert source.compact_state is None
    source.adopt(agent)
    assert agent._turn_index == agent._branch_id == agent._context_cutoff_event_id == 0
    assert agent.controller.conversation.to_messages() == []
