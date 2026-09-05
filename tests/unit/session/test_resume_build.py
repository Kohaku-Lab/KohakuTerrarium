"""Legacy snapshot repair preserves context and provider identity."""

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
