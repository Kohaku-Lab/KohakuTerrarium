"""Unit tests for :mod:`kohakuterrarium.core.conversation`."""

import json


from kohakuterrarium.core.conversation import (
    Conversation,
    ConversationConfig,
    ConversationMetadata,
    _get_content_text_length,
    _is_empty_content,
)
from kohakuterrarium.llm.message import (
    ImagePart,
    Message,
    TextPart,
)

# ── private helpers ──────────────────────────────────────────────


class TestGetContentTextLength:
    def test_none(self):
        assert _get_content_text_length(None) == 0

    def test_str(self):
        assert _get_content_text_length("hello") == 5

    def test_list_of_text_parts(self):
        parts = [TextPart(text="ab"), TextPart(text="cde")]
        assert _get_content_text_length(parts) == 5

    def test_list_includes_image_part_skipped(self):
        parts = [TextPart(text="hi"), ImagePart(url="x")]
        # Only TextParts counted.
        assert _get_content_text_length(parts) == 2


class TestIsEmptyContent:
    def test_none(self):
        assert _is_empty_content(None) is True

    def test_empty_str(self):
        assert _is_empty_content("") is True

    def test_whitespace_str(self):
        assert _is_empty_content("   \n\t") is True

    def test_nonempty_str(self):
        assert _is_empty_content("x") is False

    def test_empty_list(self):
        assert _is_empty_content([]) is True

    def test_list_with_blank_text_part(self):
        assert _is_empty_content([TextPart(text="")]) is True
        assert _is_empty_content([TextPart(text="   ")]) is True

    def test_list_with_real_text_part(self):
        assert _is_empty_content([TextPart(text="hi")]) is False

    def test_list_with_image_part(self):
        # ImagePart is meaningful — keep the message.
        assert _is_empty_content([ImagePart(url="x")]) is False

    def test_list_with_dict_text(self):
        assert _is_empty_content([{"type": "text", "text": "hi"}]) is False
        assert _is_empty_content([{"type": "text", "text": ""}]) is True

    def test_list_with_dict_non_text(self):
        assert _is_empty_content([{"type": "image_url"}]) is False

    def test_non_str_non_list(self):
        assert _is_empty_content(42) is False


# ── append / metadata ─────────────────────────────────────────────


class TestAppendBasic:
    def test_append_str(self):
        c = Conversation()
        msg = c.append("user", "hello")
        assert isinstance(msg, Message)
        assert msg.role == "user"
        assert msg.content == "hello"
        assert len(c) == 1
        assert bool(c) is True

    def test_append_multimodal(self):
        c = Conversation()
        parts = [TextPart(text="hi"), ImagePart(url="http://x/a.png")]
        c.append("user", parts)
        assert c.get_image_count() == 1
        assert c.get_messages()[0].is_multimodal()

    def test_metadata_updated(self):
        c = Conversation()
        c.append("user", "hello")
        c.append("assistant", "hi")
        assert c._metadata.message_count == 2
        assert c._metadata.total_chars == 7

    def test_empty_initial(self):
        c = Conversation()
        assert len(c) == 0
        assert bool(c) is False
        assert c.get_context_length() == 0
        assert c.get_image_count() == 0


class TestAppendMessage:
    def test_append_existing_message(self):
        c = Conversation()
        msg = Message(role="user", content="x")
        c.append_message(msg)
        assert c.get_last_message() is msg


# ── truncation ────────────────────────────────────────────────────


class TestTruncation:
    def test_zero_disables(self):
        c = Conversation(ConversationConfig(max_messages=0))
        for _ in range(100):
            c.append("user", "x")
        assert len(c) == 100

    def test_keeps_system_messages(self):
        c = Conversation(ConversationConfig(max_messages=3, keep_system=True))
        c.append("system", "sys")
        for i in range(10):
            c.append("user", f"u{i}")
        # 1 system + 2 most-recent user (limit=3).
        msgs = c.get_messages()
        assert msgs[0].role == "system"
        assert len(msgs) == 3
        assert msgs[-1].content == "u9"

    def test_no_keep_system_drops_old(self):
        c = Conversation(ConversationConfig(max_messages=2, keep_system=False))
        c.append("system", "sys")
        c.append("user", "u0")
        c.append("user", "u1")
        # Last 2 only.
        msgs = c.get_messages()
        assert [m.content for m in msgs] == ["u0", "u1"]


# ── lookups ───────────────────────────────────────────────────────


