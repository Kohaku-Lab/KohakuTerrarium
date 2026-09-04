"""Unit tests for :class:`AskUserTool`.

Behavior-first: the tool emits an ``ask_text`` event and returns the
reply text, degrades to explicit strings on empty reply / timeout, and
never blocks forever when no interactive UI is attached (headless).
"""

import asyncio
from pathlib import Path

from kohakuterrarium.builtins.outputs.none import NoneOutput
from kohakuterrarium.builtins.tools.ask_user import AskUserTool
from kohakuterrarium.modules.output.event import UIReply
from kohakuterrarium.modules.output.router import OutputRouter
from kohakuterrarium.modules.tool.base import ToolContext
from kohakuterrarium.testing.output import OutputRecorder


class _FakeAgent:
    def __init__(self, router):
        self.output_router = router


def _ctx(router) -> ToolContext:
    agent = _FakeAgent(router) if router is not None else None
    return ToolContext(agent_name="t", session=None, working_dir=Path("."), agent=agent)


async def _reply_when_pending(router, *, text=None, action_id=None):
    """Wait until the tool registers a pending reply, then answer it."""
    for _ in range(400):
        if router._pending_replies:
            event_id = next(iter(router._pending_replies))
            values = {"text": text} if text is not None else {}
            router.submit_reply(
                UIReply(event_id=event_id, action_id=action_id or "", values=values)
            )
            return event_id
        await asyncio.sleep(0.005)
    raise AssertionError("ask_user never registered a pending reply")


class TestAskUserReply:
    async def test_submit_returns_reply_text(self):
        router = OutputRouter(OutputRecorder())
        tool = AskUserTool()
        task = asyncio.create_task(tool._execute({"question": "Which?"}, _ctx(router)))
        await _reply_when_pending(router, text="the second one")
        result = await asyncio.wait_for(task, timeout=2)
        assert result.output == "the second one"
        assert result.success is True

    async def test_empty_reply_returns_no_response(self):
        router = OutputRouter(OutputRecorder())
        tool = AskUserTool()
        task = asyncio.create_task(tool._execute({"question": "Q?"}, _ctx(router)))
        await _reply_when_pending(router, text="")
        result = await asyncio.wait_for(task, timeout=2)
        assert result.output == "(no response)"

    async def test_timeout_returns_timeout_message(self):
        router = OutputRouter(OutputRecorder())
        tool = AskUserTool()
        result = await asyncio.wait_for(
            tool._execute({"question": "Q?", "timeout_s": 0.05}, _ctx(router)),
            timeout=2,
        )
        assert result.output == "(no response within timeout)"


class TestAskUserHeadless:
    async def test_headless_router_returns_no_responder_without_blocking(self):
        router = OutputRouter(NoneOutput())
        tool = AskUserTool()
        # Must return promptly — a NoneOutput default has no responder.
        result = await asyncio.wait_for(
            tool._execute({"question": "anyone?"}, _ctx(router)), timeout=1
        )
        assert "responder" in result.output
        assert result.success is True
        # No orphaned pending wait was registered.
        assert router._pending_replies == {}


class TestAskUserValidation:
    async def test_missing_question_errors(self):
        router = OutputRouter(OutputRecorder())
        result = await AskUserTool()._execute({}, _ctx(router))
        assert result.error is not None

    def test_require_manual_read_is_disabled(self):
        # The gate was dropped: ask_user is a self-explaining free-text
        # prompt and must be dispatchable without an ``info`` read.
        assert AskUserTool().require_manual_read is False

    def test_description_points_to_show_card(self):
        # The disambiguation lives in the description, which is always loaded;
        # the contribution carries only what the description cannot.
        assert "show_card" in AskUserTool().description
        assert "Blocks the turn" in AskUserTool().prompt_contribution()
