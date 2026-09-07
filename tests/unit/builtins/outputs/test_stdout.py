"""Unit tests for ``builtins/outputs/stdout.py`` — resume history summary."""

from kohakuterrarium.builtins.outputs.stdout import StdoutOutput, _group_resume_events


def test_group_resume_events_renders_multimodal_user_input_as_text():
    events = [
        {
            "type": "user_input",
            "content": [{"text": "hello from web", "type": "text"}],
        },
        {"type": "text", "content": "answer"},
    ]

    turns = _group_resume_events(events)

    assert turns[0]["user"] == "hello from web"
    assert turns[0]["text"] == "answer"


def test_group_resume_events_keeps_plain_string_content():
    events = [
        {"type": "user_input", "content": "plain"},
        {"type": "text", "content": "reply"},
    ]

    turns = _group_resume_events(events)

    assert turns[0]["user"] == "plain"
    assert turns[0]["text"] == "reply"


async def test_on_resume_prints_text_not_repr(capsys):
    output = StdoutOutput()
    events = [
        {
            "type": "user_input",
            "content": [{"text": "hello from web", "type": "text"}],
        },
    ]

    await output.on_resume(events)

    out = capsys.readouterr().out
    assert "You: hello from web" in out
    assert "'type': 'text'" not in out
