"""Tests for Anthropic prompt-caching auto-wiring (F.2 extension point)."""

import pytest

from kohakuterrarium.llm.anthropic_cache import (
    apply_anthropic_cache_markers,
    is_anthropic_endpoint,
)
from kohakuterrarium.llm.openai import OpenAIProvider

# ---------------------------------------------------------------------------
# is_anthropic_endpoint heuristics
# ---------------------------------------------------------------------------


def test_is_anthropic_endpoint_by_url():
    assert is_anthropic_endpoint("https://api.anthropic.com/v1/", None) is True
    assert is_anthropic_endpoint("https://API.ANTHROPIC.COM/v1/", None) is True


def test_is_anthropic_endpoint_by_provider_name():
    assert is_anthropic_endpoint("https://openrouter.ai/api/v1", "anthropic") is True


def test_is_anthropic_endpoint_false_for_others():
    assert is_anthropic_endpoint("https://api.openai.com/v1", None) is False
    assert is_anthropic_endpoint("https://openrouter.ai/api/v1", "openrouter") is False
    assert is_anthropic_endpoint(None, None) is False


# ---------------------------------------------------------------------------
# apply_anthropic_cache_markers — pure helper
# ---------------------------------------------------------------------------


def test_empty_messages_pass_through():
    assert apply_anthropic_cache_markers([]) == []


def test_string_system_converted_to_parts_and_marked():
    messages = [{"role": "system", "content": "you are helpful"}]
    out = apply_anthropic_cache_markers(messages)
    # Original untouched (deep-copy)
    assert messages[0]["content"] == "you are helpful"
    # New list-of-parts form with cache_control on the single text part
    sys = out[0]
    assert isinstance(sys["content"], list)
    assert sys["content"][0]["type"] == "text"
    assert sys["content"][0]["text"] == "you are helpful"
    assert sys["content"][0]["cache_control"] == {"type": "ephemeral"}


def test_list_content_marks_last_text_part():
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "first"},
                {"type": "text", "text": "second"},
            ],
        }
    ]
    out = apply_anthropic_cache_markers(messages)
    parts = out[0]["content"]
    assert "cache_control" not in parts[0]
    assert parts[1]["cache_control"] == {"type": "ephemeral"}


def test_system_plus_last_three_messages_marked():
    messages = [
        {"role": "system", "content": "SYS"},
        {"role": "user", "content": "u1"},
        {"role": "assistant", "content": "a1"},
        {"role": "user", "content": "u2"},
        {"role": "assistant", "content": "a2"},
        {"role": "user", "content": "u3"},
    ]
    out = apply_anthropic_cache_markers(messages)

    def _marked(msg):
        c = msg["content"]
        if isinstance(c, list) and c:
            return c[-1].get("cache_control") == {"type": "ephemeral"}
        return False

    assert _marked(out[0])  # system
    assert not _marked(out[1])  # u1 (too far back)
    assert not _marked(out[2])  # a1 (too far back)
    assert _marked(out[3])  # u2
    assert _marked(out[4])  # a2
    assert _marked(out[5])  # u3


def test_tool_messages_are_skipped_in_window():
    messages = [
        {"role": "system", "content": "SYS"},
        {"role": "user", "content": "u1"},
        {"role": "assistant", "content": "call"},
        {"role": "tool", "content": "tool result", "tool_call_id": "t1"},
        {"role": "assistant", "content": "a2"},
        {"role": "user", "content": "u3"},
    ]
    out = apply_anthropic_cache_markers(messages)

    def _marked(msg):
        c = msg["content"]
        if isinstance(c, list) and c:
            return c[-1].get("cache_control") == {"type": "ephemeral"}
        return False

    # system always marked
    assert _marked(out[0])
    # The last 3 non-tool-non-system anchors should be: u1(1), assistant(call)(2),
    # assistant(a2)(4), user(u3)(5). With 3 tail slots left → we mark the last 3,
    # i.e. "call", "a2", "u3".
    assert not _marked(out[1])  # u1
    assert _marked(out[2])  # "call"
    # Tool message content is untouched (never got a marker)
    assert out[3]["content"] == "tool result"
    assert _marked(out[4])  # a2
    assert _marked(out[5])  # u3


def test_empty_content_is_left_alone():
    messages = [
        {"role": "system", "content": ""},
        {"role": "user", "content": None},
    ]
    out = apply_anthropic_cache_markers(messages)
    # System got no marker — content stayed empty string
    assert out[0]["content"] == ""
    assert out[1]["content"] is None


def test_input_is_not_mutated():
    messages = [{"role": "system", "content": "SYS"}]
    snapshot = [dict(m) for m in messages]
    _ = apply_anthropic_cache_markers(messages)
    # original untouched
    assert messages == snapshot


# ---------------------------------------------------------------------------
# OpenAIProvider integration — messages transformed only for Anthropic
# ---------------------------------------------------------------------------


@pytest.fixture()
def anthropic_provider():
    return OpenAIProvider(
        api_key="test-key",
        model="claude-opus-4.7",
        base_url="https://api.anthropic.com/v1",
    )


@pytest.fixture()
def openai_provider():
    return OpenAIProvider(
        api_key="test-key",
        model="gpt-4o",
        base_url="https://api.openai.com/v1",
    )


def test_openai_provider_passes_messages_through_unchanged(openai_provider):
    messages = [
        {"role": "system", "content": "SYS"},
        {"role": "user", "content": "u1"},
    ]
    prepared = openai_provider._prepare_messages(messages)
    # Same objects (no deep-copy when not Anthropic)
    assert prepared is messages
    assert messages[0] == {"role": "system", "content": "SYS"}


def test_anthropic_provider_adds_cache_markers(anthropic_provider):
    messages = [
        {"role": "system", "content": "SYS"},
        {"role": "user", "content": "u1"},
    ]
    prepared = anthropic_provider._prepare_messages(messages)
    # Deep-copy: originals untouched
    assert messages[0]["content"] == "SYS"
    # System converted to structured parts with cache_control
    sys = prepared[0]
    assert isinstance(sys["content"], list)
    assert sys["content"][0]["cache_control"] == {"type": "ephemeral"}


def test_anthropic_opt_out_via_extra_body():
    provider = OpenAIProvider(
        api_key="test-key",
        model="claude-opus-4.7",
        base_url="https://api.anthropic.com/v1",
        extra_body={"disable_prompt_caching": True},
    )
    messages = [{"role": "system", "content": "SYS"}]
    prepared = provider._prepare_messages(messages)
    # No caching applied — passthrough identity
    assert prepared is messages
    assert prepared[0]["content"] == "SYS"


def test_sanitize_extra_body_strips_disable_flag(anthropic_provider):
    merged = {"disable_prompt_caching": True, "other": 1}
    cleaned = anthropic_provider._sanitize_extra_body(merged)
    assert cleaned == {"other": 1}
    # Input unchanged (defensive copy via dict-comp)
    assert merged == {"disable_prompt_caching": True, "other": 1}


def test_sanitize_extra_body_is_noop_when_flag_absent(anthropic_provider):
    merged = {"other": 1}
    cleaned = anthropic_provider._sanitize_extra_body(merged)
    assert cleaned is merged  # no copy needed
