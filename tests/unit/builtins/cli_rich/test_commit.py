"""Unit tests for ``builtins/cli_rich/commit.py`` — SessionReplay rendering."""

from kohakuterrarium.builtins.cli_rich.commit import (
    ScrollbackCommitter,
    SessionReplay,
)


class StubApp:
    """Duck-typed RichCLIApp: committer writes straight to stdout."""

    def __init__(self):
        self.app = None
        self.committer = ScrollbackCommitter(self)

    def _terminal_width(self) -> int:
        return 80


def test_replay_renders_multimodal_user_input_as_text(capsys):
    replay = SessionReplay(StubApp())
    events = [
        {
            "type": "user_input",
            "content": [{"text": "hello from web", "type": "text"}],
        },
        {"type": "text", "content": "answer"},
        {"type": "processing_end", "content": ""},
    ]

    replay.replay(events)

    assert "hello from web" in capsys.readouterr().out


def test_replay_keeps_plain_string_user_input(capsys):
    replay = SessionReplay(StubApp())

    replay.replay([{"type": "user_input", "content": "plain text"}])

    assert "plain text" in capsys.readouterr().out
