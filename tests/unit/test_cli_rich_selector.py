"""Tests for the rich CLI SelectorOverlay — the arrow-key picker that
replaces ``/model``'s numbered-list output (issue #27).

The overlay lives inside the single RichCLIApp Application, so these
tests drive it via its public async API plus direct key-handler
invocation. We never spin up a real prompt_toolkit Application —
``show_select`` / ``show_confirm`` accept ``app=None`` for exactly this
reason.
"""

import asyncio
from typing import Callable
from unittest.mock import MagicMock

import pytest
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.keys import KEY_ALIASES, Keys

from kohakuterrarium.builtins.cli_rich.selector import SelectorOverlay

# Prompt_toolkit rewrites ``@kb.add("enter")`` → Keys.ControlM (value "c-m")
# via KEY_ALIASES and then resolves the string to the Keys enum member.
# Mirror that resolution so tests can address bindings by friendly name.
_KEY_VALUES = {k.value: k for k in Keys}


def _normalize_key(key: str):
    resolved = KEY_ALIASES.get(key, key)
    return _KEY_VALUES.get(resolved, resolved)


def _handler_for(kb: KeyBindings, *keys: str) -> Callable:
    """Find the handler registered for the given key sequence."""
    target = tuple(_normalize_key(k) for k in keys)
    for binding in kb.bindings:
        if binding.keys == target:
            return binding.handler
    raise KeyError(f"no binding for {target!r}")


def _event() -> MagicMock:
    """Minimal stand-in for prompt_toolkit's KeyPressEvent."""
    evt = MagicMock()
    evt.app = MagicMock()
    return evt


# ── Initial state ──────────────────────────────────────────────────────


class TestInitialState:
    def test_not_visible_at_construction(self):
        overlay = SelectorOverlay()
        assert overlay.visible is False
        assert overlay.is_select is False
        assert overlay.is_confirm is False

    def test_build_floats_returns_two(self):
        overlay = SelectorOverlay()
        floats = overlay.build_floats()
        assert len(floats) == 2

    def test_key_bindings_exposed(self):
        overlay = SelectorOverlay()
        assert isinstance(overlay.key_bindings, KeyBindings)


# ── Initial highlight ──────────────────────────────────────────────────


class TestInitialHighlight:
    def test_defaults_to_zero(self):
        opts = [{"value": "a"}, {"value": "b"}, {"value": "c"}]
        assert SelectorOverlay._initial_highlight(opts, "") == 0

    def test_prefers_selected_flag(self):
        opts = [
            {"value": "a"},
            {"value": "b", "selected": True},
            {"value": "c"},
        ]
        assert SelectorOverlay._initial_highlight(opts, "") == 1

    def test_falls_back_to_current_value(self):
        opts = [{"value": "a"}, {"value": "b"}, {"value": "c"}]
        assert SelectorOverlay._initial_highlight(opts, "c") == 2

    def test_falls_back_to_current_model_field(self):
        opts = [
            {"value": "opus-4-7", "model": "claude-opus-4-7"},
            {"value": "sonnet-4-6", "model": "claude-sonnet-4-6"},
        ]
        assert SelectorOverlay._initial_highlight(opts, "claude-sonnet-4-6") == 1

    def test_selected_flag_wins_over_current(self):
        opts = [
            {"value": "a"},
            {"value": "b", "selected": True},
            {"value": "c"},
        ]
        # Even if current matches a different option, selected takes priority.
        assert SelectorOverlay._initial_highlight(opts, "c") == 1


# ── show_select: happy paths ───────────────────────────────────────────


