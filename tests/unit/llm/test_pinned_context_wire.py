"""Provider formatters accept a pinned user message right after system.

Pinned context puts a user message (text plus image) between the system
prompt and the next user message, so every formatter must keep both user
turns and the image.
"""

import pytest

from kohakuterrarium.core.pinned import PinnedContext
from kohakuterrarium.llm.anthropic_format import prepare_messages
from kohakuterrarium.llm.antigravity_format import encode_messages
from kohakuterrarium.llm.codex_format import to_responses_input
from kohakuterrarium.llm.message import ImagePart, TextPart
from kohakuterrarium.llm.openai import OpenAIProvider

pytestmark = pytest.mark.real_llm_provider

PIXEL = (
    "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42"
    "mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="
)


def _messages() -> list[dict]:
    pins = PinnedContext()
    pins.pin("persona", "user", [TextPart(text="PINNED"), ImagePart(url=PIXEL)])
    return pins.apply(
        [
            {"role": "system", "content": "SYSTEM"},
            {"role": "user", "content": "hello"},
        ]
    )


def test_openai_chat_keeps_both_user_turns_and_the_image():
    provider = OpenAIProvider(api_key="sk-test", model="gpt-test")
    wire = provider._prepare_messages(_messages())
    assert [m["role"] for m in wire] == ["system", "user", "user"]
    assert [p["type"] for p in wire[1]["content"]] == ["text", "image_url"]
    assert wire[2]["content"] == "hello"


def test_openai_compatible_anthropic_endpoint_keeps_both_user_turns():
    provider = OpenAIProvider(
        api_key="sk-test", model="claude-test", base_url="https://api.anthropic.com/v1"
    )
    wire = provider._prepare_messages(_messages())
    assert [m["role"] for m in wire] == ["system", "user", "user"]
    assert "PINNED" in str(wire[1]["content"]) and "hello" in str(wire[2]["content"])


def test_anthropic_messages_keep_both_user_turns_and_the_image():
    system, body = prepare_messages(_messages())
    assert system == "SYSTEM"
    assert [m["role"] for m in body] == ["user", "user"]
    assert [b["type"] for b in body[0]["content"]] == ["text", "image"]
    assert "hello" in str(body[1]["content"])


def test_responses_input_keeps_both_user_items_and_the_image():
    items = to_responses_input(_messages())
    users = [item for item in items if item.get("role") == "user"]
    assert len(users) == 2
    assert [p["type"] for p in users[0]["content"]] == ["input_text", "input_image"]
    assert "hello" in str(users[1]["content"])


def test_antigravity_contents_keep_both_user_turns_and_the_image():
    system, contents = encode_messages(_messages(), "gemini-test", "scope")
    assert "SYSTEM" in str(system)
    texts = str(contents)
    assert "PINNED" in texts and "hello" in texts
    assert any("inlineData" in str(c) or "inline_data" in str(c) for c in contents)


def test_litellm_params_keep_both_user_turns():
    pytest.importorskip("litellm")
    from kohakuterrarium.llm.litellm_provider import LiteLLMProvider

    provider = LiteLLMProvider(model="gpt-test", api_key="sk-test")
    params = provider._build_params(_messages())
    assert [m["role"] for m in params["messages"]] == ["system", "user", "user"]
