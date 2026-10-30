"""Legacy snapshot repair preserves context and provider identity."""

from copy import deepcopy
from types import SimpleNamespace

from kohakuterrarium.core.agent.history.message_history import (
    apply_prepared_branch_view,
)
from kohakuterrarium.core.conversation.conversation import Conversation
from kohakuterrarium.llm.message import FilePart, ImagePart, TextPart
from kohakuterrarium.session.resume_build import build_conversation


def test_snapshot_repairs_labels_without_replaying_away_summary():
    messages = [
        {"role": "system", "content": "compacted summary"},
        None,
        {"role": "user", "content": "continue"},
        {
            "role": "assistant",
            "content": "",
            "reasoning_content": "keep reasoning",
            "tool_calls": [
                {
                    "id": "call_provider",
                    "type": "function",
                    "function": {
                        "name": "web_search[abc123]",
                        "arguments": '{"q":"x"}',
                    },
                }
            ],
        },
        {
            "role": "tool",
            "content": "result",
            "tool_call_id": "call_provider",
            "name": "web_search[abc123]",
        },
    ]
    restored = build_conversation(messages).to_messages()
    assert restored[0]["content"] == "compacted summary"
    assert restored[2]["reasoning_content"] == "keep reasoning"
    call = restored[2]["tool_calls"][0]
    assert call["id"] == restored[3]["tool_call_id"] == "call_provider"
    assert call["function"]["name"] == restored[3]["name"] == "web_search"
    assert call["function"]["arguments"] == '{"q":"x"}'
    assert messages[3]["tool_calls"][0]["function"]["name"] == "web_search[abc123]"


def test_cold_build_and_branch_install_restore_multimodal_runtime():
    parts = [
        ImagePart(url="data:image/png;base64,QUJD", source_name="x.png").to_dict(),
        TextPart(text="caption").to_dict(),
        FilePart(
            name="x.txt", content="attached", mime="text/plain", is_inline=True
        ).to_dict(),
    ]
    metadata = {"event_id": 1, "turn_index": 1, "branch_id": 2}
    messages = [
        {"role": "system", "content": "system"},
        {"role": "user", "content": parts, "metadata": metadata},
        {
            "role": "assistant",
            "content": "answer",
            "reasoning_content": "opaque",
            "_kt_tool_call_extra": {"call_x": {"signature": "provider-state"}},
        },
    ]
    original = deepcopy(messages)
    cold = build_conversation(messages)
    agent = SimpleNamespace(
        controller=SimpleNamespace(conversation=Conversation()), compact_manager=None
    )
    apply_prepared_branch_view(agent, {"messages": messages, "selected": {1: 2}})
    for conv in (cold, agent.controller.conversation):
        user = next(m for m in conv.get_messages() if m.role == "user")
        assert user.get_text_content() == "caption"
        assert user.has_images()
        assert [type(p) for p in user.content] == [ImagePart, TextPart, FilePart]
        assert user.content[2].content == "attached"
        assert user.metadata == metadata
        saved = conv.snapshot_messages()
        assert next(m for m in saved if m["role"] == "user")["content"] == parts
        assistant = next(m for m in saved if m["role"] == "assistant")
        assert assistant["reasoning_content"] == "opaque"
        assert assistant["_kt_tool_call_extra"] == messages[-1]["_kt_tool_call_extra"]
    assert messages == original
