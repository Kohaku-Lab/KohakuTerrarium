"""Unit tests for ``llm/anthropic_cache.py`` — prompt-cache marker placement.

Behavior-first: assert the exact ``cache_control`` placement (which
messages, which content part) and the documented 4-breakpoint cap, plus
the input-immutability contract the module's docstring promises.
"""

from kohakuterrarium.llm.anthropic_cache import (
    apply_anthropic_cache_markers,
    is_anthropic_endpoint,
)

_EPHEMERAL = {"type": "ephemeral"}


def _marked(msg):
    """Return True when msg's content carries a cache_control marker."""
    content = msg.get("content")
    if isinstance(content, list):
        return any(
            isinstance(p, dict) and p.get("cache_control") == _EPHEMERAL
            for p in content
        )
    return False


class TestApplyAnthropicCacheMarkers:
    def test_empty_messages_returned_unchanged(self):
        assert apply_anthropic_cache_markers([]) == []

    def test_input_list_never_mutated(self):
        messages = [{"role": "system", "content": "sys"}]
        apply_anthropic_cache_markers(messages)
        assert messages[0]["content"] == "sys"  # still a plain string

    def test_system_message_string_wrapped_and_marked(self):
        out = apply_anthropic_cache_markers([{"role": "system", "content": "sys"}])
        assert out[0]["content"] == [
            {"type": "text", "text": "sys", "cache_control": _EPHEMERAL}
        ]

    def test_last_three_body_messages_marked(self):
        messages = [
            {"role": "system", "content": "s"},
            {"role": "user", "content": "u1"},
            {"role": "assistant", "content": "a1"},
            {"role": "user", "content": "u2"},
            {"role": "assistant", "content": "a2"},
        ]
        out = apply_anthropic_cache_markers(messages)
        # system + last 3 body messages = 4 breakpoints (the documented cap)
        assert _marked(out[0])  # system
        assert not _marked(out[1])  # u1 — falls outside the 3-message tail
        assert _marked(out[2]) and _marked(out[3]) and _marked(out[4])

    def test_tool_messages_skipped_as_anchor_points(self):
        messages = [
            {"role": "user", "content": "u1"},
            {"role": "assistant", "content": "a1"},
            {"role": "tool", "tool_call_id": "x", "content": "result"},
            {"role": "user", "content": "u2"},
        ]
        out = apply_anthropic_cache_markers(messages)
        # tool message is never marked; the 3 body anchors are u1, a1, u2
        assert not _marked(out[2])
        assert _marked(out[0]) and _marked(out[1]) and _marked(out[3])

    def test_no_system_message_allows_four_body_breakpoints(self):
        messages = [
            {"role": "user", "content": "u1"},
            {"role": "assistant", "content": "a1"},
            {"role": "user", "content": "u2"},
            {"role": "assistant", "content": "a2"},
        ]
        out = apply_anthropic_cache_markers(messages)
        # with no system message all 4 slots go to the body tail
        assert all(_marked(m) for m in out)

    def test_list_content_marks_last_text_part(self):
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
        assert "cache_control" not in out[0]["content"][0]
        assert out[0]["content"][1]["cache_control"] == _EPHEMERAL

    def test_image_only_content_left_unmarked(self):
        messages = [
            {"role": "user", "content": [{"type": "image_url", "image_url": {}}]}
        ]
        out = apply_anthropic_cache_markers(messages)
        # no text part to anchor on — the turn is left alone, not broken
        assert "cache_control" not in out[0]["content"][0]

    def test_empty_string_content_left_alone(self):
        messages = [{"role": "user", "content": ""}]
        out = apply_anthropic_cache_markers(messages)
        assert out[0]["content"] == ""

    def test_none_content_left_alone(self):
        messages = [{"role": "assistant", "content": None}]
        out = apply_anthropic_cache_markers(messages)
        assert out[0]["content"] is None


class TestIsAnthropicEndpoint:
    def test_base_url_with_anthropic_host(self):
        assert is_anthropic_endpoint("https://api.anthropic.com/v1", None) is True

    def test_provider_name_anthropic(self):
        assert is_anthropic_endpoint(None, "anthropic") is True

    def test_provider_name_case_insensitive(self):
        assert is_anthropic_endpoint(None, "Anthropic") is True

    def test_non_anthropic_endpoint(self):
        assert is_anthropic_endpoint("https://openrouter.ai/api", "openrouter") is False

    def test_both_none_is_false(self):
        assert is_anthropic_endpoint(None, None) is False
