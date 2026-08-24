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
LONG_ARGUMENT = "segment-without-spaces-" * 10


class _BlockHost(App):
    def compose(self) -> ComposeResult:
        yield ToolBlock(
            HOSTILE_TEXT,
            HOSTILE_TEXT,
            args_detail=HOSTILE_TEXT,
            id="tool",
        )
        yield SubAgentBlock(HOSTILE_TEXT, HOSTILE_TEXT, id="subagent")
        yield CompactSummaryBlock(HOSTILE_TEXT, id="compact")


class _ToolArgsHost(App):
    def compose(self) -> ComposeResult:
        yield ToolBlock(
            "short_tool",
            "prompt=short",
            args_detail="prompt=short",
            id="short-tool",
        )
        yield ToolBlock(
            "long_tool",
            f"prompt={LONG_ARGUMENT}",
            args_detail=f"prompt={LONG_ARGUMENT}\ncount=2",
            id="long-tool",
        )


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

        assert tool._args_widget.render().plain == f"Arguments:\n{HOSTILE_TEXT}"
        assert tool._output_widget.render().plain == HOSTILE_TEXT
        assert HOSTILE_TEXT in tool.query_one(CollapsibleTitle).render().plain
        assert subagent._result_widget.render().plain == HOSTILE_TEXT
        assert HOSTILE_TEXT in subagent.query_one(CollapsibleTitle).render().plain
        assert compact._body.render().plain == HOSTILE_TEXT
        assert tool.has_class("-done")
        assert subagent.has_class("-done")
        assert compact.has_class("-done")


async def test_tool_block_marks_truncated_titles_and_reveals_full_arguments() -> None:
    app = _ToolArgsHost()

    async with app.run_test(size=(60, 20)) as pilot:
        short_tool = app.query_one("#short-tool", ToolBlock)
        long_tool = app.query_one("#long-tool", ToolBlock)

        short_title = short_tool.query_one(CollapsibleTitle).render().plain
        long_title = long_tool.query_one(CollapsibleTitle).render().plain
        assert "prompt=short" in short_title
        assert "…" not in short_title
        assert "…" in long_title

        long_tool.collapsed = False
        await pilot.pause()

        assert long_tool._args_widget.render().plain == (
            f"Arguments:\nprompt={LONG_ARGUMENT}\ncount=2"
        )
        assert long_tool._args_widget.region.width <= long_tool.region.width
