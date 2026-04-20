"""Tests for the rich CLI SelectorOverlay — the arrow-key picker that
replaces ``/model``'s numbered-list output (issue #27).

The overlay lives inside the single RichCLIApp Application, so these
tests drive it via its public async API plus a filter-aware key-press
simulator. We never spin up a real prompt_toolkit Application —
``show_select`` / ``show_confirm`` accept ``app=None`` for exactly this
reason.
"""

import asyncio
from unittest.mock import MagicMock

import pytest
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.keys import KEY_ALIASES, Keys

from kohakuterrarium.builtins.cli_rich.selector import SelectorOverlay, _fit

# Prompt_toolkit rewrites ``@kb.add("enter")`` → Keys.ControlM (value "c-m")
# via KEY_ALIASES and then resolves the string to the Keys enum member.
# Mirror that resolution so tests can address bindings by friendly name.
_KEY_VALUES = {k.value: k for k in Keys}


def _normalize_key(key: str):
    resolved = KEY_ALIASES.get(key, key)
    return _KEY_VALUES.get(resolved, resolved)


def _event(data: str = "") -> MagicMock:
    """Minimal stand-in for prompt_toolkit's KeyPressEvent."""
    evt = MagicMock()
    evt.app = MagicMock()
    evt.data = data
    return evt


def _press(overlay: SelectorOverlay, *keys: str, data: str = "") -> bool:
    """Simulate a key press that respects binding filters.

    Prompt_toolkit's real dispatch evaluates filters before invoking
    handlers and falls back to ``Keys.Any`` when no specific binding
    fires. This helper mirrors that so tests exercise the same paths
    the user would hit.

    Returns True if a handler fired.
    """
    target = tuple(_normalize_key(k) for k in keys)
    any_binding = None
    for b in overlay.key_bindings.bindings:
        if b.keys == (Keys.Any,):
            any_binding = b
            continue
        if b.keys != target:
            continue
        if not b.filter():
            continue
        evt_data = data or (keys[0] if len(keys) == 1 and len(keys[0]) == 1 else "")
        b.handler(_event(evt_data))
        return True
    if any_binding is not None and any_binding.filter():
        evt_data = data or (keys[0] if len(keys) == 1 and len(keys[0]) == 1 else "")
        any_binding.handler(_event(evt_data))
        return True
    return False


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


# ── _fit helper ────────────────────────────────────────────────────────


class TestFit:
    def test_pads_short_text(self):
        assert _fit("opus", 8) == "opus    "

    def test_truncates_with_ellipsis(self):
        assert _fit("opus-4-7-mega-long", 10) == "opus-4-7-…"

    def test_exact_width_unchanged(self):
        assert _fit("abcdef", 6) == "abcdef"

    def test_right_align(self):
        assert _fit("12", 6, "right") == "    12"


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
        await asyncio.sleep(0)
        assert overlay.is_select is True
        assert overlay._highlight == 1  # preselected via "selected"

        _press(overlay, "enter")
        assert await task == "sonnet-4-6"

    async def test_down_then_enter_picks_next_option(self):
        overlay = SelectorOverlay()
        opts = [{"value": "a"}, {"value": "b"}, {"value": "c"}]
        task = asyncio.create_task(overlay.show_select("Pick", opts, "", app=None))
        await asyncio.sleep(0)

        _press(overlay, "down")
        _press(overlay, "enter")
        assert await task == "b"

    async def test_navigation_wraps(self):
        overlay = SelectorOverlay()
        opts = [{"value": "a"}, {"value": "b"}]
        task = asyncio.create_task(overlay.show_select("Pick", opts, "", app=None))
        await asyncio.sleep(0)

        _press(overlay, "up")
        assert overlay._highlight == 1  # wrap from 0 to last

        _press(overlay, "enter")
        assert await task == "b"

    async def test_home_and_end_jump(self):
        overlay = SelectorOverlay()
        opts = [{"value": "a"}, {"value": "b"}, {"value": "c"}, {"value": "d"}]
        task = asyncio.create_task(overlay.show_select("Pick", opts, "b", app=None))
        await asyncio.sleep(0)

        assert overlay._highlight == 1
        _press(overlay, "end")
        assert overlay._highlight == 3
        _press(overlay, "home")
        assert overlay._highlight == 0

        _press(overlay, "enter")
        assert await task == "a"

    async def test_ctrl_navigation(self):
        overlay = SelectorOverlay()
        opts = [{"value": "a"}, {"value": "b"}, {"value": "c"}]
        task = asyncio.create_task(overlay.show_select("Pick", opts, "", app=None))
        await asyncio.sleep(0)

        _press(overlay, "c-n")
        _press(overlay, "c-n")
        _press(overlay, "c-p")
        assert overlay._highlight == 1

        _press(overlay, "enter")
        assert await task == "b"


