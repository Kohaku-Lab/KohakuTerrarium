"""Tests for :mod:`builtins.cli_rich.dialogs.peek_panel`."""

from io import StringIO
from types import SimpleNamespace

from rich.console import Console

from kohakuterrarium.builtins.cli_rich.dialogs.peek_panel import (
    PeekPanel,
    render_peek,
)
from kohakuterrarium.builtins.cli_rich.live_state import LiveRegionState


def _render(state, name=""):
    buf = StringIO()
    Console(file=buf, color_system=None, width=80).print(
        render_peek(state, creature_name=name)
    )
    return buf.getvalue()


class TestRenderEmptyState:
    def test_none_state_shows_placeholder(self):
        out = _render(None, name="alpha")
        assert "No state" in out
        assert "alpha" in out

    def test_empty_recent_events(self):
        st = LiveRegionState(creature_id="c1")
        out = _render(st, name="alpha")
        assert "no recent activity" in out


class TestRenderWithEvents:
    def test_text_event_renders(self):
        st = LiveRegionState(creature_id="c1")
        st.record_event("hello world")
        out = _render(st)
        assert "hello world" in out

    def test_object_event_with_payload(self):
        st = LiveRegionState(creature_id="c1")
        ev = SimpleNamespace(type="tool", payload={"name": "bash"})
        st.record_event(ev)
        out = _render(st)
        assert "[tool]" in out
        assert "bash" in out

    def test_text_buffer_visible(self):
        st = LiveRegionState(creature_id="c1")
        st.append_text("partial response here")
        out = _render(st)
        assert "partial response" in out


class TestPeekPanelWrapper:
    def test_returns_none_when_creature_id_falsy(self):
        p = PeekPanel(get_state=lambda cid: None)
        assert p.render("") is None
        assert p.render(None) is None

    def test_uses_get_name_callback(self):
        states = {"c1": LiveRegionState(creature_id="c1")}
        states["c1"].record_event("hi")
        p = PeekPanel(
            get_state=lambda cid: states.get(cid),
            get_name=lambda cid: {"c1": "alice"}.get(cid, ""),
        )
        renderable = p.render("c1")
        assert renderable is not None
        buf = StringIO()
        Console(file=buf, color_system=None, width=80).print(renderable)
        out = buf.getvalue()
        assert "alice" in out
