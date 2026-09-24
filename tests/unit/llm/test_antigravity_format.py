import json

import pytest

from kohakuterrarium.llm.antigravity_auth import AntigravityError
from kohakuterrarium.llm.antigravity_format import (
    STATE_KEY,
    encode_messages,
    signed_state,
)


@pytest.mark.parametrize("upstream_ids", [(), ("a",), ("a", "b")])
def test_signed_tool_roundtrip_orders_results_by_id(upstream_ids):
    calls = [
        {
            "id": key,
            "type": "function",
            "function": {"name": "echo", "arguments": json.dumps({"value": key})},
        }
        for key in ("a", "b")
    ]
    parts = [
        {"thoughtSignature": "signature-only"},
        *[
            {
                "functionCall": {
                    "name": "echo",
                    "args": {"value": key},
                    **({"id": key} if key in upstream_ids else {}),
                },
                "thoughtSignature": "sig-" + key,
            }
            for key in ("a", "b")
        ],
    ]
    message = {"role": "assistant", "content": "", "tool_calls": calls}
    message[STATE_KEY] = signed_state(message, parts, "gemini-3-flash", "account")
    system, contents = encode_messages(
        [
            {"role": "system", "content": "Be brief"},
            {"role": "user", "content": "echo"},
            message,
            {"role": "tool", "tool_call_id": "b", "content": "second"},
            {"role": "tool", "tool_call_id": "a", "content": "first"},
        ],
        "gemini-3-flash",
        "account",
    )
    assert system == {"parts": [{"text": "Be brief"}]}
    assert contents[1]["parts"] == parts
    assert contents[2]["parts"] == [
        {
            "functionResponse": {
                "name": "echo",
                "response": {"output": output},
                **({"id": key} if key in upstream_ids else {}),
            }
        }
        for key, output in (("a", "first"), ("b", "second"))
    ]
    message["tool_calls"] = calls[:1]
    with pytest.raises(AntigravityError, match="history_requires_new_session"):
        encode_messages([message], "gemini-3-flash", "account")


def test_text_edit_or_account_switch_does_not_replay_old_content():
    message = {"role": "assistant", "content": "original"}
    message[STATE_KEY] = signed_state(
        message,
        [
            {"text": "secret thought", "thought": True},
            {"text": "original", "thoughtSignature": "sig"},
        ],
        "m",
        "a",
    )
    message["content"] = "edited"
    _, contents = encode_messages([message], "m", "a")
    assert contents == [{"role": "model", "parts": [{"text": "edited"}]}]
    message["content"] = "original"
    _, contents = encode_messages([message], "m", "b")
    assert contents == [{"role": "model", "parts": [{"text": "original"}]}]


@pytest.mark.parametrize(
    "messages",
    [
        [{"role": "tool", "tool_call_id": "missing", "content": "x"}],
        [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": "https://example.org/image"},
                    }
                ],
            }
        ],
    ],
)
def test_unsupported_input_is_not_silently_dropped(messages):
    with pytest.raises(AntigravityError):
        encode_messages(messages, "m", "a")


@pytest.mark.parametrize(
    "model",
    ["claude-sonnet-4-6", "claude-opus-4-6-thinking", "gemini-3.8-flash-tiered"],
)
def test_streamed_claude_parts_are_rebuilt_before_signed_tool_replay(model):
    raw = [
        {"text": ""},
        {"text": "Let me ", "thought": True},
        {"text": "use the tool.", "thought": True},
        {"text": "", "thought": True, "thoughtSignature": "opaque-signature"},
        {"text": "I will "},
        {"text": "call it."},
        {"functionCall": {"id": "server-1", "name": "echo", "args": {}}},
        {"text": ""},
    ]
    message = {
        "role": "assistant",
        "content": "I will call it.",
        "tool_calls": [
            {
                "id": "server-1",
                "type": "function",
                "function": {"name": "echo", "arguments": "{}"},
            }
        ],
    }
    message[STATE_KEY] = signed_state(message, raw, model, "scope")
    _, contents = encode_messages(
        [message, {"role": "tool", "tool_call_id": "server-1", "content": "ok"}],
        model,
        "scope",
    )
    expected = (
        [
            {
                "text": "Let me use the tool.",
                "thought": True,
                "thoughtSignature": "opaque-signature",
            },
            {"text": "I will call it."},
            raw[6],
        ]
        if model.startswith("claude-")
        else raw
    )
    assert contents[0]["parts"] == expected
    assert message[STATE_KEY]["parts"] == raw
    assert contents[1]["parts"][0]["functionResponse"]["id"] == "server-1"


def test_claude_does_not_merge_distinct_signed_blocks_or_replay_unsigned_thoughts():
    parts = [
        {"text": "first", "thought": True, "thoughtSignature": "sig-a"},
        {"text": "second", "thought": True},
        {"text": "", "thought": True, "thoughtSignature": "sig-b"},
        {"text": "unsigned", "thought": True},
        {"text": "answer", "thoughtSignature": "text-sig"},
        {"text": " more"},
    ]
    message = {"role": "assistant", "content": "answer more"}
    message[STATE_KEY] = signed_state(message, parts, "claude-sonnet-4-6", "scope")
    _, contents = encode_messages([message], "claude-sonnet-4-6", "scope")
    assert contents[0]["parts"] == [
        parts[0],
        {"text": "second", "thought": True, "thoughtSignature": "sig-b"},
        parts[4],
        parts[5],
    ]


def test_claude_signed_thought_without_text_fails_closed():
    message = {"role": "assistant", "content": "answer"}
    message[STATE_KEY] = signed_state(
        message,
        [{"text": "", "thought": True, "thoughtSignature": "orphan-signature"}],
        "claude-sonnet-4-6",
        "scope",
    )
    with pytest.raises(AntigravityError, match="history_requires_new_session"):
        encode_messages([message], "claude-sonnet-4-6", "scope")