# ── show_select: cancel paths ──────────────────────────────────────────


class TestShowSelectCancel:
    async def test_escape_resolves_none(self):
        overlay = SelectorOverlay()
        opts = [{"value": "a"}, {"value": "b"}]
        task = asyncio.create_task(overlay.show_select("Pick", opts, "", app=None))
        await asyncio.sleep(0)

        _press(overlay, "escape")
        assert await task is None
        assert overlay.visible is False

    async def test_ctrl_c_resolves_none(self):
        overlay = SelectorOverlay()
        opts = [{"value": "a"}, {"value": "b"}]
        task = asyncio.create_task(overlay.show_select("Pick", opts, "", app=None))
        await asyncio.sleep(0)

        _press(overlay, "c-c")
        assert await task is None


# ── Type-to-filter search ──────────────────────────────────────────────


class TestFilterSearch:
    @staticmethod
    def _model_options():
        return [
            {
                "value": "opus-4-7",
                "label": "opus-4-7",
                "model": "claude-opus-4-7",
                "provider": "anthropic",
            },
            {
                "value": "sonnet-4-6",
                "label": "sonnet-4-6",
                "model": "claude-sonnet-4-6",
                "provider": "anthropic",
            },
            {
                "value": "gpt-5",
                "label": "gpt-5",
                "model": "gpt-5-turbo",
                "provider": "openai",
            },
        ]

    async def test_typing_filters_by_label_substring(self):
        overlay = SelectorOverlay()
        opts = self._model_options()
        task = asyncio.create_task(overlay.show_select("Pick", opts, "", app=None))
        await asyncio.sleep(0)

        _press(overlay, "o")
        _press(overlay, "p")
        _press(overlay, "u")
        assert overlay._query == "opu"
        assert [o["value"] for o in overlay._filtered] == ["opus-4-7"]

        _press(overlay, "enter")
        assert await task == "opus-4-7"

    async def test_filter_is_case_insensitive(self):
        overlay = SelectorOverlay()
        opts = self._model_options()
        task = asyncio.create_task(overlay.show_select("Pick", opts, "", app=None))
        await asyncio.sleep(0)

        _press(overlay, "S")
        _press(overlay, "O")
        assert [o["value"] for o in overlay._filtered] == ["sonnet-4-6"]

        _press(overlay, "escape")
        await task

    async def test_filter_matches_model_field(self):
        overlay = SelectorOverlay()
        opts = self._model_options()
        task = asyncio.create_task(overlay.show_select("Pick", opts, "", app=None))
        await asyncio.sleep(0)

        # "turbo" only appears in the model field of gpt-5.
        for ch in "turbo":
            _press(overlay, ch)
        assert [o["value"] for o in overlay._filtered] == ["gpt-5"]

        _press(overlay, "escape")
        await task

    async def test_filter_matches_provider_field(self):
        overlay = SelectorOverlay()
        opts = self._model_options()
        task = asyncio.create_task(overlay.show_select("Pick", opts, "", app=None))
        await asyncio.sleep(0)

        for ch in "openai":
            _press(overlay, ch)
        assert [o["value"] for o in overlay._filtered] == ["gpt-5"]

        _press(overlay, "escape")
        await task

    async def test_backspace_removes_last_char(self):
        overlay = SelectorOverlay()
        opts = self._model_options()
        task = asyncio.create_task(overlay.show_select("Pick", opts, "", app=None))
        await asyncio.sleep(0)

        for ch in "opus":
            _press(overlay, ch)
        assert overlay._query == "opus"

        _press(overlay, "backspace")
        assert overlay._query == "opu"
        _press(overlay, "backspace")
        assert overlay._query == "op"

        _press(overlay, "escape")
        await task

    async def test_backspace_on_empty_query_is_noop(self):
        overlay = SelectorOverlay()
        opts = self._model_options()
        task = asyncio.create_task(overlay.show_select("Pick", opts, "", app=None))
        await asyncio.sleep(0)

        assert overlay._query == ""
        _press(overlay, "backspace")
        assert overlay._query == ""
        assert len(overlay._filtered) == 3

        _press(overlay, "escape")
        await task

    async def test_ctrl_u_clears_query(self):
        overlay = SelectorOverlay()
        opts = self._model_options()
        task = asyncio.create_task(overlay.show_select("Pick", opts, "", app=None))
        await asyncio.sleep(0)

        for ch in "opus":
            _press(overlay, ch)
        assert overlay._query == "opus"

        _press(overlay, "c-u")
        assert overlay._query == ""
        assert len(overlay._filtered) == 3

        _press(overlay, "escape")
        await task

    async def test_highlight_clamps_when_filter_shrinks_list(self):
        overlay = SelectorOverlay()
        opts = self._model_options()
        task = asyncio.create_task(overlay.show_select("Pick", opts, "", app=None))
        await asyncio.sleep(0)

        # Move highlight to the last option (index 2).
        _press(overlay, "end")
        assert overlay._highlight == 2

        # Type a filter that leaves only opus — highlight must clamp to 0.
        for ch in "opus":
            _press(overlay, ch)
        assert len(overlay._filtered) == 1
        assert overlay._highlight == 0

        _press(overlay, "enter")
        assert await task == "opus-4-7"

    async def test_enter_on_no_matches_resolves_none(self):
        overlay = SelectorOverlay()
        opts = self._model_options()
        task = asyncio.create_task(overlay.show_select("Pick", opts, "", app=None))
        await asyncio.sleep(0)

        for ch in "zzzzz":
            _press(overlay, ch)
        assert overlay._filtered == []

        _press(overlay, "enter")
        assert await task is None

    async def test_clearing_query_restores_full_list(self):
        overlay = SelectorOverlay()
        opts = self._model_options()
        task = asyncio.create_task(overlay.show_select("Pick", opts, "", app=None))
        await asyncio.sleep(0)

        for ch in "gpt":
            _press(overlay, ch)
        assert len(overlay._filtered) == 1

        _press(overlay, "c-u")
        assert len(overlay._filtered) == 3

        _press(overlay, "escape")
        await task

    async def test_space_and_punctuation_append_to_query(self):
        overlay = SelectorOverlay()
        opts = [
            {"value": "x", "label": "x 1"},
            {"value": "y", "label": "y 2"},
        ]
        task = asyncio.create_task(overlay.show_select("Pick", opts, "", app=None))
        await asyncio.sleep(0)

        _press(overlay, "x")
        _press(overlay, " ", data=" ")
        _press(overlay, "1")
        assert overlay._query == "x 1"
        assert [o["value"] for o in overlay._filtered] == ["x"]

        _press(overlay, "escape")
        await task


