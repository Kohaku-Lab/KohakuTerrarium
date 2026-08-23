"""Tests for TUI information panels."""

import pytest
from textual.app import App, ComposeResult

from kohakuterrarium.builtins.tui.widgets.panels import (
    RunningPanel,
    ScratchpadPanel,
    SessionInfoPanel,
    TerrariumPanel,
)

HOSTILE_TEXT = "[/red] [done] [/etc/passwd] [] [b]x"


class _PanelHost(App):
    def compose(self) -> ComposeResult:
        yield RunningPanel(id="running")
        yield ScratchpadPanel(id="scratchpad")
        yield SessionInfoPanel(id="session")
        yield TerrariumPanel(id="terrarium")


@pytest.mark.asyncio
async def test_dynamic_panel_text_is_literal() -> None:
    app = _PanelHost()

    async with app.run_test() as pilot:
        app.query_one("#running", RunningPanel).add_item("job", HOSTILE_TEXT)
        app.query_one("#scratchpad", ScratchpadPanel).update_data(
            {HOSTILE_TEXT: HOSTILE_TEXT}
        )
        app.query_one("#session", SessionInfoPanel).set_info(
            session_id=HOSTILE_TEXT,
            model=HOSTILE_TEXT,
            agent_name=HOSTILE_TEXT,
        )
        app.query_one("#terrarium", TerrariumPanel).set_topology(
            [{"name": HOSTILE_TEXT, "running": True}],
            [{"name": HOSTILE_TEXT, "type": HOSTILE_TEXT}],
        )
        await pilot.pause()

        for selector, panel_type in (
            ("#running", RunningPanel),
            ("#scratchpad", ScratchpadPanel),
            ("#session", SessionInfoPanel),
            ("#terrarium", TerrariumPanel),
        ):
            assert HOSTILE_TEXT in app.query_one(selector, panel_type).render().plain
