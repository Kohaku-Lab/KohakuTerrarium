"""Unit tests for ``builtins/tui/resume.py`` — replay turn grouping."""

from textual.widgets import Markdown

from kohakuterrarium.builtins.tui.resume import _build_turn_widgets, _group_into_turns


def test_user_input_list_content_becomes_display_text():
    events = [
        {
            "type": "user_input",
            "content": [{"text": "hello from web", "type": "text"}],
        },
    ]

    turns = _group_into_turns(events)

    assert len(turns) == 1
    assert turns[0]["input"] == "hello from web"


def test_trigger_list_content_becomes_display_text():
    events = [
        {
            "type": "trigger_fired",
            "channel": "tasks",
            "sender": "bot",
            "content": [{"text": "channel work", "type": "text"}],
        },
    ]

    turns = _group_into_turns(events)

    assert turns[0]["trigger_content"] == "channel work"


def test_string_content_unchanged():
    events = [
        {"type": "user_input", "content": "plain"},
        {"type": "text", "content": "reply"},
    ]

    turns = _group_into_turns(events)

    assert turns[0]["input"] == "plain"
    assert turns[0]["steps"] == [("text", "reply")]


def test_resumed_text_with_a_ui_block_shows_its_text_form():
    turn = {
        "input_type": "user_input",
        "input": "split",
        "steps": [("text", 'Here:\n<ui><stat label="Each">40</stat></ui>')],
    }
    widgets, _, _ = _build_turn_widgets(turn, None, {}, {})
    (markdown,) = [w for w in widgets if isinstance(w, Markdown)]
    text = markdown._initial_markdown
    assert "Each: 40" in text and "<ui" not in text
