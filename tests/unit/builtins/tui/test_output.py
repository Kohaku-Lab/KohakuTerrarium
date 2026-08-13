from kohakuterrarium.builtins.tui.output import TUIOutput


class FakeTUI:
    def __init__(self):
        self.notices = []

    def add_system_notice(
        self, text: str, command: str = "", error: bool = False, target: str = ""
    ) -> None:
        self.notices.append(
            {
                "text": text,
                "command": command,
                "error": error,
                "target": target,
            }
        )


def test_command_result_activity_renders_notice():
    tui = FakeTUI()
    output = TUIOutput()
    output._tui = tui
    output._default_target = "general"

    output.on_activity_with_metadata(
        "command_result",
        "Available commands:",
        {"command": "/help", "source": "tui"},
    )

    assert tui.notices == [
        {
            "text": "Available commands:",
            "command": "help",
            "error": False,
            "target": "general",
        }
    ]


def test_command_error_activity_renders_error_notice():
    tui = FakeTUI()
    output = TUIOutput()
    output._tui = tui
    output._default_target = "general"

    output.on_activity_with_metadata(
        "command_error",
        "bad command",
        {"command": "/nope arg", "source": "tui"},
    )

    assert tui.notices == [
        {
            "text": "bad command",
            "command": "nope",
            "error": True,
            "target": "general",
        }
    ]
