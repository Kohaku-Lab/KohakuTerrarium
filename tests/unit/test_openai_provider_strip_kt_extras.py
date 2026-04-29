"""Tests for ``_strip_kt_extras`` — the content-part sanitiser that
keeps KT-internal fields (e.g. ``ImagePart.meta``) from leaking into
LLM API requests.

Without this stripping step, strict OpenAI-compatible providers (vLLM,
SGLang, MiMo, …) drop or ignore image_url parts whose top-level shape
includes unknown keys, producing the failure mode "the model says it
sees no image."
"""

from __future__ import annotations

from kohakuterrarium.llm.message import ImagePart, TextPart
from kohakuterrarium.llm.openai_sanitize import strip_kt_extras as _strip_kt_extras

# ---------------------------------------------------------------------------
# Direct tests on the helper
# ---------------------------------------------------------------------------


def test_string_content_messages_pass_through():
    msgs = [
        {"role": "system", "content": "You are helpful."},
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
    ]
    out = _strip_kt_extras(msgs)
    assert out == msgs
    # Identity preserved when nothing needs cleaning.
    assert all(out[i] is msgs[i] for i in range(len(msgs)))


def test_text_part_only_passes_through():
    msgs = [
        {"role": "user", "content": [{"type": "text", "text": "hi"}]},
    ]
    out = _strip_kt_extras(msgs)
    assert out[0]["content"] == [{"type": "text", "text": "hi"}]


def test_clean_image_url_passes_through():
    """A schema-perfect image_url part is left identical."""
    msgs = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "what's this?"},
                {
                    "type": "image_url",
                    "image_url": {
                        "url": "data:image/png;base64,abc",
                        "detail": "low",
                    },
                },
            ],
        },
    ]
    out = _strip_kt_extras(msgs)
    assert out[0]["content"] == msgs[0]["content"]


def test_meta_field_stripped_from_image_url():
    """The exact bug — ImagePart.meta on the wire causes strict
    providers to drop the part. Strip it."""
    msgs = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "what's this?"},
                {
                    "type": "image_url",
                    "image_url": {
                        "url": "data:image/png;base64,abc",
                        "detail": "low",
                    },
                    "meta": {
                        "source_type": "attachment",
                        "source_name": "screenshot.png",
                    },
                },
            ],
        },
    ]
    out = _strip_kt_extras(msgs)
    assert out[0]["content"] == [
        {"type": "text", "text": "what's this?"},
        {
            "type": "image_url",
            "image_url": {"url": "data:image/png;base64,abc", "detail": "low"},
        },
    ]


def test_unknown_keys_inside_image_url_object_stripped():
    """Anything inside ``image_url`` other than ``url`` / ``detail`` is
    not part of the schema — drop it."""
    msgs = [
        {
            "role": "user",
            "content": [
                {
                    "type": "image_url",
                    "image_url": {
                        "url": "https://example.com/x.png",
                        "detail": "auto",
                        "revised_prompt": "...",  # KT-emitted by codex image-gen
                        "kt_internal": "yes",
                    },
                },
            ],
        },
    ]
    out = _strip_kt_extras(msgs)
    assert out[0]["content"] == [
        {
            "type": "image_url",
            "image_url": {
                "url": "https://example.com/x.png",
                "detail": "auto",
            },
        },
    ]


def test_unknown_part_type_passes_through():
    """The sanitiser only polices known schemas. A custom 'file' part
    (resolved earlier in the controller) or any other unknown type is
    not touched."""
    msgs = [
        {
            "role": "user",
            "content": [
                {"type": "file", "file": {"name": "x.txt", "content": "..."}},
            ],
        },
    ]
    out = _strip_kt_extras(msgs)
    assert out[0]["content"] == msgs[0]["content"]


