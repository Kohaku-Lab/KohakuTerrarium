from textual.widgets import Label
from textual.widgets._markdown import MarkdownFence

from kohakuterrarium.builtins.tui.output import TUIOutput
from kohakuterrarium.builtins.tui.session import TUISession
from kohakuterrarium.builtins.tui.widgets.blocks import ToolBlock


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


async def test_resume_restores_complete_markdown_and_safe_tool_arguments():
    long_path = "/" + "restored-path-segment/" * 10 + "artifact.png"
    long_prompt = "restored-prompt-" * 8
    events = [
        {"type": "user_input", "content": "restore this turn"},
        {"type": "text", "content": f"```text\n{long_path}\n```"},
        {
            "type": "tool_call",
            "name": "custom_tool",
            "call_id": "call-1",
            "args": {
                "prompt": long_prompt,
                "count": 2,
                "content": "hidden content",
                "_internal": "hidden internal",
            },
        },
        {
            "type": "tool_result",
            "name": "custom_tool",
            "call_id": "call-1",
            "output": "OK",
        },
    ]
    session = TUISession()
    await session.start()
    assert session._app is not None
    output = TUIOutput()
    output._tui = session

    async with session._app.run_test(size=(60, 20)) as pilot:
        await output.on_resume(events)
        await pilot.pause()

        fence = session._app.query_one(MarkdownFence)
        assert fence.code == long_path
        assert long_path in fence.query_one("#code-content", Label).render().plain

        tool = session._app.query_one(ToolBlock)
        assert tool._args_widget.render().plain == (
            f"Arguments:\nprompt={long_prompt}\ncount=2"
        )
        assert "hidden content" not in tool._args_widget.render().plain
        assert "hidden internal" not in tool._args_widget.render().plain


async def test_live_tool_start_mounts_complete_safe_arguments():
    long_prompt = "live-prompt-" * 12
    session = TUISession()
    await session.start()
    assert session._app is not None
    output = TUIOutput()
    output._tui = session

    async with session._app.run_test(size=(60, 20)) as pilot:
        output.on_activity_with_metadata(
            "tool_start",
            "[custom_tool] fallback",
            {
                "job_id": "job-1",
                "args": {
                    "prompt": long_prompt,
                    "count": 2,
                    "content": "hidden content",
                    "_internal": "hidden internal",
                },
            },
        )
        await pilot.pause()

        tool = session._app.query_one(ToolBlock)
        assert tool._args_widget.render().plain == (
            f"Arguments:\nprompt={long_prompt}\ncount=2"
        )
        assert "hidden content" not in tool._args_widget.render().plain
        assert "hidden internal" not in tool._args_widget.render().plain
