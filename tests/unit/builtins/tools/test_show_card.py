"""Unit tests for :class:`ShowCardTool`.

Behavior-first: display-only cards emit and return at once; interactive
cards block for a button click and return its id; link-only cards never
wait; and a headless router yields an explicit no-responder result
instead of blocking forever.
"""

import asyncio
from pathlib import Path

from kohakuterrarium.builtins.outputs.none import NoneOutput
from kohakuterrarium.builtins.tools.show_card import ShowCardTool
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


async def _reply_when_pending(router, action_id):
    for _ in range(400):
        if router._pending_replies:
            event_id = next(iter(router._pending_replies))
            router.submit_reply(UIReply(event_id=event_id, action_id=action_id))
            return event_id
        await asyncio.sleep(0.005)
    raise AssertionError("show_card never registered a pending reply")


def _card_events(recorder):
    return [e for e in recorder.events if e.type == "card"]


class TestDisplayOnly:
    async def test_no_actions_emits_and_returns(self):
        recorder = OutputRecorder()
        router = OutputRouter(recorder)
        result = await asyncio.wait_for(
            ShowCardTool()._execute(
                {"title": "Status", "body": "all good"}, _ctx(router)
            ),
            timeout=1,
        )
        assert result.output == "card displayed"
        cards = _card_events(recorder)
        assert len(cards) == 1
        assert cards[0].payload["title"] == "Status"
        assert router._pending_replies == {}


class TestInteractive:
    async def test_action_click_returns_action_id(self):
        router = OutputRouter(OutputRecorder())
        args = {
            "title": "Approve?",
            "actions": [{"id": "approve", "label": "Approve", "style": "primary"}],
        }
        task = asyncio.create_task(ShowCardTool()._execute(args, _ctx(router)))
        await _reply_when_pending(router, "approve")
        result = await asyncio.wait_for(task, timeout=2)
        assert result.output == "action: approve"

    async def test_timeout_returns_timeout_message(self):
        router = OutputRouter(OutputRecorder())
        args = {
            "title": "Approve?",
            "timeout_s": 0.05,
            "actions": [{"id": "approve", "label": "Approve", "style": "primary"}],
        }
        result = await asyncio.wait_for(
            ShowCardTool()._execute(args, _ctx(router)), timeout=2
        )
        assert result.output == "card timed out without reply"

    async def test_link_and_button_mix_still_waits(self):
        # A link action alongside a real button must NOT collapse to
        # display-only — the button can still resolve the wait.
        router = OutputRouter(OutputRecorder())
        args = {
            "title": "Docs or go?",
            "actions": [
                {"id": "doc", "label": "Docs", "style": "link", "url": "http://x"},
                {"id": "go", "label": "Go", "style": "primary"},
            ],
        }
        task = asyncio.create_task(ShowCardTool()._execute(args, _ctx(router)))
        await _reply_when_pending(router, "go")
        result = await asyncio.wait_for(task, timeout=2)
        assert result.output == "action: go"


class TestLinkOnly:
    async def test_link_only_card_does_not_wait(self):
        recorder = OutputRecorder()
        router = OutputRouter(recorder)
        args = {
            "title": "More info",
            "actions": [
                {"id": "doc", "label": "Open", "style": "link", "url": "http://x"}
            ],
        }
        result = await asyncio.wait_for(
            ShowCardTool()._execute(args, _ctx(router)), timeout=1
        )
        assert result.output == "card displayed"
        # Never registered an interactive wait, but still showed the card
        # with its link action intact.
        assert router._pending_replies == {}
        cards = _card_events(recorder)
        assert len(cards) == 1
        assert cards[0].payload["actions"][0]["url"] == "http://x"


class TestHeadless:
    async def test_headless_interactive_card_returns_no_responder(self):
        router = OutputRouter(NoneOutput())
        args = {
            "title": "Approve?",
            "actions": [{"id": "approve", "label": "Approve", "style": "primary"}],
        }
        result = await asyncio.wait_for(
            ShowCardTool()._execute(args, _ctx(router)), timeout=1
        )
        assert "responder" in result.output
        assert result.success is True
        assert router._pending_replies == {}


class TestValidationAndFallback:
    async def test_missing_title_errors(self):
        result = await ShowCardTool()._execute({}, _ctx(OutputRouter(OutputRecorder())))
        assert result.error is not None

    async def test_no_router_returns_fallback_text(self):
        result = await ShowCardTool()._execute({"title": "Hi", "body": "b"}, _ctx(None))
        assert "Hi" in result.get_text_output()

    def test_description_points_to_ask_user(self):
        assert "ask_user" in ShowCardTool().description
        assert "Blocks the turn" in ShowCardTool().prompt_contribution()