def test_other_message_fields_preserved():
    """Stripping should never drop name, tool_calls, tool_call_id, or
    arbitrary extra_fields keys at the message level — they're not in
    scope for this sanitiser."""
    msgs = [
        {
            "role": "tool",
            "tool_call_id": "tc-1",
            "name": "bash",
            "content": [
                {
                    "type": "image_url",
                    "image_url": {"url": "u"},
                    "meta": {"x": 1},
                },
            ],
        },
    ]
    out = _strip_kt_extras(msgs)
    assert out[0]["role"] == "tool"
    assert out[0]["tool_call_id"] == "tc-1"
    assert out[0]["name"] == "bash"
    assert out[0]["content"] == [
        {"type": "image_url", "image_url": {"url": "u"}},
    ]


def test_assistant_message_with_tool_calls_preserved():
    msgs = [
        {
            "role": "assistant",
            "content": "let me check",
            "tool_calls": [
                {
                    "id": "c1",
                    "type": "function",
                    "function": {"name": "bash", "arguments": "{}"},
                },
            ],
        },
    ]
    out = _strip_kt_extras(msgs)
    assert out[0] == msgs[0]
    assert out[0] is msgs[0]  # untouched, identity preserved


def test_text_part_with_extra_field_stripped():
    """A ``text`` part with an extra key (hypothetical KT addition)
    also gets cleaned to the canonical {type, text} shape."""
    msgs = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "hello", "kt_render_hint": "code"},
            ],
        },
    ]
    out = _strip_kt_extras(msgs)
    assert out[0]["content"] == [{"type": "text", "text": "hello"}]


def test_multiple_messages_mixed_clean_and_dirty():
    msgs = [
        {"role": "system", "content": "you are helpful"},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "img:"},
                {
                    "type": "image_url",
                    "image_url": {"url": "u1", "detail": "low"},
                    "meta": {"source_name": "a.png"},
                },
            ],
        },
        {"role": "assistant", "content": "ok"},
        {
            "role": "user",
            "content": [
                {
                    "type": "image_url",
                    "image_url": {"url": "u2"},
                },
            ],
        },
    ]
    out = _strip_kt_extras(msgs)
    # Index 0 + 2 + 3 are clean, untouched (identity-preserved).
    assert out[0] is msgs[0]
    assert out[2] is msgs[2]
    assert out[3] is msgs[3]
    # Index 1 is rewritten without ``meta``.
    assert out[1]["content"][1] == {
        "type": "image_url",
        "image_url": {"url": "u1", "detail": "low"},
    }


# ---------------------------------------------------------------------------
# End-to-end through ImagePart.to_dict (the actual offender)
# ---------------------------------------------------------------------------


def test_imagepart_to_dict_roundtrip_through_strip():
    """The bug as the user encountered it: a frontend-uploaded image
    becomes an ImagePart, ImagePart.to_dict() emits ``meta``, the
    provider boundary must strip it before reaching the wire."""
    part = ImagePart(
        url="data:image/png;base64,iVBORw0KG...",
        detail="low",
        source_type="attachment",
        source_name="screenshot.png",
    )
    raw = part.to_dict()
    # Sanity: meta is present pre-strip (the offending field).
    assert "meta" in raw
    assert raw["meta"]["source_name"] == "screenshot.png"

    msgs = [{"role": "user", "content": [TextPart(text="?").to_dict(), raw]}]
    out = _strip_kt_extras(msgs)

    assert out[0]["content"] == [
        {"type": "text", "text": "?"},
        {
            "type": "image_url",
            "image_url": {
                "url": "data:image/png;base64,iVBORw0KG...",
                "detail": "low",
            },
        },
    ]


# ---------------------------------------------------------------------------
# Empty / degenerate inputs
# ---------------------------------------------------------------------------


def test_empty_messages():
    assert _strip_kt_extras([]) == []


def test_empty_content_list():
    msgs = [{"role": "user", "content": []}]
    out = _strip_kt_extras(msgs)
    assert out == msgs


def test_content_with_non_dict_items_passes_through():
    """Defensive: if some caller sticks a raw string or object into a
    content list, we don't crash — we just leave it alone."""
    msgs = [{"role": "user", "content": ["raw text", None, 42]}]
    out = _strip_kt_extras(msgs)
    assert out[0]["content"] == ["raw text", None, 42]
