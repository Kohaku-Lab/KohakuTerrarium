"""Unit tests for llm/context_repair.py: the edits that remove refused content."""

from copy import deepcopy

from kohakuterrarium.llm.context_repair import (
    CONTENT_REPAIR_LADDER,
    REASON_LIMIT,
    ContextRepair,
    drop_tool_round,
    media_note,
    strip_media,
)


def _image(url):
    return {"type": "image_url", "image_url": {"url": url}}


def _conversation():
    return [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": [{"type": "text", "text": "look"}, _image("a")]},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"id": "c1", "type": "function", "function": {"name": "read"}}
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "c1",
            "content": [
                {"type": "text", "text": "PDF: doc.pdf"},
                _image("p1"),
                {"type": "input_image", "image_url": "p2"},
            ],
        },
    ]


def test_strip_newest_edits_only_the_last_media_message_and_explains():
    messages = _conversation()
    original = deepcopy(messages)
    changed, repaired = strip_media(messages, "newest", "invalid_image: bad page")
    assert changed == 2
    assert messages == original
    assert repaired[1] is messages[1]
    tool = repaired[3]["content"]
    assert [p["type"] for p in tool] == ["text", "text"]
    assert tool[0]["text"] == "PDF: doc.pdf"
    assert "2 image(s) removed" in tool[1]["text"]
    assert "invalid_image: bad page" in tool[1]["text"]
    assert repaired[3]["tool_call_id"] == "c1"


def test_strip_all_reaches_every_user_and_tool_message():
    changed, repaired = strip_media(_conversation(), "all", "too big")
    assert changed == 3
    for message in repaired:
        content = message.get("content")
        if isinstance(content, list):
            assert not [p for p in content if p.get("type") != "text"]


def test_strip_without_media_is_a_no_op_returning_the_same_list():
    messages = [{"role": "user", "content": "hi"}]
    assert strip_media(messages, "all", "x") == (0, messages)


def test_assistant_media_is_never_stripped():
    messages = [{"role": "assistant", "content": [_image("generated")]}]
    assert strip_media(messages, "all", "x")[0] == 0


def test_note_reason_is_collapsed_and_bounded():
    note = media_note(1, "line one\n\n   line two " + "x" * 1000)
    assert "line one line two" in note
    assert len(note) < REASON_LIMIT + 400
    assert "the provider rejected the request" in media_note(1, "")


def test_note_reason_keeps_the_text_of_an_html_proxy_page():
    page = (
        "Error code: 413 - <html>\r\n<head><title>413 Request Entity Too Large"
        "</title></head>\r\n<body><center><h1>413</h1></center></body></html>"
    )
    note = media_note(1, page)
    assert "<" not in note.split("(", 1)[1]
    assert "Error code: 413 - 413 Request Entity Too Large 413" in note
    comparison = media_note(1, "prompt is too long: 345320 tokens > 199999 maximum")
    assert "345320 tokens > 199999 maximum" in comparison
    assert "a < b" in media_note(1, "a < b")


def test_drop_tool_round_names_the_rejection_in_its_placeholder():
    changed, repaired = drop_tool_round(_conversation(), "string too long")
    assert changed == 1
    assert [m["role"] for m in repaired] == ["system", "user", "user"]
    placeholder = repaired[-1]["content"]
    assert placeholder.startswith("[tool-result truncated]")
    assert placeholder.endswith(
        "The model provider rejected the request: string too long"
    )


def test_drop_tool_round_without_a_round_changes_nothing():
    messages = [{"role": "user", "content": "hi"}]
    assert drop_tool_round(messages, "x") == (0, messages)


def test_repair_dispatches_by_kind_and_ladder_order_is_fixed():
    messages = _conversation()
    assert ContextRepair("strip_media", "all", "r").apply(messages)[0] == 3
    assert ContextRepair("drop_tool_round", reason="r").apply(messages)[0] == 1
    assert CONTENT_REPAIR_LADDER == (
        ("strip_media", "newest"),
        ("strip_media", "all"),
        ("drop_tool_round", "newest"),
    )
