"""Round-trip coverage for ImagePart serialization in Conversation.

Two concerns:

1. **Forward round-trip** — an ``ImagePart`` serialized via
   ``Conversation._serialize_content`` and deserialized back should
   compare equal on the fields we care about.
2. **Backward compat** — the legacy flat shape
   (``{type, url, detail, source_type, source_name}``) written by
   older versions of this codebase still loads into an ``ImagePart``
   correctly.
"""

from kohakuterrarium.core.conversation import Conversation
from kohakuterrarium.llm.message import ImagePart, TextPart


def _make_convo() -> Conversation:
    """Return an instance with private serialize helpers accessible."""
    return Conversation()


def test_image_part_round_trip_nested():
    convo = _make_convo()
    original = [
        TextPart(text="hello"),
        ImagePart(
            url="https://example.com/cat.png",
            detail="high",
            source_type="attachment",
            source_name="cat.png",
        ),
    ]
    serialized = convo._serialize_content(original)
    assert isinstance(serialized, list)

    # Nested shape is what we emit now (matches ImagePart.to_dict and
    # the frontend's ChatMessage.vue reader).
    assert serialized[1]["type"] == "image_url"
    assert serialized[1]["image_url"] == {
        "url": "https://example.com/cat.png",
        "detail": "high",
    }
    assert serialized[1]["meta"] == {
        "source_type": "attachment",
        "source_name": "cat.png",
    }

    restored = convo._deserialize_content(serialized)
    assert isinstance(restored, list)
    assert isinstance(restored[0], TextPart)
    assert restored[0].text == "hello"
    assert isinstance(restored[1], ImagePart)
    assert restored[1].url == original[1].url
    assert restored[1].detail == original[1].detail
    assert restored[1].source_type == original[1].source_type
    assert restored[1].source_name == original[1].source_name


def test_image_part_legacy_flat_shape_still_loads():
    """Sessions written before the normalization used a flat shape.

    Reader must still accept them — otherwise `kt resume` breaks on
    any saved session that carried an image part.
    """
    convo = _make_convo()
    legacy = [
        {"type": "text", "text": "see photo"},
        {
            "type": "image_url",
            "url": "data:image/png;base64,AAAA",
            "detail": "low",
            "source_type": "attachment",
            "source_name": "old.png",
        },
    ]
    restored = convo._deserialize_content(legacy)
    assert len(restored) == 2
    img = restored[1]
    assert isinstance(img, ImagePart)
    assert img.url == "data:image/png;base64,AAAA"
    assert img.detail == "low"
    assert img.source_type == "attachment"
    assert img.source_name == "old.png"


def test_image_part_without_meta_has_no_meta_key():
    """A bare ImagePart (no source info) should serialize without a
    meta key — matches `ImagePart.to_dict` and keeps payloads small."""
    convo = _make_convo()
    bare = [ImagePart(url="data:image/png;base64,xxx", detail="low")]
    serialized = convo._serialize_content(bare)
    assert serialized == [
        {
            "type": "image_url",
            "image_url": {"url": "data:image/png;base64,xxx", "detail": "low"},
        }
    ]
    restored = convo._deserialize_content(serialized)
    assert isinstance(restored[0], ImagePart)
    assert restored[0].source_type is None
    assert restored[0].source_name is None