# ── show_confirm ───────────────────────────────────────────────────────


class TestShowConfirm:
    async def test_y_resolves_true(self):
        overlay = SelectorOverlay()
        task = asyncio.create_task(overlay.show_confirm("Sure?", app=None))
        await asyncio.sleep(0)
        assert overlay.is_confirm is True

        _press(overlay, "y")
        assert await task is True

    async def test_capital_y_resolves_true(self):
        overlay = SelectorOverlay()
        task = asyncio.create_task(overlay.show_confirm("Sure?", app=None))
        await asyncio.sleep(0)

        _press(overlay, "Y", data="Y")
        assert await task is True

    async def test_n_resolves_false(self):
        overlay = SelectorOverlay()
        task = asyncio.create_task(overlay.show_confirm("Sure?", app=None))
        await asyncio.sleep(0)

        _press(overlay, "n")
        assert await task is False

    async def test_escape_resolves_false(self):
        overlay = SelectorOverlay()
        task = asyncio.create_task(overlay.show_confirm("Sure?", app=None))
        await asyncio.sleep(0)

        _press(overlay, "escape")
        assert await task is False


# ── Mode isolation (y/n as search in select, not trigger) ──────────────


class TestModeIsolation:
    async def test_y_typed_as_search_in_select_mode(self):
        overlay = SelectorOverlay()
        opts = [
            {"value": "yolo", "label": "yolo"},
            {"value": "other", "label": "other"},
        ]
        task = asyncio.create_task(overlay.show_select("Pick", opts, "", app=None))
        await asyncio.sleep(0)

        # 'y' must not resolve the future — it filters the list.
        _press(overlay, "y")
        assert not task.done()
        assert overlay._query == "y"
        assert [o["value"] for o in overlay._filtered] == ["yolo"]

        _press(overlay, "enter")
        assert await task == "yolo"

    async def test_n_typed_as_search_in_select_mode(self):
        overlay = SelectorOverlay()
        opts = [
            {"value": "nano", "label": "nano"},
            {"value": "opus", "label": "opus"},
        ]
        task = asyncio.create_task(overlay.show_select("Pick", opts, "", app=None))
        await asyncio.sleep(0)

        _press(overlay, "n")
        assert not task.done()
        assert overlay._query == "n"
        assert [o["value"] for o in overlay._filtered] == ["nano"]

        _press(overlay, "escape")
        await task

    async def test_navigation_keys_ignored_in_confirm_mode(self):
        overlay = SelectorOverlay()
        task = asyncio.create_task(overlay.show_confirm("Sure?", app=None))
        await asyncio.sleep(0)

        # Up/down must not fire in confirm mode; overlay stays open.
        _press(overlay, "up")
        _press(overlay, "down")
        assert not task.done()

        _press(overlay, "n")
        assert await task is False


