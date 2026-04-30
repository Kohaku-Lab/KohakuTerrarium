"""Tests for the show_card builtin tool."""

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from kohakuterrarium.builtins.tools.show_card import ShowCardTool
from kohakuterrarium.modules.output.event import UIReply
from kohakuterrarium.modules.output.router import OutputRouter
from kohakuterrarium.modules.tool.base import ToolContext
from kohakuterrarium.testing import OutputRecorder


class _AutoReplyOutput(OutputRecorder):
    """Auto-submits a reply for any incoming card event."""

    def __init__(self):
        super().__init__()
        self._action_id: str | None = None

    def set_reply(self, action_id: str) -> None:
        self._action_id = action_id

    async def emit(self, event):
        await super().emit(event)
        if event.type == "card" and self._action_id is not None:
            asyncio.create_task(self._deliver(event.id))

    async def _deliver(self, event_id: str) -> None:
        await asyncio.sleep(0.01)
        router = getattr(self, "_router", None)
        if router is None:
            return
        router.submit_reply(
            UIReply(
                event_id=event_id,
                action_id=self._action_id,
                values={"action_id": self._action_id},
            )
        )


def _make_context(router: OutputRouter | None) -> ToolContext:
    fake_agent = SimpleNamespace(output_router=router) if router else None
    return ToolContext(
        agent_name="test",
        session=None,
        working_dir=Path("."),
        agent=fake_agent,
    )


@pytest.mark.asyncio
async def test_show_card_requires_title():
    tool = ShowCardTool()
    result = await tool._execute({}, context=None)
    assert result.error
    assert "title" in result.error.lower()


@pytest.mark.asyncio
async def test_show_card_display_only_emits_event_and_returns():
    rec = OutputRecorder()
    router = OutputRouter(default_output=rec)
    tool = ShowCardTool()
    ctx = _make_context(router)

    result = await tool._execute(
        {
            "title": "Test",
            "subtitle": "x",
            "accent": "info",
            "fields": [{"label": "a", "value": "1", "inline": True}],
            "body": "Hello world",
        },
        context=ctx,
    )
    assert result.exit_code == 0
    cards = [e for e in rec.events if e.type == "card"]
    assert len(cards) == 1
    assert cards[0].payload["title"] == "Test"
    assert cards[0].payload["accent"] == "info"


@pytest.mark.asyncio
async def test_show_card_interactive_returns_action_id():
    rec = _AutoReplyOutput()
    rec.set_reply("approve")
    router = OutputRouter(default_output=rec)
    tool = ShowCardTool()
    ctx = _make_context(router)

    result = await tool._execute(
        {
            "title": "Plan",
            "actions": [
                {"id": "approve", "label": "Approve", "style": "primary"},
                {"id": "reject", "label": "Reject", "style": "danger"},
            ],
        },
        context=ctx,
    )
    assert result.exit_code == 0
    assert "approve" in result.output
    cards = [e for e in rec.events if e.type == "card"]
    assert len(cards) == 1
    # Both buttons made it through the payload normalisation.
    assert {a["id"] for a in cards[0].payload["actions"]} == {"approve", "reject"}


@pytest.mark.asyncio
async def test_show_card_invalid_accent_drops_silently():
    """Unknown accent is filtered out so the schema stays clean."""
    rec = OutputRecorder()
    router = OutputRouter(default_output=rec)
    tool = ShowCardTool()
    ctx = _make_context(router)

    await tool._execute({"title": "x", "accent": "rainbow"}, context=ctx)
    card = next(e for e in rec.events if e.type == "card")
    assert "accent" not in card.payload


@pytest.mark.asyncio
async def test_show_card_link_action_includes_url():
    rec = OutputRecorder()
    router = OutputRouter(default_output=rec)
    tool = ShowCardTool()
    ctx = _make_context(router)

    await tool._execute(
        {
            "title": "Docs",
            "actions": [
                {"id": "open", "label": "Open", "style": "link", "url": "https://x"},
            ],
            "wait_for_reply": False,  # link-only, no awaiting
        },
        context=ctx,
    )
    card = next(e for e in rec.events if e.type == "card")
    action = card.payload["actions"][0]
    assert action["style"] == "link"
    assert action["url"] == "https://x"


@pytest.mark.asyncio
async def test_show_card_falls_back_to_text_when_no_router():
    """Programmatic / test invocation with no router returns a text
    rendering so the tool result is still useful.
    """
    tool = ShowCardTool()
    ctx = _make_context(None)
    result = await tool._execute(
        {
            "title": "Hi",
            "body": "body text",
            "fields": [{"label": "k", "value": "v"}],
        },
        context=ctx,
    )
    assert result.exit_code == 0
    assert "Hi" in result.output
    assert "body text" in result.output
    assert "k: v" in result.output


@pytest.mark.asyncio
async def test_show_card_strips_invalid_action_styles():
    rec = OutputRecorder()
    router = OutputRouter(default_output=rec)
    tool = ShowCardTool()
    ctx = _make_context(router)

    await tool._execute(
        {
            "title": "x",
            "actions": [
                {"id": "ok", "label": "OK", "style": "rainbow"},
            ],
            "wait_for_reply": False,
        },
        context=ctx,
    )
    card = next(e for e in rec.events if e.type == "card")
    assert card.payload["actions"][0]["style"] == "secondary"


@pytest.mark.asyncio
async def test_show_card_timeout_returns_status():
    rec = OutputRecorder()  # no auto-reply
    router = OutputRouter(default_output=rec)
    tool = ShowCardTool()
    ctx = _make_context(router)

    result = await tool._execute(
        {
            "title": "x",
            "actions": [{"id": "ok", "label": "OK"}],
            "timeout_s": 0.05,
        },
        context=ctx,
    )
    assert result.exit_code == 0
    assert "timed out" in result.output.lower()
