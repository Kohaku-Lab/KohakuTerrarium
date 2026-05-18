"""Tests for :mod:`builtins.cli_rich.dialogs.agent_overlay`."""

from io import StringIO

from rich.console import Console

from kohakuterrarium.builtins.cli_rich.creature_status import CreatureStatus
from kohakuterrarium.builtins.cli_rich.dialogs.agent_overlay import (
    AgentOverlay,
    AgentOverlayState,
    handle_key,
    render_overlay,
)


def _mk(cid: str, name: str, state: str, activity: str = "") -> CreatureStatus:
    return CreatureStatus(
        creature_id=cid, name=name, state=state, activity=activity or state  # type: ignore[arg-type]
    )


_FIXTURE = [
    _mk("c1", "alpha", "working", "running tests"),
    _mk("c2", "beta", "idle", "idle 2m"),
    _mk("c3", "gamma", "waiting", "needs: confirm"),
    _mk("c4", "delta", "stopped"),
]


class TestVisibleGrouping:
    def test_visible_groups_in_priority_order(self):
        s = AgentOverlayState(statuses=_FIXTURE)
        visible = s.visible()
        # Priority: waiting → working → idle → stopped → failed
        assert [c.creature_id for c in visible] == ["c3", "c1", "c2", "c4"]

    def test_filter_narrows_by_name(self):
        s = AgentOverlayState(statuses=_FIXTURE, filter_text="beta")
        assert [c.creature_id for c in s.visible()] == ["c2"]

    def test_filter_narrows_by_activity(self):
        s = AgentOverlayState(statuses=_FIXTURE, filter_text="confirm")
        assert [c.creature_id for c in s.visible()] == ["c3"]

    def test_filter_is_case_insensitive(self):
        s = AgentOverlayState(statuses=_FIXTURE, filter_text="ALPHA")
        assert [c.creature_id for c in s.visible()] == ["c1"]


class TestSelectionCycling:
    def test_initial_selection_falls_to_first_visible(self):
        s = AgentOverlayState(statuses=_FIXTURE)
        s.ensure_valid_selection()
        # First visible (priority order) is c3 (waiting).
        assert s.selected_id == "c3"

    def test_select_next_wraps(self):
        s = AgentOverlayState(statuses=_FIXTURE)
        s.ensure_valid_selection()
        ids = []
        for _ in range(5):
            s.select_next()
            ids.append(s.selected_id)
        # 4 unique creatures → after 4 nexts we should have cycled
        # back; 5th matches the 1st.
        assert ids[4] == ids[0]

    def test_select_prev_wraps(self):
        s = AgentOverlayState(statuses=_FIXTURE)
        s.ensure_valid_selection()
        s.select_prev()
        # From c3 backwards → last visible.
        assert s.selected_id == "c4"


class TestPeek:
    def test_toggle_peek_opens_then_closes(self):
        s = AgentOverlayState(statuses=_FIXTURE, selected_id="c1")
        s.toggle_peek()
        assert s.peek_id == "c1"
        s.toggle_peek()
        assert s.peek_id is None


class TestKeyHandling:
    def test_escape_closes(self):
        s = AgentOverlayState(statuses=_FIXTURE)
        r = handle_key(s, "escape")
        assert r.action == "close"

    def test_arrow_keys_move_selection(self):
        s = AgentOverlayState(statuses=_FIXTURE)
        s.ensure_valid_selection()
        first = s.selected_id
        r = handle_key(s, "down")
        assert r.action == "consumed"
        assert s.selected_id != first
        handle_key(s, "up")
        assert s.selected_id == first

    def test_enter_returns_focus_action(self):
        s = AgentOverlayState(statuses=_FIXTURE, selected_id="c2")
        r = handle_key(s, "enter")
        assert r.action == "focus"
        assert r.creature_id == "c2"

    def test_right_promotes_peek_to_focus(self):
        s = AgentOverlayState(statuses=_FIXTURE, selected_id="c1", peek_id="c1")
        r = handle_key(s, "right")
        assert r.action == "focus"
        assert r.creature_id == "c1"

    def test_unknown_key_passthrough(self):
        s = AgentOverlayState(statuses=_FIXTURE)
        r = handle_key(s, "f12")
        assert r.action == "passthrough"


class TestRender:
    def test_render_includes_group_labels(self):
        s = AgentOverlayState(statuses=_FIXTURE)
        s.ensure_valid_selection()
        buf = StringIO()
        Console(file=buf, color_system=None, width=80).print(render_overlay(s))
        out = buf.getvalue()
        assert "Needs input" in out
        assert "Working" in out
        assert "Idle" in out
        assert "Stopped" in out
        assert "alpha" in out and "beta" in out

    def test_empty_visible_shows_message(self):
        s = AgentOverlayState(statuses=_FIXTURE, filter_text="nothingmatches")
        buf = StringIO()
        Console(file=buf, color_system=None, width=80).print(render_overlay(s))
        assert "No creatures match" in buf.getvalue()


class TestAgentOverlayLifecycle:
    def test_open_close_toggles_visible(self):
        ov = AgentOverlay(get_statuses=lambda: list(_FIXTURE))
        assert ov.visible is False
        ov.open()
        assert ov.visible is True
        ov.close()
        assert ov.visible is False

    def test_handle_key_when_closed_returns_false(self):
        ov = AgentOverlay(get_statuses=lambda: list(_FIXTURE))
        assert ov.handle_key("down") is False

    def test_focus_callback_fires_on_enter(self):
        focused = []
        ov = AgentOverlay(
            get_statuses=lambda: list(_FIXTURE),
            on_focus=lambda cid: focused.append(cid),
        )
        ov.open()
        ov.state.selected_id = "c2"
        ov.handle_key("enter")
        assert focused == ["c2"]
        assert ov.visible is False  # focus closes overlay

    def test_filter_handling_via_typing(self):
        ov = AgentOverlay(get_statuses=lambda: list(_FIXTURE))
        ov.open()
        ov.handle_text("be")
        ov.handle_text("ta")
        assert ov.state.filter_text == "beta"
        ov.backspace()
        assert ov.state.filter_text == "bet"
