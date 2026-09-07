"""Unit tests for ``builtins/tui/resume.py`` — replay turn grouping."""

from kohakuterrarium.builtins.tui.resume import _group_into_turns


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
