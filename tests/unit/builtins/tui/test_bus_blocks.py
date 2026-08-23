"""Tests for TUI card and progress event blocks."""

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Button, Static

from kohakuterrarium.builtins.tui.widgets.bus_blocks import CardBlock, ProgressBlock

HOSTILE_TEXT = "[/red] [done] [/etc/passwd] [] [b]x"


class _BusBlockHost(App):
    def compose(self) -> ComposeResult:
        yield CardBlock(
            {
                "title": HOSTILE_TEXT,
                "subtitle": HOSTILE_TEXT,
                "fields": [{"label": HOSTILE_TEXT, "value": HOSTILE_TEXT}],
                "footer": HOSTILE_TEXT,
                "actions": [{"id": "safe", "label": HOSTILE_TEXT}],
            },
            on_action=lambda _event_id, _action_id: None,
            event_id="event",
        )
        yield ProgressBlock("safe", HOSTILE_TEXT)


@pytest.mark.asyncio
async def test_dynamic_card_and_progress_text_is_literal() -> None:
    app = _BusBlockHost()

    async with app.run_test() as pilot:
        progress = app.query_one("#progress-safe", ProgressBlock)
        progress.update_progress(HOSTILE_TEXT, 1, 1, False, True)
        await pilot.pause()

        card = app.query_one(CardBlock)
        assert card.query_one(".card-header", Static).render().plain == HOSTILE_TEXT
        assert card.query_one(".card-subtitle", Static).render().plain == HOSTILE_TEXT
        field = card.query_one(".card-fields").query_one(Static)
        assert field.render().plain == f"{HOSTILE_TEXT}: {HOSTILE_TEXT}"
        assert card.query_one(".card-footer", Static).render().plain == HOSTILE_TEXT
        assert card.query_one(Button).label.plain == HOSTILE_TEXT
        assert (
            HOSTILE_TEXT in progress.query_one(".progress-label", Static).render().plain
        )
