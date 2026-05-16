"""Unit tests for ``llm/message.py`` — Message + multimodal ContentParts.

Behavior-first: every assert checks the exact reconstructed content /
serialized dict, not just that something dict-shaped came back. The core
contract under test is the OpenAI-wire round-trip and the multimodal
ContentPart reconstruction.
"""

import pytest

from kohakuterrarium.llm.message import (
    AssistantMessage,
    FilePart,
    ImagePart,
    Message,
    SystemMessage,
    TextPart,
    ToolMessage,
    UserMessage,
    content_part_from_dict,
    content_parts_to_dicts,
    create_message,
    dicts_to_messages,
    make_multimodal_content,
    messages_to_dicts,
    normalize_content_parts,
)

# ---------------------------------------------------------------------------
# ContentPart.to_dict
# ---------------------------------------------------------------------------


class TestContentPartToDict:
    def test_text_part(self):
        assert TextPart(text="hello").to_dict() == {"type": "text", "text": "hello"}

    def test_image_part_minimal(self):
        part = ImagePart(url="https://x/y.png")
        assert part.to_dict() == {
            "type": "image_url",
            "image_url": {"url": "https://x/y.png", "detail": "low"},
        }

    def test_image_part_with_source_meta(self):
        part = ImagePart(
            url="data:image/png;base64,AAAA",
            detail="high",
            source_type="attachment",
            source_name="cat.png",
        )
        assert part.to_dict() == {
            "type": "image_url",
            "image_url": {"url": "data:image/png;base64,AAAA", "detail": "high"},
            "meta": {"source_type": "attachment", "source_name": "cat.png"},
        }

    def test_image_part_meta_omitted_when_no_source(self):
        # only emit meta when there's something to put in it
        assert "meta" not in ImagePart(url="u").to_dict()

    def test_image_get_description(self):
        assert (
            ImagePart(
                url="u", source_type="emoji", source_name="smile"
            ).get_description()
            == "[emoji: smile]"
        )
        assert (
            ImagePart(url="u", source_type="sticker").get_description() == "[sticker]"
        )
        assert ImagePart(url="u").get_description() == "[image]"

    def test_file_part(self):
        part = FilePart(
            path="/a/b.txt",
            name="b.txt",
            content="data",
            mime="text/plain",
            encoding="utf-8",
            is_inline=True,
        )
        assert part.to_dict() == {
            "type": "file",
            "file": {
                "path": "/a/b.txt",
                "name": "b.txt",
                "content": "data",
                "mime": "text/plain",
                "data_base64": None,
                "encoding": "utf-8",
                "is_inline": True,
            },
        }


# ---------------------------------------------------------------------------
# content_part_from_dict — reconstruction
# ---------------------------------------------------------------------------


class TestContentPartFromDict:
    def test_text_round_trip(self):
        src = TextPart(text="round")
        rebuilt = content_part_from_dict(src.to_dict())
        assert rebuilt == src

    def test_image_round_trip_preserves_meta(self):
        src = ImagePart(url="u", detail="high", source_type="emoji", source_name="wave")
        rebuilt = content_part_from_dict(src.to_dict())
        assert rebuilt == src

    def test_image_without_meta_round_trip(self):
        src = ImagePart(url="u", detail="low")
        rebuilt = content_part_from_dict(src.to_dict())
        assert rebuilt == src
        assert rebuilt.source_type is None

    def test_file_round_trip(self):
        src = FilePart(path="/p", name="n", content="c", is_inline=True)
        rebuilt = content_part_from_dict(src.to_dict())
        assert rebuilt == src

    def test_unknown_type_returns_none(self):
        assert content_part_from_dict({"type": "video", "url": "x"}) is None

    def test_missing_type_returns_none(self):
        assert content_part_from_dict({"text": "no type"}) is None

    def test_image_missing_url_defaults_empty(self):
        part = content_part_from_dict({"type": "image_url", "image_url": {}})
        assert part == ImagePart(url="", detail="low")


# ---------------------------------------------------------------------------
# normalize_content_parts
# ---------------------------------------------------------------------------


class TestNormalizeContentParts:
    def test_none_passthrough(self):
        assert normalize_content_parts(None) is None

    def test_string_passthrough(self):
        assert normalize_content_parts("plain") == "plain"

    def test_typed_parts_kept_as_is(self):
        tp = TextPart(text="x")
        result = normalize_content_parts([tp])
        assert result == [tp]

    def test_dicts_converted_to_typed(self):
        result = normalize_content_parts(
            [
                {"type": "text", "text": "hi"},
                {"type": "image_url", "image_url": {"url": "u"}},
            ]
        )
        assert result == [TextPart(text="hi"), ImagePart(url="u")]

    def test_unknown_dicts_dropped(self):
        result = normalize_content_parts(
            [{"type": "text", "text": "keep"}, {"type": "bogus"}]
        )
        assert result == [TextPart(text="keep")]


