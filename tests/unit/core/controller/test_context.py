"""Unit tests for :mod:`kohakuterrarium.core.controller.context`."""

from kohakuterrarium.core.controller.context import format_events_for_context
from kohakuterrarium.core.events import (
    TriggerEvent,
    create_tool_complete_event,
    create_user_input_event,
)
from kohakuterrarium.llm.message import FilePart, ImagePart, TextPart


def test_user_event_keeps_original_part_order():
    image = ImagePart(url="https://x/a.png", detail="high")
    mid = TextPart(text="caption")
    attached = FilePart(
        name="notes.bin",
        mime="application/octet-stream",
        data_base64="AQID",
        encoding="base64",
        is_inline=True,
    )
    trailing = TextPart(text="after")
    out = format_events_for_context(
        [create_user_input_event([image, mid, attached, trailing])]
    )
    assert [type(p) for p in out] == [ImagePart, TextPart, FilePart, TextPart]
    assert out[0] is image
    assert out[1].text == "caption"
    assert out[2].name == "notes.bin"
    assert out[2].data_base64 == "AQID"
    assert out[3].text == "after"


def test_plain_text_batch_keeps_double_newline_separator():
    out = format_events_for_context(
        [create_user_input_event("alpha"), create_user_input_event("beta")]
    )
    assert out == "alpha\n\nbeta"


def test_text_only_user_parts_keep_boundaries_and_empty_parts():
    parts = [TextPart(text="hello"), TextPart(text=""), TextPart(text="there")]
    out = format_events_for_context([create_user_input_event(parts)])
    assert out == parts
    assert [part.text for part in out] == ["hello", "", "there"]


def test_tool_feedback_keeps_label_and_structured_parts():
    image = ImagePart(url="https://x/tool.png")
    attached = FilePart(name="result.txt", content="payload", mime="text/plain")
    out = format_events_for_context(
        [
            create_tool_complete_event(
                job_id="bash_x",
                content=[TextPart(text="tool output"), image, attached],
            )
        ]
    )
    assert isinstance(out, list)
    text = "\n".join(part.text for part in out if isinstance(part, TextPart))
    assert text.startswith("[Tool bash_x completed]")
    assert "tool output" in text
    assert any(isinstance(part, ImagePart) and part.url == image.url for part in out)
    assert any(isinstance(part, FilePart) and part.name == "result.txt" for part in out)


def test_prompt_override_keeps_structured_parts():
    image = ImagePart(url="https://x/trig.png")
    out = format_events_for_context(
        [
            TriggerEvent(
                type="custom_trigger",
                content=[TextPart(text="body"), image],
                prompt_override="rendered prompt",
            )
        ]
    )
    assert isinstance(out, list)
    text = "\n".join(part.text for part in out if isinstance(part, TextPart))
    assert "rendered prompt" in text
    assert any(isinstance(part, ImagePart) for part in out)
