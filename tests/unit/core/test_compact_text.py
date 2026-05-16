"""Unit tests for :mod:`kohakuterrarium.core.compact_text`.

This helper was the site of a real production bug — user messages
built from web POSTs (``list[dict]`` content) were silently dropped
from the compact-summary input, producing summaries that claimed
"no user instructions" when the conversation was full of them.
Every supported content shape MUST extract its text.
"""

from types import SimpleNamespace

from kohakuterrarium.core.compact_text import extract_message_text


class TestStringContent:
    def test_plain_string_returned_verbatim(self):
        msg = SimpleNamespace(content="hello")
        assert extract_message_text(msg) == "hello"

    def test_empty_string(self):
        msg = SimpleNamespace(content="")
        assert extract_message_text(msg) == ""


class TestListOfContentParts:
    def test_framework_textpart(self):
        # Framework TextPart-style: object with ``.text``.
        part = SimpleNamespace(text="alpha")
        msg = SimpleNamespace(content=[part])
        assert extract_message_text(msg) == "alpha"

    def test_multiple_textparts_joined_with_spaces(self):
        msg = SimpleNamespace(
            content=[SimpleNamespace(text="hello"), SimpleNamespace(text="world")]
        )
        assert extract_message_text(msg) == "hello world"

    def test_part_with_empty_text_excluded(self):
        # A part with empty text → no chunk added (joined with space
        # otherwise leaves dangling spaces).
        msg = SimpleNamespace(
            content=[
                SimpleNamespace(text="hello"),
                SimpleNamespace(text=""),
                SimpleNamespace(text="world"),
            ]
        )
        assert extract_message_text(msg) == "hello world"

    def test_part_with_none_text_excluded(self):
        msg = SimpleNamespace(
            content=[
                SimpleNamespace(text="hello"),
                SimpleNamespace(text=None),
            ]
        )
        # ``None`` is coerced to "" by the `or ""` branch → falsy → skipped.
        assert extract_message_text(msg) == "hello"

    def test_part_without_text_attr_excluded(self):
        # ImagePart / AudioPart have no ``.text``; the function skips
        # them — they have nothing to feed a text summariser.
        class _ImagePart:
            url = "..."

        msg = SimpleNamespace(content=[SimpleNamespace(text="hi"), _ImagePart()])
        assert extract_message_text(msg) == "hi"


class TestListOfDictParts:
    def test_dict_with_text_key(self):
        # The regression case: raw web POST shape.
        msg = SimpleNamespace(content=[{"type": "text", "text": "user said this"}])
        assert extract_message_text(msg) == "user said this"

    def test_dict_with_content_key_fallback(self):
        # Some providers emit ``content`` instead of ``text``.
        msg = SimpleNamespace(content=[{"type": "text", "content": "alt key"}])
        assert extract_message_text(msg) == "alt key"

    def test_dict_text_takes_precedence_over_content(self):
        msg = SimpleNamespace(content=[{"text": "primary", "content": "secondary"}])
        assert extract_message_text(msg) == "primary"

    def test_dict_non_string_text_ignored(self):
        # Defensive: ``text: null`` shouldn't crash.
        msg = SimpleNamespace(content=[{"type": "text", "text": None}])
        assert extract_message_text(msg) == ""

    def test_dict_without_text_or_content(self):
        # An image-part dict has no text/content → skipped.
        msg = SimpleNamespace(
            content=[{"type": "image_url", "image_url": {"url": "x"}}]
        )
        assert extract_message_text(msg) == ""

    def test_mixed_textparts_and_dicts_in_one_list(self):
        msg = SimpleNamespace(
            content=[
                SimpleNamespace(text="from-part"),
                {"text": "from-dict"},
            ]
        )
        assert extract_message_text(msg) == "from-part from-dict"


class TestEdgeCases:
    def test_message_without_content_attribute_returns_empty(self):
        msg = SimpleNamespace()
        assert extract_message_text(msg) == ""

    def test_content_is_none(self):
        msg = SimpleNamespace(content=None)
        # None matches neither str nor list → empty.
        assert extract_message_text(msg) == ""

    def test_content_is_unknown_type(self):
        # Defensive — int, bool, etc. all fall through to "".
        msg = SimpleNamespace(content=42)
        assert extract_message_text(msg) == ""

    def test_empty_list(self):
        msg = SimpleNamespace(content=[])
        assert extract_message_text(msg) == ""
