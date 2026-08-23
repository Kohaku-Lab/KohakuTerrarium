"""Tests for TUI activity blocks."""

import pytest
from textual.app import App, ComposeResult
from textual.widgets._collapsible import CollapsibleTitle

from kohakuterrarium.builtins.tui.widgets.blocks import (
    CompactSummaryBlock,
    SubAgentBlock,
    ToolBlock,
)

HOSTILE_TEXT = "[/red] [done] [/etc/passwd] [] [b]x"


class _BlockHost(App):
    def compose(self) -> ComposeResult:
        yield ToolBlock(HOSTILE_TEXT, HOSTILE_TEXT, id="tool")
        yield SubAgentBlock(HOSTILE_TEXT, HOSTILE_TEXT, id="subagent")
        yield CompactSummaryBlock(HOSTILE_TEXT, id="compact")


@pytest.mark.asyncio
async def test_dynamic_block_text_is_literal_during_layout_and_updates() -> None:
    app = _BlockHost()

    async with app.run_test() as pilot:
        tool = app.query_one("#tool", ToolBlock)
        tool.mark_done(HOSTILE_TEXT, HOSTILE_TEXT)
        subagent = app.query_one("#subagent", SubAgentBlock)
        subagent.add_tool_line(HOSTILE_TEXT, HOSTILE_TEXT)
        subagent.mark_done(HOSTILE_TEXT)
        compact = app.query_one("#compact", CompactSummaryBlock)
        compact.mark_done(HOSTILE_TEXT)
        await pilot.pause()

        assert tool._output_widget.render().plain == HOSTILE_TEXT
        assert HOSTILE_TEXT in tool.query_one(CollapsibleTitle).render().plain
        assert subagent._result_widget.render().plain == HOSTILE_TEXT
        assert HOSTILE_TEXT in subagent.query_one(CollapsibleTitle).render().plain
        assert compact._body.render().plain == HOSTILE_TEXT
        assert tool.has_class("-done")
        assert subagent.has_class("-done")
        assert compact.has_class("-done")