class TestContentPartsToDicts:
    def test_typed_parts_serialized(self):
        assert content_parts_to_dicts([TextPart(text="a")]) == [
            {"type": "text", "text": "a"}
        ]

    def test_raw_dicts_passed_through_unchanged(self):
        raw = {"type": "text", "text": "already-dict"}
        assert content_parts_to_dicts([raw]) == [raw]


# ---------------------------------------------------------------------------
# Message.to_dict / from_dict
# ---------------------------------------------------------------------------


class TestMessageToDict:
    def test_text_message(self):
        msg = Message(role="user", content="hello")
        assert msg.to_dict() == {"role": "user", "content": "hello"}

    def test_none_content_preserved(self):
        msg = Message(role="assistant", content=None)
        assert msg.to_dict() == {"role": "assistant", "content": None}

    def test_multimodal_content_serialized(self):
        msg = Message(role="user", content=[TextPart(text="see"), ImagePart(url="u")])
        assert msg.to_dict() == {
            "role": "user",
            "content": [
                {"type": "text", "text": "see"},
                {"type": "image_url", "image_url": {"url": "u", "detail": "low"}},
            ],
        }

    def test_optional_fields_only_when_set(self):
        msg = Message(
            role="tool",
            content="result",
            name="bash",
            tool_call_id="call_1",
            tool_calls=[{"id": "call_1"}],
        )
        d = msg.to_dict()
        assert d["name"] == "bash"
        assert d["tool_call_id"] == "call_1"
        assert d["tool_calls"] == [{"id": "call_1"}]

    def test_empty_optionals_omitted(self):
        msg = Message(role="user", content="x", name=None, tool_call_id=None)
        assert msg.to_dict() == {"role": "user", "content": "x"}

    def test_extra_fields_echoed(self):
        msg = Message(
            role="assistant",
            content="answer",
            extra_fields={"reasoning_content": "because"},
        )
        assert msg.to_dict() == {
            "role": "assistant",
            "content": "answer",
            "reasoning_content": "because",
        }

    def test_extra_fields_cannot_clobber_standard_keys(self):
        # docstring: extra_fields must never overwrite role/content/etc.
        msg = Message(
            role="assistant",
            content="real",
            extra_fields={"content": "FAKE", "role": "system"},
        )
        d = msg.to_dict()
        assert d["content"] == "real"
        assert d["role"] == "assistant"


class TestMessageFromDict:
    def test_basic_text_message(self):
        msg = Message.from_dict({"role": "user", "content": "hi"})
        assert msg.role == "user"
        assert msg.content == "hi"
        assert msg.extra_fields == {}

    def test_multimodal_content_normalized(self):
        msg = Message.from_dict(
            {
                "role": "user",
                "content": [{"type": "text", "text": "look"}],
            }
        )
        assert msg.content == [TextPart(text="look")]

    def test_non_standard_keys_captured_in_extra_fields(self):
        msg = Message.from_dict(
            {
                "role": "assistant",
                "content": "a",
                "reasoning_content": "r",
                "reasoning_details": {"steps": 3},
            }
        )
        assert msg.extra_fields == {
            "reasoning_content": "r",
            "reasoning_details": {"steps": 3},
        }

    def test_metadata_key_dropped_from_extra_fields(self):
        # docstring: internal-only 'metadata' key must not land on the wire
        msg = Message.from_dict(
            {"role": "user", "content": "x", "metadata": {"internal": True}}
        )
        assert "metadata" not in msg.extra_fields

    def test_standard_fields_extracted(self):
        msg = Message.from_dict(
            {
                "role": "tool",
                "content": "out",
                "name": "tool1",
                "tool_call_id": "c1",
                "tool_calls": [{"id": "c1"}],
            }
        )
        assert msg.name == "tool1"
        assert msg.tool_call_id == "c1"
        assert msg.tool_calls == [{"id": "c1"}]

    def test_round_trip_with_extras(self):
        original = {
            "role": "assistant",
            "content": "ans",
            "reasoning_content": "thought",
        }
        rebuilt = Message.from_dict(original).to_dict()
        assert rebuilt == original


# ---------------------------------------------------------------------------
# Message content helpers
# ---------------------------------------------------------------------------


