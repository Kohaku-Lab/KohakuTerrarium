"""Tests for TUI conversation message widgets."""

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Static
from textual.widgets._collapsible import CollapsibleTitle

from kohakuterrarium.builtins.tui.widgets.messages import (
    QueuedMessage,
    StreamingText,
    SystemNotice,
    TriggerMessage,
    UserMessage,
)

HOSTILE_TEXT = "[/red] [done] [/etc/passwd] [] [b]x"


class _MessageHost(App):
    def compose(self) -> ComposeResult:
        yield UserMessage(HOSTILE_TEXT, id="user")
        yield QueuedMessage(HOSTILE_TEXT, id="queued")
        yield SystemNotice(HOSTILE_TEXT, command=HOSTILE_TEXT, error=True, id="notice")
        yield TriggerMessage(HOSTILE_TEXT, HOSTILE_TEXT, id="trigger")
        yield StreamingText(id="stream")


@pytest.mark.asyncio
async def test_dynamic_message_text_is_literal_during_layout() -> None:
    app = _MessageHost()

    async with app.run_test() as pilot:
        app.query_one("#stream", StreamingText).append(HOSTILE_TEXT)
        await pilot.pause()

        for selector in ("#user", "#queued", "#notice", "#stream"):
            assert app.query_one(selector, Static).render().plain == HOSTILE_TEXT

        trigger = app.query_one("#trigger", TriggerMessage)
        assert trigger._body.render().plain == HOSTILE_TEXT
        title = trigger.query_one(CollapsibleTitle).render().plain
        assert HOSTILE_TEXT in title
        notice = app.query_one("#notice", SystemNotice)
        assert notice.render_str(notice.border_title).plain == f"/{HOSTILE_TEXT}"
        assert notice.has_class("--error")