class TestLookups:
    def test_get_system_message(self):
        c = Conversation()
        c.append("user", "first")
        assert c.get_system_message() is None
        c.append("system", "sys")
        assert c.get_system_message().content == "sys"

    def test_get_last_message_empty(self):
        assert Conversation().get_last_message() is None

    def test_get_last_assistant(self):
        c = Conversation()
        c.append("user", "u")
        c.append("assistant", "a1")
        c.append("user", "u2")
        c.append("assistant", "a2")
        assert c.get_last_assistant_message().content == "a2"

    def test_get_last_assistant_when_none(self):
        c = Conversation()
        c.append("user", "u")
        assert c.get_last_assistant_message() is None

    def test_find_last_user_index(self):
        c = Conversation()
        assert c.find_last_user_index() == -1
        c.append("user", "u0")
        c.append("assistant", "a")
        c.append("user", "u1")
        c.append("assistant", "a2")
        assert c.find_last_user_index() == 2


# ── truncate_from / clear ─────────────────────────────────────────


class TestTruncateFrom:
    def test_removes_from_index(self):
        c = Conversation()
        for i in range(5):
            c.append("user", f"u{i}")
        removed = c.truncate_from(2)
        assert [m.content for m in removed] == ["u2", "u3", "u4"]
        assert [m.content for m in c.get_messages()] == ["u0", "u1"]

    def test_invalid_index_noop(self):
        c = Conversation()
        c.append("user", "x")
        assert c.truncate_from(99) == []
        assert c.truncate_from(-1) == []
        assert len(c) == 1

    def test_truncate_from_zero_keeps_leading_system(self):
        # Regression guard for B-fat2-api-2: truncate_from(0) must NOT
        # drop the leading system message — it rewinds to a fresh
        # conversation that still carries the system prompt. Before the
        # fix, truncate_from(0) wiped everything and a subsequent
        # get_system_prompt() returned "".
        c = Conversation()
        c.append("system", "sys")
        c.append("user", "u0")
        c.append("assistant", "a0")
        removed = c.truncate_from(0)
        assert [m.content for m in removed] == ["u0", "a0"]
        assert [m.role for m in c.get_messages()] == ["system"]
        assert c.get_system_message().content == "sys"

    def test_truncate_from_clamps_into_multiple_system_messages(self):
        # Two leading system messages: any index that would cut into
        # them is clamped to just past them.
        c = Conversation()
        c.append("system", "s0")
        c.append("system", "s1")
        c.append("user", "u0")
        removed = c.truncate_from(1)
        assert [m.content for m in removed] == ["u0"]
        assert [m.role for m in c.get_messages()] == ["system", "system"]


class TestClear:
    def test_keep_system(self):
        c = Conversation()
        c.append("system", "sys")
        c.append("user", "u")
        c.clear(keep_system=True)
        assert [m.role for m in c.get_messages()] == ["system"]

    def test_clear_all(self):
        c = Conversation()
        c.append("system", "sys")
        c.append("user", "u")
        c.clear(keep_system=False)
        assert len(c) == 0


# ── sanitize_orphan_tool_pairs ────────────────────────────────────


def _asst_with_tools(*ids, content=None):
    return {
        "role": "assistant",
        "content": content,
        "tool_calls": [{"id": i, "type": "function"} for i in ids],
    }


def _tool_result(tc_id: str, output: str = "ok"):
    return {"role": "tool", "tool_call_id": tc_id, "content": output}


class TestSanitizeOrphans:
    def test_empty_passthrough(self):
        assert Conversation.sanitize_orphan_tool_pairs([]) == []

    def test_complete_pairing_preserved(self):
        msgs = [
            {"role": "user", "content": "hi"},
            _asst_with_tools("a"),
            _tool_result("a"),
            {"role": "assistant", "content": "done"},
        ]
        out = Conversation.sanitize_orphan_tool_pairs(msgs)
        assert out == msgs

    def test_orphan_tool_call_dropped(self):
        msgs = [
            _asst_with_tools("a", "b", content="thinking"),
            _tool_result("a"),
            # No tool result for "b".
            {"role": "user", "content": "next"},
        ]
        out = Conversation.sanitize_orphan_tool_pairs(msgs)
        # Assistant kept (it has content) but tool_calls reduced to id "a".
        kept = out[0]
        assert [tc["id"] for tc in kept["tool_calls"]] == ["a"]

    def test_assistant_dropped_when_all_orphaned_and_no_content(self):
        msgs = [
            _asst_with_tools("a"),
            {"role": "user", "content": "next"},  # no tool result for "a"
        ]
        out = Conversation.sanitize_orphan_tool_pairs(msgs)
        # Assistant removed entirely (no kept tool_calls, no content).
        assert [m["role"] for m in out] == ["user"]

    def test_assistant_kept_when_all_orphaned_but_has_text(self):
        msgs = [
            _asst_with_tools("a", content="I tried"),
            {"role": "user", "content": "next"},
        ]
        out = Conversation.sanitize_orphan_tool_pairs(msgs)
        # tool_calls key removed because empty.
        assistant = next(m for m in out if m["role"] == "assistant")
        assert "tool_calls" not in assistant
        assert assistant["content"] == "I tried"

    def test_orphan_tool_result_dropped(self):
        msgs = [
            {"role": "user", "content": "hi"},
            _tool_result("ghost"),  # no preceding assistant for this id
            {"role": "assistant", "content": "ok"},
        ]
        out = Conversation.sanitize_orphan_tool_pairs(msgs)
        assert all(m.get("role") != "tool" for m in out)

    def test_idempotent(self):
        msgs = [
            _asst_with_tools("a", "b", content="x"),
            _tool_result("a"),
            {"role": "user", "content": "next"},
        ]
        once = Conversation.sanitize_orphan_tool_pairs(msgs)
        twice = Conversation.sanitize_orphan_tool_pairs(once)
        assert once == twice


