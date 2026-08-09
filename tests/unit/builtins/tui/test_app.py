"""Tests for the Textual application shell."""

import pytest
from textual.containers import VerticalScroll
from textual.widgets import Static, TabbedContent, TabPane
from textual.widgets._tabbed_content import ContentTab, ContentTabs

from kohakuterrarium.builtins.tui.app import (
    AgentTUI,
    _id_to_name,
    _safe_id,
)


def test_tab_ids_round_trip_without_collisions() -> None:
    names = [
        "review-worker",
        "review_worker",
        "#task-channel",
        "#task_channel",
        "中文-creature",
    ]

    encoded = [_safe_id(name) for name in names]

    assert len(set(encoded)) == len(names)
    assert [_id_to_name(value) for value in encoded] == names


@pytest.mark.asyncio
async def test_reconcile_terrarium_tabs_preserves_retained_panes_and_active_tab() -> (
    None
):
    app = AgentTUI(terrarium_tabs=["root", "review-worker"])

    async with app.run_test() as pilot:
        await pilot.pause()
        tabs = app.query_one("#chat-tabs", TabbedContent)
        tabs.active = f"tab-{_safe_id('review-worker')}"
        retained_chat = app.query_one(
            f"#chat-{_safe_id('review-worker')}", VerticalScroll
        )
        retained_marker = Static("keep me")
        await retained_chat.mount(retained_marker)

        await app.reconcile_terrarium_tabs(
            ["root", "new_worker", "review-worker", "#task-channel"]
        )
        await pilot.pause()

        expected_ids = [
            f"tab-{_safe_id('root')}",
            f"tab-{_safe_id('new_worker')}",
            f"tab-{_safe_id('review-worker')}",
            f"tab-{_safe_id('#task-channel')}",
        ]
        assert {pane.id for pane in tabs.query(TabPane)} == set(expected_ids)
        content_tabs = tabs.get_child_by_type(ContentTabs)
        assert [
            tab.id.removeprefix(ContentTab._PREFIX)
            for tab in content_tabs.query(ContentTab)
            if tab.id
        ] == expected_ids
        assert app.get_active_tab_name() == "review-worker"
        assert retained_marker in retained_chat.children

        await app.reconcile_terrarium_tabs(["root", "new_worker"])
        await pilot.pause()

        assert {pane.id for pane in tabs.query(TabPane)} == {
            f"tab-{_safe_id('root')}",
            f"tab-{_safe_id('new_worker')}",
        }
        assert app.get_active_tab_name() == "root"
