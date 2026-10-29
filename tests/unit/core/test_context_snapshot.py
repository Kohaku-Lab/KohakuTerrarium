"""Detached context capture and provider-valid fork boundaries."""

from types import SimpleNamespace

from kohakuterrarium.core.context_snapshot import (
    ContextSnapshot,
    capture_agent_context,
    capture_context,
)
from kohakuterrarium.core.conversation import Conversation
from kohakuterrarium.core.pinned import PinnedContext


def test_snapshot_is_detached_and_roundtrips_provider_fields():
    conversation = Conversation()
    conversation.append("system", "instructions")
    message = conversation.append(
        "assistant", "answer", extra_fields={"reasoning_content": "trace"}
    )
    message.metadata = {"branch_id": 2, "nested": [1]}
    snapshot = capture_context(conversation, provenance={"branch_path": [[1, 2]]})
    message.content = "changed"
    message.metadata["nested"].append(2)
    exported = snapshot.messages
    exported[1]["content"] = "also changed"
    child = snapshot.conversation()
    assert child.to_messages()[1]["content"] == "answer"
    assert child.to_messages()[1]["reasoning_content"] == "trace"
    assert child.get_messages()[1].metadata["nested"] == [1]
    assert ContextSnapshot.from_dict(snapshot.to_dict()) == snapshot


def test_pending_native_group_and_later_context_are_excluded():
    conversation = Conversation()
    conversation.append("system", "system")
    conversation.append("user", "question")
    conversation.append(
        "assistant",
        "dispatch",
        tool_calls=[
            {
                "id": "fork-call",
                "type": "function",
                "function": {"name": "fork", "arguments": "{}"},
            }
        ],
    )
    message = conversation.append("tool", "pending", tool_call_id="fork-call")
    message.metadata = {"tool_result_phase": "pending"}
    conversation.append("user", "later")
    snapshot = capture_context(conversation)
    assert [message["content"] for message in snapshot.messages] == [
        "system",
        "question",
    ]
    assert len(conversation.get_messages()) == 5


def test_settled_tool_pairs_and_multimodal_references_survive():
    conversation = Conversation()
    conversation.append(
        "user", [{"type": "image_url", "image_url": {"url": "file:///image.png"}}]
    )
    conversation.append(
        "assistant",
        None,
        tool_calls=[
            {
                "id": "a",
                "type": "function",
                "function": {"name": "read", "arguments": "{}"},
            }
        ],
    )
    conversation.append("tool", "evidence", tool_call_id="a")
    child = capture_context(conversation).conversation()
    assert (
        child.to_messages()[0]["content"][0]["image_url"]["url"] == "file:///image.png"
    )
    assert child.to_messages()[2]["tool_call_id"] == "a"
    assert child.to_messages() == conversation.to_messages()


def test_agent_capture_carries_pins_right_after_system():
    conversation = Conversation()
    conversation.append("system", "S")
    conversation.append("user", "hi")
    pins = PinnedContext()
    pins.pin("persona", "user", "PIN")
    agent = SimpleNamespace(
        controller=SimpleNamespace(conversation=conversation, pinned=pins),
        config=SimpleNamespace(name="bot"),
        session_store=None,
        llm=SimpleNamespace(model="m"),
    )
    snapshot = capture_agent_context(agent)
    assert [m["content"] for m in snapshot.messages] == ["S", "PIN", "hi"]
    assert snapshot.messages[1]["metadata"] == {"pinned": "persona"}
    assert len(conversation) == 2
