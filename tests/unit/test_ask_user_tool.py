"""Test ask_user emits an ``ask_text`` OutputEvent and returns the reply.

Phase B rewires ask_user from stderr/stdin to the bus. These tests pin
that contract so a regression in the bus or the tool flips them red.
"""

import asyncio
from types import SimpleNamespace

import pytest

from kohakuterrarium.builtins.tools.ask_user import AskUserTool
from kohakuterrarium.modules.output.event import UIReply
from kohakuterrarium.modules.output.router import OutputRouter
from kohakuterrarium.modules.tool.base import ToolContext
from kohakuterrarium.testing import OutputRecorder


class _AutoReplyOutput(OutputRecorder):
    def __init__(self):
        super().__init__()
        self._text: str | None = None

    def set_reply_text(self, text: str) -> None:
        self._text = text

    async def emit(self, event):
        await super().emit(event)
        if event.type == "ask_text" and self._text is not None:
            asyncio.create_task(self._deliver(event.id))

    async def _deliver(self, event_id: str) -> None:
        await asyncio.sleep(0.01)
        router = getattr(self, "_router", None)
        if router is None:
            return
        router.submit_reply(
            UIReply(
                event_id=event_id,
                action_id="submit",
                values={"text": self._text},
            )
        )


def _make_context(router: OutputRouter) -> ToolContext:
    fake_agent = SimpleNamespace(output_router=router)
    return ToolContext(
        agent_name="test",
        session=None,
        working_dir=__import__("pathlib").Path("."),
        agent=fake_agent,
    )


@pytest.mark.asyncio
async def test_ask_user_emits_ask_text_event_and_returns_reply():
    rec = _AutoReplyOutput()
    rec.set_reply_text("blue")
    router = OutputRouter(default_output=rec)
    tool = AskUserTool()
    ctx = _make_context(router)

    result = await tool._execute(
        {"question": "Favourite colour?", "timeout_s": 2.0}, context=ctx
    )

    assert result.output == "blue"
    assert result.exit_code == 0
    # The ask_text event was actually emitted with our prompt.
    ask_events = [e for e in rec.events if e.type == "ask_text"]
    assert len(ask_events) == 1
    assert ask_events[0].payload["prompt"] == "Favourite colour?"


@pytest.mark.asyncio
async def test_ask_user_returns_no_response_on_timeout():
    rec = OutputRecorder()  # plain — no auto reply
    router = OutputRouter(default_output=rec)
    tool = AskUserTool()
    ctx = _make_context(router)

    result = await tool._execute(
        {"question": "Anyone?", "timeout_s": 0.05}, context=ctx
    )
    assert "no response" in result.output.lower()
    assert result.exit_code == 0


@pytest.mark.asyncio
async def test_ask_user_requires_question():
    tool = AskUserTool()
    result = await tool._execute({}, context=None)
    assert result.error