class TestSanitizeIntegration:
    def test_to_messages_applies_sanitizer(self):
        c = Conversation()
        c.append("assistant", "", tool_calls=[{"id": "x", "type": "function"}])
        c.append("user", "hi")  # no tool result for "x"
        msgs = c.to_messages()
        # Assistant message dropped — only user remains.
        roles = [m["role"] for m in msgs]
        assert "assistant" not in roles

    def test_sanitizer_disable(self):
        c = Conversation(ConversationConfig(sanitize_orphan_tool_calls=False))
        c.append("assistant", "", tool_calls=[{"id": "x", "type": "function"}])
        c.append("user", "hi")
        msgs = c.to_messages()
        # Assistant survives intact.
        assistant = next(m for m in msgs if m["role"] == "assistant")
        assert assistant.get("tool_calls") == [{"id": "x", "type": "function"}]


# ── JSON round-trip ───────────────────────────────────────────────


class TestJsonRoundTrip:
    def test_text_only(self):
        c = Conversation()
        c.append("system", "sys")
        c.append("user", "u")
        c.append("assistant", "a")
        recovered = Conversation.from_json(c.to_json())
        assert [m.content for m in recovered.get_messages()] == ["sys", "u", "a"]

    def test_multimodal(self):
        c = Conversation()
        c.append(
            "user",
            [
                TextPart(text="describe"),
                ImagePart(
                    url="https://x/a.png",
                    detail="high",
                    source_type="attachment",
                    source_name="a.png",
                ),
            ],
        )
        recovered = Conversation.from_json(c.to_json())
        parts = recovered.get_messages()[0].content
        assert isinstance(parts[0], TextPart)
        assert isinstance(parts[1], ImagePart)
        assert parts[1].detail == "high"
        assert parts[1].source_name == "a.png"

    def test_legacy_flat_image_shape(self):
        # Older sessions stored images as ``{type:"image_url", url, detail,
        # source_type, source_name}`` at the top level. Load must accept
        # that shape too.
        legacy = json.dumps(
            {
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image_url",
                                "url": "https://x/y.png",
                                "detail": "low",
                                "source_type": "emoji",
                                "source_name": "smile",
                            }
                        ],
                        "name": None,
                        "tool_call_id": None,
                        "tool_calls": None,
                        "extra_fields": None,
                        "metadata": {},
                    }
                ],
                "metadata": {
                    "created_at": "2024-01-01T00:00:00",
                    "updated_at": "2024-01-02T00:00:00",
                    "message_count": 1,
                    "total_chars": 0,
                },
            }
        )
        recovered = Conversation.from_json(legacy)
        msg = recovered.get_messages()[0]
        img = msg.content[0]
        assert img.url == "https://x/y.png"
        assert img.source_type == "emoji"

    def test_extra_fields_survive(self):
        c = Conversation()
        msg = c.append("assistant", "thinking...")
        msg.extra_fields = {"reasoning_content": "secret reasoning"}
        recovered = Conversation.from_json(c.to_json())
        assert (
            recovered.get_messages()[0].extra_fields.get("reasoning_content")
            == "secret reasoning"
        )


# ── repr ──────────────────────────────────────────────────────────


class TestRepr:
    def test_format(self):
        c = Conversation()
        c.append("user", "hello")
        r = repr(c)
        assert "messages=1" in r
        assert "context_chars=5" in r


# ── ConversationMetadata defaults ────────────────────────────────


class TestMetadataDefaults:
    def test_each_instance_independent(self):
        a = ConversationMetadata()
        b = ConversationMetadata()
        a.message_count = 10
        assert b.message_count == 0