class TestShowSelect:
    async def test_empty_options_returns_none_without_opening(self):
        overlay = SelectorOverlay()
        result = await overlay.show_select("Pick", [], "", app=None)
        assert result is None
        assert overlay.visible is False

    async def test_enter_resolves_highlighted_value(self):
        overlay = SelectorOverlay()
        opts = [
            {"value": "opus-4-7", "label": "opus-4-7"},
            {"value": "sonnet-4-6", "label": "sonnet-4-6", "selected": True},
            {"value": "haiku-4-5", "label": "haiku-4-5"},
        ]
        task = asyncio.create_task(
            overlay.show_select("Switch Model", opts, "", app=None)
        )
        # Yield so the coroutine actually starts and sets state.
        await asyncio.sleep(0)
        assert overlay.is_select is True
        assert overlay._highlight == 1  # preselected via "selected"

        _handler_for(overlay.key_bindings, "enter")(_event())
        result = await task
        assert result == "sonnet-4-6"
        assert overlay.visible is False
        assert overlay.is_select is False

    async def test_down_then_enter_picks_next_option(self):
        overlay = SelectorOverlay()
        opts = [{"value": "a"}, {"value": "b"}, {"value": "c"}]
        task = asyncio.create_task(overlay.show_select("Pick", opts, "", app=None))
        await asyncio.sleep(0)

        _handler_for(overlay.key_bindings, "down")(_event())
        _handler_for(overlay.key_bindings, "enter")(_event())
        assert await task == "b"

    async def test_down_wraps_around(self):
        overlay = SelectorOverlay()
        opts = [{"value": "a"}, {"value": "b"}]
        task = asyncio.create_task(overlay.show_select("Pick", opts, "", app=None))
        await asyncio.sleep(0)

        # a → b → a (wrap)
        _handler_for(overlay.key_bindings, "down")(_event())
        _handler_for(overlay.key_bindings, "down")(_event())
        assert overlay._highlight == 0

        _handler_for(overlay.key_bindings, "enter")(_event())
        assert await task == "a"

    async def test_up_wraps_around(self):
        overlay = SelectorOverlay()
        opts = [{"value": "a"}, {"value": "b"}, {"value": "c"}]
        task = asyncio.create_task(overlay.show_select("Pick", opts, "", app=None))
        await asyncio.sleep(0)

        _handler_for(overlay.key_bindings, "up")(_event())
        assert overlay._highlight == 2

        _handler_for(overlay.key_bindings, "enter")(_event())
        assert await task == "c"

    async def test_home_and_end_jump(self):
        overlay = SelectorOverlay()
        opts = [{"value": "a"}, {"value": "b"}, {"value": "c"}, {"value": "d"}]
        task = asyncio.create_task(overlay.show_select("Pick", opts, "b", app=None))
        await asyncio.sleep(0)

        assert overlay._highlight == 1
        _handler_for(overlay.key_bindings, "end")(_event())
        assert overlay._highlight == 3
        _handler_for(overlay.key_bindings, "home")(_event())
        assert overlay._highlight == 0

        _handler_for(overlay.key_bindings, "enter")(_event())
        assert await task == "a"

    async def test_ctrl_n_and_ctrl_p_navigate(self):
        overlay = SelectorOverlay()
        opts = [{"value": "a"}, {"value": "b"}, {"value": "c"}]
        task = asyncio.create_task(overlay.show_select("Pick", opts, "", app=None))
        await asyncio.sleep(0)

        _handler_for(overlay.key_bindings, "c-n")(_event())
        _handler_for(overlay.key_bindings, "c-n")(_event())
        _handler_for(overlay.key_bindings, "c-p")(_event())
        assert overlay._highlight == 1

        _handler_for(overlay.key_bindings, "enter")(_event())
        assert await task == "b"


# ── show_select: cancel paths ──────────────────────────────────────────


class TestShowSelectCancel:
    async def test_escape_resolves_none(self):
        overlay = SelectorOverlay()
        opts = [{"value": "a"}, {"value": "b"}]
        task = asyncio.create_task(overlay.show_select("Pick", opts, "", app=None))
        await asyncio.sleep(0)

        _handler_for(overlay.key_bindings, "escape")(_event())
        assert await task is None
        assert overlay.visible is False

    async def test_ctrl_c_resolves_none(self):
        overlay = SelectorOverlay()
        opts = [{"value": "a"}, {"value": "b"}]
        task = asyncio.create_task(overlay.show_select("Pick", opts, "", app=None))
        await asyncio.sleep(0)

        _handler_for(overlay.key_bindings, "c-c")(_event())
        assert await task is None


# ── show_confirm ───────────────────────────────────────────────────────


class TestShowConfirm:
    async def test_y_resolves_true(self):
        overlay = SelectorOverlay()
        task = asyncio.create_task(overlay.show_confirm("Sure?", app=None))
        await asyncio.sleep(0)
        assert overlay.is_confirm is True
        assert overlay.is_select is False

        _handler_for(overlay.key_bindings, "y")(_event())
        assert await task is True
        assert overlay.visible is False

    async def test_capital_y_resolves_true(self):
        overlay = SelectorOverlay()
        task = asyncio.create_task(overlay.show_confirm("Sure?", app=None))
        await asyncio.sleep(0)

        _handler_for(overlay.key_bindings, "Y")(_event())
        assert await task is True

    async def test_n_resolves_false(self):
        overlay = SelectorOverlay()
        task = asyncio.create_task(overlay.show_confirm("Sure?", app=None))
        await asyncio.sleep(0)

        _handler_for(overlay.key_bindings, "n")(_event())
        assert await task is False

    async def test_escape_resolves_false(self):
        overlay = SelectorOverlay()
        task = asyncio.create_task(overlay.show_confirm("Sure?", app=None))
        await asyncio.sleep(0)

        _handler_for(overlay.key_bindings, "escape")(_event())
        assert await task is False


# ── Guard: inactive-mode keys are no-ops ───────────────────────────────


