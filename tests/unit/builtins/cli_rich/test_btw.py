"""Busy-safe CLI commands preserve the parent task and side-output identity."""

import asyncio
from types import SimpleNamespace

import pytest

from kohakuterrarium.builtins.cli_rich.app.app import RichCLIApp
from kohakuterrarium.builtins.cli_rich.output import RichCLIOutput
from kohakuterrarium.modules.user_command.base import UserCommandResult


@pytest.mark.asyncio
async def test_safe_command_keeps_parent_task_and_captures_target():
    commands = []
    interrupted = []

    async def execute(text):
        commands.append(text)
        return UserCommandResult(
            output="side transcript",
            data={"type": "side_conversation", "job_id": "side-1"},
        )

    agent = SimpleNamespace(
        llm_identifier=lambda: "test",
        llm=SimpleNamespace(model="test"),
        config=SimpleNamespace(name="test"),
        _processing_task=None,
        _try_slash_command_text=execute,
        interrupt=lambda: interrupted.append(True),
    )
    app = RichCLIApp(agent)
    app._command_registry = {
        "btw": SimpleNamespace(safe_while_busy=True, aliases=["aside"])
    }
    app._commit_user_message = lambda text: None
    notices = []
    app.on_notification_event = notices.append
    parent = asyncio.create_task(asyncio.Event().wait())
    app._pending_task = parent
    app._turn_tasks[""] = parent
    app._processing = True
    try:
        app._handle_submit("/aside question")
        tasks = list(app._side_command_tasks)
        app.agent = SimpleNamespace()
        await asyncio.gather(*tasks)
        assert commands == ["/aside question"]
        assert not parent.done()
        assert app._pending_task is parent
        assert app._turn_tasks[""] is parent
        assert app._processing
        assert interrupted == []
        assert notices == [
            {
                "title": "btw · side conversation side-1",
                "text": "side transcript",
                "level": "info",
            }
        ]
    finally:
        parent.cancel()
        await asyncio.gather(parent, return_exceptions=True)


@pytest.mark.parametrize("kind,error", [("btw_started", None), ("btw_result", "oops")])
def test_side_activity_never_enters_parent_text(kind, error):
    notices = []
    app = SimpleNamespace(on_notification_event=notices.append)
    output = RichCLIOutput(app)
    output.on_activity_with_metadata(kind, "side", {"job_id": "job", "error": error})
    assert notices == [
        {
            "title": "btw · side conversation job",
            "text": "side",
            "level": "error" if error else "info",
        }
    ]
