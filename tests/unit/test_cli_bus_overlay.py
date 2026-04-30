"""Tests for the CLI BusInteractiveOverlay — confirm/ask_text/selection
plus interactive card rendering and key handling.
"""

import pytest

from kohakuterrarium.builtins.cli_rich.dialogs.bus_overlay import (
    BusInteractiveOverlay,
)
from kohakuterrarium.modules.output.event import OutputEvent


class _MockRouter:
    def __init__(self):
        self.replies: list = []

    def submit_reply(self, reply):
        self.replies.append(reply)
        return True


def _new_overlay(router=None):
    router = router or _MockRouter()
    return router, BusInteractiveOverlay(get_router=lambda: router)


def test_card_with_actions_opens_overlay_and_renders_title_body_actions():
    router, overlay = _new_overlay()
    overlay.open(
        OutputEvent(
            type="card",
            interactive=True,
            id="c1",
            payload={
                "title": "Plan ready",
                "subtitle": "5 files",
                "accent": "warning",
                "body": "Migrate auth to new module.",
                "fields": [{"label": "Files", "value": "5"}],
                "actions": [
                    {"id": "approve", "label": "Approve", "style": "primary"},
                    {"id": "reject", "label": "Reject", "style": "danger"},
                ],
            },
        )
    )
    assert overlay.visible
    assert overlay._current.type == "card"

    output = overlay.render(80)
    assert "Plan ready" in output
    assert "Migrate auth" in output
    assert "Approve" in output
    assert "Reject" in output
    assert "Files" in output


def test_card_navigation_via_arrow_keys_and_enter_submits_action():
    router, overlay = _new_overlay()
    overlay.open(
        OutputEvent(
            type="card",
            interactive=True,
            id="c2",
            payload={
                "title": "x",
                "actions": [
                    {"id": "yes", "label": "Yes"},
                    {"id": "no", "label": "No"},
                ],
            },
        )
    )
    assert overlay.handle_key("down") is True
    assert overlay._option_index == 1
    assert overlay.handle_key("enter") is True
    assert len(router.replies) == 1
    assert router.replies[0].action_id == "no"
    assert overlay.visible is False


def test_card_digit_shortcut_picks_action_by_index():
    router, overlay = _new_overlay()
    overlay.open(
        OutputEvent(
            type="card",
            interactive=True,
            id="c3",
            payload={
                "title": "x",
                "actions": [
                    {"id": "a", "label": "A"},
                    {"id": "b", "label": "B"},
                    {"id": "c", "label": "C"},
                ],
            },
        )
    )
    assert overlay.handle_key("3") is True
    assert router.replies[0].action_id == "c"


def test_card_escape_submits_cancel():
    router, overlay = _new_overlay()
    overlay.open(
        OutputEvent(
            type="card",
            interactive=True,
            id="c4",
            payload={"title": "x", "actions": [{"id": "ok", "label": "OK"}]},
        )
    )
    assert overlay.handle_key("escape") is True
    assert len(router.replies) == 1
    assert router.replies[0].action_id == "cancel"


def test_card_link_actions_are_filtered_from_navigation_but_still_rendered():
    router, overlay = _new_overlay()
    overlay.open(
        OutputEvent(
            type="card",
            interactive=True,
            id="c5",
            payload={
                "title": "Docs",
                "actions": [
                    {"id": "approve", "label": "OK", "style": "primary"},
                    {
                        "id": "doc",
                        "label": "Open docs",
                        "style": "link",
                        "url": "https://example.com",
                    },
                ],
            },
        )
    )
    options = overlay._options()
    assert len(options) == 1, f"link should be filtered; got {options}"
    assert options[0]["id"] == "approve"

    output = overlay.render(80)
    assert "Open docs" in output, "link label should still display"
    assert "https://example.com" in output, "link URL should display"


@pytest.mark.parametrize(
    "accent,expected_in_render",
    [
        ("primary", "Plan"),
        ("info", "Plan"),
        ("success", "Plan"),
        ("warning", "Plan"),
        ("danger", "Plan"),
        ("neutral", "Plan"),
    ],
)
def test_card_renders_with_each_accent(accent, expected_in_render):
    router, overlay = _new_overlay()
    overlay.open(
        OutputEvent(
            type="card",
            interactive=True,
            id=f"c-{accent}",
            payload={
                "title": "Plan",
                "accent": accent,
                "actions": [{"id": "ok", "label": "OK"}],
            },
        )
    )
    output = overlay.render(80)
    assert expected_in_render in output


def test_card_without_actions_does_not_open_overlay_via_options():
    """Display-only cards (no actions / link-only actions) shouldn't
    have any selectable options."""
    router, overlay = _new_overlay()
    overlay.open(
        OutputEvent(
            type="card",
            interactive=False,
            id="c6",
            payload={"title": "Done", "body": "all good"},
        )
    )
    assert overlay._options() == []
