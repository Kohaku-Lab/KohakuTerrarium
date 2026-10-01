"""Emergency recovery preserves internal identity without leaking it to providers."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from kohakuterrarium.core.agent_budget_recovery import sync_emergency_drop_conversation
from kohakuterrarium.core.conversation import Conversation
from kohakuterrarium.llm.recovery import drop_last_tool_round


def _conversation():
    conv = Conversation()
    conv.append("system", "system", metadata={"kind": "system"})
    conv.append("tool", "orphan", tool_call_id="missing", metadata={"turn_index": 0})
    conv.append("user", "repeat", metadata={"turn_index": 1, "branch_id": 1})
    for number in (1, 2):
        identity = {"turn_index": number, "branch_id": number, "kind": "tool_feedback"}
        conv.append(
            "assistant",
            "",
            tool_calls=[
                {
                    "id": f"call_{number}",
                    "type": "function",
                    "function": {"name": "read[abcdef]", "arguments": "{}"},
                }
            ],
            metadata=identity,
            extra_fields={"reasoning_content": f"thought {number}"},
        )
        conv.append(
            "tool",
            "result",
            name="read[abcdef]",
            tool_call_id=f"call_{number}",
            metadata=identity,
        )
    conv.append("user", "repeat", metadata={"turn_index": 3, "branch_id": 2})
    return conv


def _sync(conv, recovered):
    agent = SimpleNamespace(controller=SimpleNamespace(conversation=conv))
    sync_emergency_drop_conversation(agent, recovered)
    assert agent.controller.conversation is conv


def test_preserves_surviving_identity_and_snapshot_round_trip():
    conv = _conversation()
    original = deepcopy(conv.snapshot_messages())
    request = conv.to_messages()
    assert all("metadata" not in msg for msg in request)
    dropped, recovered = drop_last_tool_round(request)
    assert dropped == 1
    untouched = deepcopy(recovered)
    _sync(conv, recovered)

    expected = [msg["metadata"] for msg in original[:4]] + [
        {},
        original[-1]["metadata"],
    ]
    assert [msg.metadata for msg in conv.get_messages()] == expected
    assert conv.to_messages() == recovered
    assert recovered == untouched
    assert conv.get_messages()[2].extra_fields == {"reasoning_content": "thought 1"}
    assert conv.get_messages()[3].name == "read"
    assert conv.get_messages()[4].content.startswith("[tool-result truncated]")
    assert all("metadata" not in msg.extra_fields for msg in conv.get_messages())
    assert (
        Conversation.from_json(conv.to_json()).snapshot_messages()
        == conv.snapshot_messages()
    )

    _, second = drop_last_tool_round(conv.to_messages())
    _sync(conv, second)
    assert [msg.metadata for msg in conv.get_messages()] == [
        expected[0],
        expected[1],
        {},
        {},
        expected[-1],
    ]
    assert all("metadata" not in msg for msg in conv.to_messages())


@pytest.mark.parametrize("change", ["injection", "rewrite", "reorder"])
def test_does_not_guess_identity_when_recovered_request_differs(change):
    conv = _conversation()
    _, recovered = drop_last_tool_round(conv.to_messages())
    if change == "injection":
        recovered.insert(1, {"role": "user", "content": "plugin context"})
    elif change == "rewrite":
        recovered[-1] = {**recovered[-1], "content": "rewritten"}
    else:
        recovered[0], recovered[1] = recovered[1], recovered[0]
    _sync(conv, recovered)
    assert conv.to_messages() == recovered
    assert all(msg.metadata == {} for msg in conv.get_messages())
