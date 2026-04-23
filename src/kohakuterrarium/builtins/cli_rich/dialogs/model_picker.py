"""Model picker dialog — interactive selection rendered inside the live region.

When opened, the picker takes over the status area (live region) with a
scrollable list of available presets, grouped by provider. Up/Down moves
the cursor, Left/Right cycles the currently-selected variation on the
hovered row, Tab rotates which variation group is being cycled, Enter
applies the composed selector, Esc cancels.

The picker intentionally reuses the same data shape as the web
ModelSwitcher — `list_all()` returns dicts with model/provider/
variation_groups/selected_variations so UX is consistent across the
web frontend, TUI, and CLI.
"""

from io import StringIO
from typing import Any, Callable

from rich.console import Console, Group, RenderableType
from rich.panel import Panel
from rich.text import Text

# How many rows the picker shows inside its panel. Enough to be useful,
# small enough that the panel doesn't eat the whole terminal even with a
# long preset list. Cursor auto-scrolls the viewport to keep itself in
# range.
VISIBLE_ROWS = 12


class ModelPicker:
    """Stateful model picker renderable.

    The RichCLIApp holds a single instance. When `open()` is called,
    `visible` flips True and the app routes rendering through
    `render(width)` instead of the normal live region content. Key
    events are delivered via `handle_key(name)` which returns True if
    the event was consumed.
    """

    def __init__(
        self,
        load_presets: Callable[[], list[dict[str, Any]]],
        on_apply: Callable[[str], None],
    ) -> None:
        self._load = load_presets
        self._on_apply = on_apply
        self.visible = False
        self._entries: list[dict[str, Any]] = []
        self._cursor = 0
        # Per-model variation state: keyed by model name → group → option.
        # Initialised from each model's `selected_variations` at open().
        self._selections: dict[str, dict[str, str]] = {}
        # Which variation group the Left/Right arrows cycle on the
        # currently-selected row. Empty string means "none".
        self._group_cursor: dict[str, int] = {}

    # ── Lifecycle ──

    def open(self) -> None:
        self._entries = list(self._load())
        # Selection state keyed by ``provider/name`` — bare names collide
        # across providers under the (provider, name) hierarchy (e.g.
        # ``gpt-5.4`` exists on codex, openai, openrouter and possibly a
        # user's custom provider), so we need the full identifier as the
        # dict key.
        self._selections = {
            self._entry_key(e): dict(e.get("selected_variations") or {})
            for e in self._entries
        }
        self._group_cursor = {self._entry_key(e): 0 for e in self._entries}
        # Default cursor to current / default model when present.
        self._cursor = 0
        for i, e in enumerate(self._entries):
            if e.get("is_default"):
                self._cursor = i
                break
        self.visible = True

    @staticmethod
    def _entry_key(entry: dict[str, Any]) -> str:
        """Unique key for a preset entry under the (provider, name) hierarchy."""
        provider = entry.get("provider") or entry.get("login_provider") or ""
        return f"{provider}/{entry['name']}" if provider else str(entry["name"])

    def close(self) -> None:
        self.visible = False

    # ── Keyboard ──

    def handle_key(self, key: str) -> bool:
        """Process a key while the picker is open.

        Returns True if the key was consumed (and the app should
        invalidate to redraw).
        """
        if not self.visible:
            return False

        if key in ("up", "c-p"):
            self._move_cursor(-1)
            return True
        if key in ("down", "c-n"):
            self._move_cursor(1)
            return True
        if key in ("pageup",):
            self._move_cursor(-5)
            return True
        if key in ("pagedown",):
            self._move_cursor(5)
            return True
        if key in ("left",):
            self._cycle_variation(-1)
            return True
        if key in ("right",):
            self._cycle_variation(+1)
            return True
        if key in ("tab",):
            self._rotate_group(+1)
            return True
        if key in ("s-tab", "backtab"):
            self._rotate_group(-1)
            return True
        if key in ("enter",):
            self._apply()
            return True
        if key in ("escape",):
            self.close()
            return True
        return False

    def _current(self) -> dict[str, Any] | None:
        if not self._entries:
            return None
        return self._entries[max(0, min(self._cursor, len(self._entries) - 1))]

    def _move_cursor(self, delta: int) -> None:
        if not self._entries:
            return
        self._cursor = max(0, min(len(self._entries) - 1, self._cursor + delta))

    def _current_group_names(self, entry: dict[str, Any]) -> list[str]:
        return sorted((entry.get("variation_groups") or {}).keys())

    def _cycle_variation(self, delta: int) -> None:
        entry = self._current()
        if entry is None:
            return
        groups = self._current_group_names(entry)
        if not groups:
            return
        key = self._entry_key(entry)
        group_idx = self._group_cursor.get(key, 0) % len(groups)
        group = groups[group_idx]
        options = sorted((entry["variation_groups"].get(group) or {}).keys())
        if not options:
            return
        selection = self._selections.setdefault(key, {})
        current_option = selection.get(group)
        if current_option in options:
            i = options.index(current_option)
        else:
            i = 0
        i = (i + delta) % len(options)
        selection[group] = options[i]

    def _rotate_group(self, delta: int) -> None:
        entry = self._current()
        if entry is None:
            return
        groups = self._current_group_names(entry)
        if not groups:
            return
        key = self._entry_key(entry)
        idx = self._group_cursor.get(key, 0)
        idx = (idx + delta) % len(groups)
        self._group_cursor[key] = idx

    def _apply(self) -> None:
        entry = self._current()
        if entry is None:
            self.close()
            return
        selector = self._compose_selector(entry)
        self.close()
        try:
            self._on_apply(selector)
        except Exception:
            # The app's command dispatcher will surface errors via
            # scrollback; swallowing here keeps the picker closure
            # atomic even if the downstream command fails.
            pass

    def _compose_selector(self, entry: dict[str, Any]) -> str:
        """Build the ``provider/name[@group=option,g2=o2]`` selector string.

        The ``provider/`` prefix is mandatory under the (provider, name)
        hierarchy: bare names are ambiguous when the same model is
        available through multiple providers (``gpt-5.4`` → codex,
        openai, openrouter, …), so the picker always emits the full
        identifier. Mirrors the same shorthand used by the web
        ModelSwitcher and ``resolve_controller_llm``.
        """
        identifier = self._entry_key(entry)
        key = identifier
        selection = self._selections.get(key) or {}
        parts = [f"{g}={o}" for g, o in sorted(selection.items()) if o]
        if not parts:
            return identifier
        return f"{identifier}@" + ",".join(parts)

    # ── Rendering ──

    def render(self, width: int) -> str:
        """Render the picker to ANSI text of *width* columns."""
        if not self.visible or not self._entries:
            return ""
        panel = self._build_panel()
        buf = StringIO()
        console = Console(
            file=buf,
            force_terminal=True,
            color_system="truecolor",
            width=max(40, width),
            legacy_windows=False,
            soft_wrap=False,
            emoji=False,
        )
        console.print(panel, end="")
        return buf.getvalue().rstrip("\n")

    def _viewport_bounds(self) -> tuple[int, int]:
        """Return (start, end) indexes for the visible window.

        We keep the cursor inside the window by scrolling whenever it
        would leave. Without this, a 40-preset list would render all 40
        rows inside the panel and eat the whole terminal on small
        screens.
        """
        total = len(self._entries)
        if total <= VISIBLE_ROWS:
            return 0, total
        half = VISIBLE_ROWS // 2
        start = max(0, min(self._cursor - half, total - VISIBLE_ROWS))
        end = start + VISIBLE_ROWS
        return start, end

    def _build_panel(self) -> RenderableType:
        rows: list[RenderableType] = []
        start, end = self._viewport_bounds()
        total = len(self._entries)

        if start > 0:
            rows.append(Text(f"  ↑ {start} more above", style="dim bright_black"))

        prev_provider = None
        for i in range(start, end):
            entry = self._entries[i]
            if entry["provider"] != prev_provider:
                if prev_provider is not None:
                    rows.append(Text(""))
                rows.append(Text(f"  {entry['provider']}", style="bold magenta"))
                prev_provider = entry["provider"]
            rows.append(self._render_row(entry, i == self._cursor))

        if end < total:
            rows.append(Text(f"  ↓ {total - end} more below", style="dim bright_black"))

        hint = Text()
        hint.append("↑↓", style="cyan")
        hint.append(" select  ", style="dim")
        hint.append("←→", style="cyan")
        hint.append(" variation  ", style="dim")
        hint.append("tab", style="cyan")
        hint.append(" group  ", style="dim")
        hint.append("enter", style="cyan")
        hint.append(" apply  ", style="dim")
        hint.append("esc", style="cyan")
        hint.append(" cancel", style="dim")

        selector_line = Text()
        current = self._current()
        if current is not None:
            selector_line.append("  → ", style="dim")
            selector_line.append(self._compose_selector(current), style="bold cyan")

        body = Group(*rows, Text(""), selector_line, Text(""), hint)
        title = Text("Select Model", style="bold magenta")
        return Panel(
            body,
            title=title,
            border_style="magenta",
            padding=(0, 1),
            expand=True,
        )

    def _render_row(self, entry: dict[str, Any], selected: bool) -> Text:
        line = Text()
        if selected:
            line.append("  › ", style="bold bright_cyan")
        elif entry.get("is_default"):
            line.append("  ✓ ", style="green")
        else:
            line.append("    ", style="dim")
        name_style = "bold" if selected else ""
        if not entry.get("available", True):
            name_style = "dim"
        line.append(entry["name"], style=name_style)

        # Variation chips — chosen option shown with group label, current
        # cycling group highlighted cyan.
        groups = self._current_group_names(entry)
        if groups:
            key = self._entry_key(entry)
            group_idx = self._group_cursor.get(key, 0) % len(groups)
            selection = self._selections.get(key) or {}
            for i, group in enumerate(groups):
                option = selection.get(group) or next(
                    iter(sorted((entry["variation_groups"].get(group) or {}).keys())),
                    "",
                )
                chip_style = (
                    "cyan bold" if selected and i == group_idx else "bright_black"
                )
                line.append(f"  [{group}=", style=chip_style)
                line.append(option, style=chip_style)
                line.append("]", style=chip_style)

        if not entry.get("available", True):
            line.append("  (unavailable)", style="dim yellow")
        return line

    # ── Introspection for tests ──

    def current_selector(self) -> str:
        current = self._current()
        if current is None:
            return ""
        return self._compose_selector(current)