# ── Concurrent show refused ────────────────────────────────────────────


class TestConcurrentShow:
    async def test_concurrent_select_returns_none(self):
        overlay = SelectorOverlay()
        opts = [{"value": "a"}, {"value": "b"}]
        first = asyncio.create_task(overlay.show_select("Pick", opts, "", app=None))
        await asyncio.sleep(0)

        second = await overlay.show_select("Pick", opts, "", app=None)
        assert second is None
        assert overlay.visible is True

        _press(overlay, "enter")
        assert await first == "a"

    async def test_concurrent_confirm_returns_false(self):
        overlay = SelectorOverlay()
        first = asyncio.create_task(overlay.show_confirm("Sure?", app=None))
        await asyncio.sleep(0)

        second = await overlay.show_confirm("Also sure?", app=None)
        assert second is False

        _press(overlay, "y")
        assert await first is True


# ── Rendering sanity ───────────────────────────────────────────────────


class TestRendering:
    async def test_select_renders_title_search_and_options(self):
        overlay = SelectorOverlay()
        opts = [
            {
                "value": "opus",
                "label": "opus",
                "model": "claude-opus-4-7",
                "provider": "anthropic",
            },
            {
                "value": "sonnet",
                "label": "sonnet",
                "model": "claude-sonnet-4-6",
                "provider": "anthropic",
            },
        ]
        task = asyncio.create_task(
            overlay.show_select("Switch Model", opts, "", app=None)
        )
        await asyncio.sleep(0)

        fragments = overlay._render_select()
        joined = "".join(text for _, text in fragments)
        assert "Switch Model" in joined
        assert "search" in joined
        assert "type to filter" in joined
        assert "opus" in joined
        assert "sonnet" in joined
        assert "↑↓" in joined

        _press(overlay, "escape")
        await task

    async def test_query_replaces_placeholder(self):
        overlay = SelectorOverlay()
        opts = [{"value": "a", "label": "a"}, {"value": "b", "label": "b"}]
        task = asyncio.create_task(overlay.show_select("Pick", opts, "", app=None))
        await asyncio.sleep(0)

        _press(overlay, "a")
        joined = "".join(text for _, text in overlay._render_select())
        assert "type to filter" not in joined
        assert "  search " in joined

        _press(overlay, "escape")
        await task

    async def test_no_matches_message(self):
        overlay = SelectorOverlay()
        opts = [{"value": "a", "label": "a"}]
        task = asyncio.create_task(overlay.show_select("Pick", opts, "", app=None))
        await asyncio.sleep(0)

        for ch in "zzz":
            _press(overlay, ch)
        joined = "".join(text for _, text in overlay._render_select())
        assert "no matches" in joined

        _press(overlay, "escape")
        await task

    async def test_count_shown_when_filtered(self):
        overlay = SelectorOverlay()
        opts = [
            {"value": "a", "label": "alpha"},
            {"value": "b", "label": "bravo"},
            {"value": "c", "label": "charlie"},
        ]
        task = asyncio.create_task(overlay.show_select("Pick", opts, "", app=None))
        await asyncio.sleep(0)

        # 'r' appears in bravo + charlie, not alpha.
        _press(overlay, "r")
        assert len(overlay._filtered) == 2
        joined = "".join(text for _, text in overlay._render_select())
        assert "2/3" in joined

        _press(overlay, "escape")
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

        _press(overlay, "escape")
        await task


# ── State cleanup ──────────────────────────────────────────────────────


class TestStateReset:
    async def test_state_cleared_after_select(self):
        overlay = SelectorOverlay()
        opts = [{"value": "a"}, {"value": "b"}]
        task = asyncio.create_task(overlay.show_select("Pick", opts, "", app=None))
        await asyncio.sleep(0)
        _press(overlay, "enter")
        await task

        assert overlay._mode is None
        assert overlay._options == []
        assert overlay._filtered == []
        assert overlay._query == ""
        assert overlay._highlight == 0
        assert overlay._future is None

    async def test_query_cleared_between_sessions(self):
        overlay = SelectorOverlay()
        opts = [{"value": "a"}, {"value": "b"}]

        # First session: type something, then cancel.
        first = asyncio.create_task(overlay.show_select("Pick", opts, "", app=None))
        await asyncio.sleep(0)
        _press(overlay, "a")
        _press(overlay, "escape")
        assert await first is None

        # Second session: query must start empty.
        second = asyncio.create_task(overlay.show_select("Pick", opts, "", app=None))
        await asyncio.sleep(0)
        assert overlay._query == ""
        assert len(overlay._filtered) == 2
        _press(overlay, "enter")
        assert await second == "a"


@pytest.fixture(autouse=True)
def _fast_event_loop():
    yield