class TestMessageContentHelpers:
    def test_get_text_content_from_string(self):
        assert Message(role="user", content="plain").get_text_content() == "plain"

    def test_get_text_content_concatenates_text_parts_only(self):
        msg = Message(
            role="user",
            content=[
                TextPart(text="line1"),
                ImagePart(url="u"),
                TextPart(text="line2"),
            ],
        )
        assert msg.get_text_content() == "line1\nline2"

    def test_has_images_string_false(self):
        assert Message(role="user", content="text").has_images() is False

    def test_has_images_true_when_image_present(self):
        msg = Message(role="user", content=[TextPart(text="t"), ImagePart(url="u")])
        assert msg.has_images() is True

    def test_has_images_false_when_only_text_parts(self):
        msg = Message(role="user", content=[TextPart(text="t")])
        assert msg.has_images() is False

    def test_get_images_returns_only_image_parts(self):
        img1 = ImagePart(url="a")
        img2 = ImagePart(url="b")
        msg = Message(role="user", content=[TextPart(text="t"), img1, img2])
        assert msg.get_images() == [img1, img2]

    def test_get_images_empty_for_string_content(self):
        assert Message(role="user", content="x").get_images() == []

    def test_is_multimodal(self):
        assert Message(role="user", content="x").is_multimodal() is False
        assert (
            Message(role="user", content=[TextPart(text="x")]).is_multimodal() is True
        )


# ---------------------------------------------------------------------------
# Message subclasses
# ---------------------------------------------------------------------------


class TestMessageSubclasses:
    def test_system_message_role(self):
        msg = SystemMessage("you are an agent")
        assert msg.role == "system"
        assert msg.content == "you are an agent"

    def test_user_message_with_name(self):
        msg = UserMessage("hi", name="alice")
        assert msg.role == "user"
        assert msg.name == "alice"

    def test_assistant_message_role(self):
        msg = AssistantMessage("done")
        assert msg.role == "assistant"

    def test_tool_message_requires_tool_call_id(self):
        msg = ToolMessage("output", tool_call_id="c1", name="bash")
        assert msg.role == "tool"
        assert msg.tool_call_id == "c1"
        assert msg.name == "bash"


# ---------------------------------------------------------------------------
# create_message factory
# ---------------------------------------------------------------------------


class TestCreateMessage:
    def test_system_returns_systemmessage(self):
        msg = create_message("system", "prompt")
        assert isinstance(msg, SystemMessage)
        assert msg.content == "prompt"

    def test_system_flattens_list_content_to_text(self):
        # docstring: system messages are always text-only
        msg = create_message("system", [TextPart(text="a"), TextPart(text="b")])
        assert isinstance(msg, SystemMessage)
        assert msg.content == "a\nb"

    def test_user_returns_usermessage(self):
        assert isinstance(create_message("user", "hi"), UserMessage)

    def test_assistant_text_list_flattened(self):
        # all-text list -> flattened string for cheaper handling
        msg = create_message("assistant", [TextPart(text="x"), TextPart(text="y")])
        assert isinstance(msg, AssistantMessage)
        assert msg.content == "x\ny"

    def test_assistant_preserves_list_with_image(self):
        # docstring: structured content (ImagePart) must survive
        img = ImagePart(url="u")
        msg = create_message("assistant", [TextPart(text="x"), img])
        assert isinstance(msg, AssistantMessage)
        assert msg.content == [TextPart(text="x"), img]

    def test_tool_requires_tool_call_id(self):
        with pytest.raises(ValueError, match="ToolMessage requires tool_call_id"):
            create_message("tool", "out")

    def test_tool_with_id_creates_toolmessage(self):
        msg = create_message("tool", "out", tool_call_id="c9")
        assert isinstance(msg, ToolMessage)
        assert msg.tool_call_id == "c9"

    def test_unknown_role_returns_plain_message(self):
        msg = create_message("developer", "x")
        assert type(msg) is Message
        assert msg.role == "developer"


# ---------------------------------------------------------------------------
# Conversion helpers + make_multimodal_content
# ---------------------------------------------------------------------------


class TestConversionHelpers:
    def test_messages_to_dicts_mixed(self):
        msgs = [Message(role="user", content="a"), {"role": "system", "content": "b"}]
        assert messages_to_dicts(msgs) == [
            {"role": "user", "content": "a"},
            {"role": "system", "content": "b"},
        ]

    def test_dicts_to_messages(self):
        result = dicts_to_messages(
            [{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"}]
        )
        assert [(m.role, m.content) for m in result] == [
            ("user", "a"),
            ("assistant", "b"),
        ]

    def test_make_multimodal_content_no_images_returns_string(self):
        assert make_multimodal_content("just text") == "just text"

    def test_make_multimodal_content_appends_images_by_default(self):
        img = ImagePart(url="u")
        result = make_multimodal_content("caption", [img])
        assert result == [TextPart(text="caption"), img]

    def test_make_multimodal_content_prepend_images(self):
        img = ImagePart(url="u")
        result = make_multimodal_content("caption", [img], prepend_images=True)
        assert result == [img, TextPart(text="caption")]

    def test_make_multimodal_content_empty_image_list_returns_string(self):
        assert make_multimodal_content("text", []) == "text"