class TestInactiveModeGuards:
    async def test_y_does_nothing_in_select_mode(self):
        overlay = SelectorOverlay()
        opts = [{"value": "a"}, {"value": "b"}]
        task = asyncio.create_task(overlay.show_select("Pick", opts, "", app=None))
        await asyncio.sleep(0)

        # Pressing 'y' while in select mode must NOT resolve the future.
        _handler_for(overlay.key_bindings, "y")(_event())
        await asyncio.sleep(0)
        assert not task.done()

        _handler_for(overlay.key_bindings, "escape")(_event())
        assert await task is None

    async def test_up_does_nothing_in_confirm_mode(self):
        overlay = SelectorOverlay()
        task = asyncio.create_task(overlay.show_confirm("Sure?", app=None))
        await asyncio.sleep(0)

        _handler_for(overlay.key_bindings, "up")(_event())
        # Highlight state is irrelevant to confirm; just ensure no crash
        # and no resolution.
        await asyncio.sleep(0)
        assert not task.done()

        _handler_for(overlay.key_bindings, "n")(_event())
        assert await task is False


# ── Concurrent show refused ────────────────────────────────────────────


class TestConcurrentShow:
    async def test_concurrent_select_returns_none(self):
        overlay = SelectorOverlay()
        opts = [{"value": "a"}, {"value": "b"}]
        first = asyncio.create_task(overlay.show_select("Pick", opts, "", app=None))
        await asyncio.sleep(0)

        # Second call while first is in-flight must bail out without
        # stomping on the active future.
        second = await overlay.show_select("Pick", opts, "", app=None)
        assert second is None
        assert overlay.visible is True  # first is still up

        _handler_for(overlay.key_bindings, "enter")(_event())
        assert await first == "a"

    async def test_concurrent_confirm_returns_false(self):
        overlay = SelectorOverlay()
        first = asyncio.create_task(overlay.show_confirm("Sure?", app=None))
        await asyncio.sleep(0)

        second = await overlay.show_confirm("Also sure?", app=None)
        assert second is False  # refused, not an acceptance

        _handler_for(overlay.key_bindings, "y")(_event())
        assert await first is True


# ── Rendering sanity ───────────────────────────────────────────────────


class TestRendering:
    async def test_select_renders_options_and_highlight(self):
        overlay = SelectorOverlay()
        opts = [
            {"value": "a", "label": "opus", "provider": "anthropic"},
            {"value": "b", "label": "sonnet", "provider": "anthropic"},
        ]
        task = asyncio.create_task(
            overlay.show_select("Switch Model", opts, "", app=None)
        )
        await asyncio.sleep(0)

        fragments = overlay._render_select()
        # Concatenate all text; verify title, labels, and hint are present.
        joined = "".join(text for _, text in fragments)
        assert "Switch Model" in joined
        assert "opus" in joined
        assert "sonnet" in joined
        assert "↑↓" in joined  # navigation hint

        # The highlighted (first) row uses the highlight style.
        highlight_styles = {style for style, text in fragments if "opus" in text}
        assert any("selector.row.highlight" in s for s in highlight_styles)

        _handler_for(overlay.key_bindings, "escape")(_event())
        await task

    async def test_confirm_renders_message(self):
        overlay = SelectorOverlay()
        task = asyncio.create_task(overlay.show_confirm("Delete everything?", app=None))
        await asyncio.sleep(0)

        fragments = overlay._render_confirm()
        joined = "".join(text for _, text in fragments)
        assert "Confirm" in joined
        assert "Delete everything?" in joined
        assert "y confirm" in joined

        _handler_for(overlay.key_bindings, "escape")(_event())
        await task


# ── State cleanup ──────────────────────────────────────────────────────


class TestStateReset:
    async def test_state_cleared_after_select(self):
        overlay = SelectorOverlay()
        opts = [{"value": "a"}, {"value": "b"}]
        task = asyncio.create_task(overlay.show_select("Pick", opts, "", app=None))
        await asyncio.sleep(0)
        _handler_for(overlay.key_bindings, "enter")(_event())
        await task

        assert overlay._mode is None
        assert overlay._options == []
        assert overlay._highlight == 0
        assert overlay._future is None

    async def test_state_cleared_after_cancel(self):
        overlay = SelectorOverlay()
        task = asyncio.create_task(overlay.show_confirm("Sure?", app=None))
        await asyncio.sleep(0)
        _handler_for(overlay.key_bindings, "escape")(_event())
        await task

        assert overlay._mode is None
        assert overlay._message == ""
        assert overlay._future is None

    async def test_second_select_after_first_completes(self):
        overlay = SelectorOverlay()
        opts = [{"value": "a"}, {"value": "b"}]

        # First select.
        first = asyncio.create_task(overlay.show_select("Pick", opts, "", app=None))
        await asyncio.sleep(0)
        _handler_for(overlay.key_bindings, "enter")(_event())
        assert await first == "a"

        # Second select works with fresh state.
        second = asyncio.create_task(
            overlay.show_select("Pick again", opts, "b", app=None)
        )
        await asyncio.sleep(0)
        assert overlay._highlight == 1  # preselected via current="b"
        _handler_for(overlay.key_bindings, "enter")(_event())
        assert await second == "b"


# ── Fixture/plumbing ───────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _fast_event_loop():
    """Ensure each test gets a clean loop via pytest-asyncio default."""
    yield
