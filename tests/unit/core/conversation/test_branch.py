"""Behavior tests for detached branch conversation construction."""

import json
from copy import deepcopy
from types import SimpleNamespace

import pytest

from kohakuterrarium.core.agent.history.message_history import (
    apply_prepared_branch_view,
)
from kohakuterrarium.core.conversation.branch import build_branch_conversation
from kohakuterrarium.core.conversation.conversation import (
    Conversation,
    ConversationConfig,
)


@pytest.mark.parametrize("max_messages", [0, 3])
def test_candidate_preserves_active_prompts_metadata_and_provider_parts(max_messages):
    current = Conversation(ConversationConfig(max_messages=max_messages))
    current.append("system", "active prompt")
    current.append("user", "old input")
    messages = [
        {"role": "system", "content": "stale persisted prompt"},
        {
            "role": "user",
            "content": "branch input",
            "metadata": {"turn_index": 2, "branch_id": 3},
        },
        {
            "role": "assistant",
            "content": [
                {
                    "type": "image_url",
                    "image_url": {"url": "https://images.invalid/a", "vendor": "keep"},
                }
            ],
            "tool_calls": [
                {"id": "c1", "function": {"name": "echo", "arguments": "{}"}}
            ],
            "provider_state": {"nested": ["keep"]},
        },
        {
            "role": "tool",
            "content": "done",
            "tool_call_id": "c1",
            "name": "echo",
            "metadata": {"job_id": "j1", "tool_result_phase": "final"},
        },
    ]
    original = deepcopy(messages)
    candidate = build_branch_conversation(current, messages)
    assert current.to_messages() == [
        {"role": "system", "content": "active prompt"},
        {"role": "user", "content": "old input"},
    ]
    expected = [{"role": "system", "content": "active prompt"}, *messages[1:]]
    if max_messages:
        expected = [expected[0], *expected[-2:]]
    assert candidate.to_messages(include_metadata=True) == expected
    candidate_metadata = json.loads(candidate.to_json())["metadata"]
    assert candidate_metadata["message_count"] == len(candidate)
    assert candidate_metadata["total_chars"] == candidate.get_context_length()
    agent = SimpleNamespace(controller=SimpleNamespace(conversation=current))
    apply_prepared_branch_view(
        agent, {"messages": messages, "selected": {2: 3}}, candidate=candidate
    )
    assert current.get_messages() == candidate.get_messages()
    assert json.loads(current.to_json())["metadata"] == candidate_metadata
    candidate.get_messages()[-2].extra_fields["provider_state"]["nested"].append(
        "changed"
    )
    candidate.get_messages()[-1].metadata["job_id"] = "changed"
    assert messages == original
