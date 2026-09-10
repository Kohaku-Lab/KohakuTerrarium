import asyncio

import pytest
from textual.widgets import Label
from textual.widgets._markdown import MarkdownFence

from kohakuterrarium.builtins.tui.output import TUIOutput
from kohakuterrarium.builtins.tui.session import TUISession
from kohakuterrarium.builtins.tui.widgets import UserMessage
from kohakuterrarium.builtins.tui.widgets.blocks import ToolBlock
from kohakuterrarium.modules.output.event import OutputEvent


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


async def test_resume_renders_multimodal_user_input_as_text():
    session = TUISession()
    await session.start()
    assert session._app is not None
    output = TUIOutput()
    output._tui = session

    events = [
        {
            "type": "user_input",
            "content": [{"text": "hello from web", "type": "text"}],
        },
        {"type": "text", "content": "hi there"},
    ]

    async with session._app.run_test(size=(60, 20)) as pilot:
        await output.on_resume(events)
        await pilot.pause()

        user = session._app.query_one(UserMessage)
        assert "hello from web" in user.render().plain


async def test_resume_buffers_events_until_tui_session_wired():
    session = TUISession()
    await session.start()
    assert session._app is not None
    output = TUIOutput()
    events = [{"type": "user_input", "content": "early history"}]

    async with session._app.run_test(size=(60, 20)) as pilot:
        # The resume_batch arrives before the TUI session is wired; the
        # agent clears its pending copy right after emitting, so losing
        # the buffer here loses the history permanently.
        await output.on_resume(events)
        await pilot.pause()
        assert not list(session._app.query(UserMessage))

        output._tui = session
        for _ in range(3):
            await pilot.pause()

        user = session._app.query_one(UserMessage)
        assert "early history" in user.render().plain


async def test_emit_serializes_live_output_behind_a_buffered_resume_replay():
    session = TUISession()
    await session.start()
    assert session._app is not None
    output = TUIOutput()
    order: list[str] = []

    async with session._app.run_test(size=(60, 20)):

        async def slow_replay(events):
            order.append("resume-start")
            await asyncio.sleep(0.05)
            order.append("resume-end")

        output.on_resume = slow_replay  # type: ignore[method-assign]
        original_write_stream = output.write_stream

        async def recording_write_stream(chunk: str) -> None:
            order.append("text")
            await original_write_stream(chunk)

        output.write_stream = recording_write_stream  # type: ignore[method-assign]
        output._buffered_resume_events = [
            {"type": "user_input", "content": "early history"}
        ]
        output._tui = session  # wiring flush schedules the replay as a task

        await output.emit(OutputEvent(type="text", content="live"))
        await asyncio.sleep(0.1)

        # Live output must wait for the in-flight replay; rendering it while
        # the history is still mounting would order the transcript wrong.
        assert order == ["resume-start", "resume-end", "text"]


async def test_resume_survives_slow_mount_without_raising(
    monkeypatch: pytest.MonkeyPatch,
):
    session = TUISession()
    await session.start()
    assert session._app is not None
    output = TUIOutput()
    output._tui = session

    monkeypatch.setattr(
        "kohakuterrarium.builtins.tui.output.RESUME_MOUNT_TIMEOUT", 0.05
    )

    async with session._app.run_test(size=(60, 20)) as pilot:
        original_call_later = session._app.call_later
        # A mount task that never signals completion must not propagate a
        # timeout into the agent's input loop.
        monkeypatch.setattr(session._app, "call_later", lambda *a, **k: None)
        await output.on_resume([{"type": "user_input", "content": "turn"}])
        monkeypatch.setattr(session._app, "call_later", original_call_later)
        await pilot.pause()
