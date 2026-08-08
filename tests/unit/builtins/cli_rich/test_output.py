"""Tests for :mod:`kohakuterrarium.builtins.cli_rich.output`."""

from kohakuterrarium.builtins.cli_rich.output import RichCLIOutput


class _RecordingApp:
    def __init__(self):
        self.notifications = []
        self.errors = []

    def on_notification_event(self, payload: dict) -> None:
        self.notifications.append(payload)

    def on_processing_error(self, error_type: str, error: str) -> None:
        self.errors.append((error_type, error))


def test_command_result_is_rendered_as_notification() -> None:
    app = _RecordingApp()
    output = RichCLIOutput(app)

    output.on_activity_with_metadata(
        "command_result",
        "Available commands",
        {"command": "/help", "source": "cli"},
    )

    assert app.notifications == [
        {"title": "help", "text": "Available commands", "level": "info"}
    ]


def test_command_error_is_rendered_as_processing_error() -> None:
    app = _RecordingApp()
    output = RichCLIOutput(app)

    output.on_activity_with_metadata(
        "command_error",
        "Unknown command",
        {"command": "/nope arg", "source": "cli"},
    )

    assert app.errors == [("nope", "Unknown command")]
