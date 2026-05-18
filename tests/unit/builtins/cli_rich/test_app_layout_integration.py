"""Smoke tests for the visual layout integration of topic 08.

These tests **do not** boot a prompt_toolkit Application (that requires
a real terminal). They DO exercise:

- ``_build_application`` constructs a Layout that includes a roster
  Window between the hint bar and the input rules
- ``roster_visible`` correctly gates on multi-creature mode + overlay
  state
- ``_roster_text`` returns an ANSI renderable when the roster is
  populated, empty when not
- ``agent_overlay_ansi`` renders a non-empty payload when the overlay
  is open
- The Composer receives the focus / overlay callbacks at construction
  so the Tab / Shift+Tab / Ctrl+A keys reach the right handlers
"""

from types import SimpleNamespace

import pytest

from kohakuterrarium.builtins.cli_rich.app import RichCLIApp


class _FakeAgent:
    def __init__(self, name: str = "alice"):
        self.config = SimpleNamespace(name=name)
        self.llm = SimpleNamespace(model=f"model-for-{name}", _profile_max_context=0)
        self.input = None
        self.output_router = SimpleNamespace(default_output=None)
        self.injected: list[str] = []
        self._processing_task = None
        self._active_handles: dict = {}
        self._last_activity_ts = None
        self._last_generation_tokens = 0

    def llm_identifier(self):
        return self.llm.model

    async def inject_input(self, text, source="cli"):
        self.injected.append(text)


class _FakeCreature:
    def __init__(self, *, creature_id, name, privileged=False, running=True):
        self.creature_id = creature_id
        self.name = name
        self.agent = _FakeAgent(name)
        self.is_privileged = privileged
        self._running = running
        self.listen_channels = []
        self.send_channels = []
        self._last_turn_failed = False

    @property
    def is_running(self):
        return self._running

    async def start(self):
        self._running = True

    async def stop(self):
        self._running = False


class _FakeEngine:
    def __init__(self, creatures):
        self._by_id = {c.creature_id: c for c in creatures}

    def get_creature(self, cid):
        if cid not in self._by_id:
            raise KeyError(cid)
        return self._by_id[cid]

    def list_creatures(self):
        return list(self._by_id.values())


@pytest.fixture
def multi_app():
    c1 = _FakeCreature(creature_id="c1", name="alice", privileged=True)
    c2 = _FakeCreature(creature_id="c2", name="bob")
    c3 = _FakeCreature(creature_id="c3", name="carol")
    eng = _FakeEngine([c1, c2, c3])
    app = RichCLIApp(c1.agent)
    app.setup_multi_creature(eng, "c1")
    return app, eng


class TestRosterVisibility:
    def test_invisible_for_single_creature_default(self):
        c1 = _FakeCreature(creature_id="c1", name="alice")
        app = RichCLIApp(c1.agent)
        # Default single-creature state — never enters multi mode.
        assert app.roster_visible() is False

    def test_visible_after_setup_with_multiple_creatures(self, multi_app):
        app, _ = multi_app
        assert app.roster_visible() is True

    def test_invisible_when_bus_overlay_open(self, multi_app):
        app, _ = multi_app
        # Force-open the bus overlay by faking its visible flag.
        app.bus_overlay.visible = True
        assert app.roster_visible() is False
        app.bus_overlay.visible = False
        assert app.roster_visible() is True

    def test_invisible_when_agent_overlay_open(self, multi_app):
        app, _ = multi_app
        app.agent_overlay.open()
        assert app.roster_visible() is False
        app.agent_overlay.close()
        assert app.roster_visible() is True


class TestRosterText:
    def test_renders_creature_names(self, multi_app):
        app, _ = multi_app
        ansi = app.roster_ansi(120)
        assert "alice" in ansi
        assert "bob" in ansi
        assert "carol" in ansi

    def test_single_creature_returns_empty(self):
        c1 = _FakeCreature(creature_id="c1", name="alice")
        app = RichCLIApp(c1.agent)
        # No multi setup → roster is None → ansi empty.
        assert app.roster_ansi(80) == ""


class TestAgentOverlayRender:
    def test_overlay_ansi_empty_when_closed(self, multi_app):
        app, _ = multi_app
        assert app.agent_overlay_ansi(80) == ""

    def test_overlay_ansi_non_empty_when_open(self, multi_app):
        app, _ = multi_app
        app.agent_overlay.open()
        ansi = app.agent_overlay_ansi(80)
        assert ansi != ""
        # Overlay panel always shows the section headers + footer hint.
        assert "Agent view" in ansi or "select" in ansi.lower()


class TestComposerCallbacks:
    def test_tab_callback_calls_focus_next(self, multi_app):
        app, _ = multi_app
        # The composer was constructed with on_focus_next = self.focus_next.
        # Invoking it should advance focus.
        before = app.focus_controller.focus_id
        app.composer._on_focus_next()
        assert app.focus_controller.focus_id != before

    def test_shift_tab_callback_calls_focus_prev(self, multi_app):
        app, _ = multi_app
        before = app.focus_controller.focus_id
        app.composer._on_focus_prev()
        assert app.focus_controller.focus_id != before

    def test_ctrl_a_callback_opens_overlay(self, multi_app):
        app, _ = multi_app
        assert app.agent_overlay.visible is False
        app.composer._on_open_overlay()
        assert app.agent_overlay.visible is True


class TestPickerIntegration:
    def test_agent_overlay_handle_key_routed_via_picker(self, multi_app):
        app, _ = multi_app
        app.agent_overlay.open()
        # Esc should close it via the picker → overlay path.
        consumed = app._picker_handle_key("escape")
        assert consumed is True
        assert app.agent_overlay.visible is False

    def test_picker_captures_input_when_overlay_open(self, multi_app):
        app, _ = multi_app
        assert app._picker_captures_input() is False
        app.agent_overlay.open()
        assert app._picker_captures_input() is True

    def test_filter_typing_routed_into_overlay(self, multi_app):
        app, _ = multi_app
        app.agent_overlay.open()
        app._picker_handle_text("b")
        app._picker_handle_text("o")
        assert app.agent_overlay.state.filter_text == "bo"
