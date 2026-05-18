"""Ctrl+A "agent view" overlay — grouped creature list with filter.

Two layers:

- :class:`AgentOverlayState` — pure data: list of statuses, filter
  string, selected index, peek target. No prompt_toolkit, no Rich.
  100% unit-testable.
- :class:`AgentOverlay` — owns the prompt_toolkit container the
  RichCLIApp mounts as a Float. Delegates all state to
  ``AgentOverlayState`` so the keyboard / picker integration can be
  smoke-tested separately.

Grouping order (top to bottom in the rendered list):

    Needs input → Working → Idle → Stopped → Failed

The filter string narrows by substring against name + activity.
Selected index is constrained to visible (post-filter) rows.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

from rich.console import Group, RenderableType
from rich.panel import Panel
from rich.text import Text

from kohakuterrarium.builtins.cli_rich.creature_status import (
    CreatureStatus,
    StatusState,
)

# Grouping order — first listed renders first.
_GROUP_ORDER: list[StatusState] = ["waiting", "working", "idle", "stopped", "failed"]
_GROUP_LABEL: dict[StatusState, str] = {
    "waiting": "Needs input",
    "working": "Working",
    "idle": "Idle",
    "stopped": "Stopped",
    "failed": "Failed",
}
_GROUP_STYLE: dict[StatusState, str] = {
    "waiting": "bold yellow",
    "working": "bold green",
    "idle": "dim",
    "stopped": "dim white",
    "failed": "bold red",
}
_GLYPH: dict[StatusState, str] = {
    "working": "●",
    "idle": "○",
    "waiting": "⚠",
    "failed": "✗",
    "stopped": "■",
}


@dataclass
class AgentOverlayState:
    """Pure data state for the agent overlay.

    The overlay's UI render reads from this; keyboard handlers
    mutate it. ``selected_id`` is the canonical "what's highlighted"
    pointer (creature_id, not index, so it survives filter changes).
    """

    statuses: list[CreatureStatus] = field(default_factory=list)
    filter_text: str = ""
    selected_id: str = ""
    peek_id: str | None = None  # None = peek closed

    def visible(self) -> list[CreatureStatus]:
        """Statuses that match the filter, grouped by state in priority order."""
        text = self.filter_text.strip().lower()

        def matches(s: CreatureStatus) -> bool:
            if not text:
                return True
            blob = (s.name + " " + s.activity).lower()
            return text in blob

        out: list[CreatureStatus] = []
        for state in _GROUP_ORDER:
            for s in self.statuses:
                if s.state == state and matches(s):
                    out.append(s)
        return out

    def ensure_valid_selection(self) -> None:
        """Keep ``selected_id`` pointed at a visible row, or fall back to first."""
        visible = self.visible()
        if not visible:
            self.selected_id = ""
            self.peek_id = None
            return
        if not any(s.creature_id == self.selected_id for s in visible):
            self.selected_id = visible[0].creature_id

    def select_next(self) -> None:
        visible = self.visible()
        if not visible:
            return
        ids = [s.creature_id for s in visible]
        if self.selected_id not in ids:
            self.selected_id = ids[0]
            return
        idx = ids.index(self.selected_id)
        self.selected_id = ids[(idx + 1) % len(ids)]

    def select_prev(self) -> None:
        visible = self.visible()
        if not visible:
            return
        ids = [s.creature_id for s in visible]
        if self.selected_id not in ids:
            self.selected_id = ids[-1]
            return
        idx = ids.index(self.selected_id)
        self.selected_id = ids[(idx - 1) % len(ids)]

    def toggle_peek(self) -> None:
        """Space: open peek on selected; if already peeking it, close."""
        if not self.selected_id:
            return
        self.peek_id = None if self.peek_id == self.selected_id else self.selected_id

    def set_filter(self, text: str) -> None:
        self.filter_text = text or ""
        self.ensure_valid_selection()


KeyAction = Literal["consumed", "passthrough", "close", "focus", "peek"]


@dataclass
class KeyResult:
    """What the overlay decided after handling a key."""

    action: KeyAction
    creature_id: str | None = None  # for "focus" / "peek" actions


def handle_key(state: AgentOverlayState, key: str) -> KeyResult:
    """Translate a key event into a state mutation + outcome action.

    Pure function so the keyboard layer can be tested without
    prompt_toolkit. The composer integration wires this to the
    actual KeyPressEvent loop.

    Tab / Shift+Tab act as ↓ / ↑ inside the overlay so the user
    keeps cycling with the same key they used outside — without
    this Tab leaks through to the composer's focus_next and the
    overlay's selected_id drifts out of sync with the actual focused
    creature.
    """
    if key == "escape":
        return KeyResult(action="close")
    if key in ("up", "s-tab"):
        state.select_prev()
        return KeyResult(action="consumed")
    if key in ("down", "tab"):
        state.select_next()
        return KeyResult(action="consumed")
    if key == "space":
        state.toggle_peek()
        return KeyResult(action="peek", creature_id=state.peek_id)
    if key == "enter":
        if state.selected_id:
            return KeyResult(action="focus", creature_id=state.selected_id)
        return KeyResult(action="consumed")
    if key in ("right",) and state.peek_id:
        # → on a peeked row promotes peek to focus.
        return KeyResult(action="focus", creature_id=state.peek_id)
    return KeyResult(action="passthrough")


# ── Rendering ────────────────────────────────────────────────────────


def _row_text(status: CreatureStatus, is_selected: bool) -> Text:
    marker = "▸" if is_selected else " "
    out = Text(f" {marker} ")
    out.append(_GLYPH[status.state], style=_GROUP_STYLE[status.state])
    out.append(f"  {status.name:<16}", style="bold" if is_selected else "")
    out.append(f"  {status.activity}", style="dim")
    return out


def render_overlay(state: AgentOverlayState) -> RenderableType:
    """Build a Rich renderable for the overlay (caller wraps in a Float)."""
    visible = state.visible()
    rows: list[RenderableType] = []
    rows.append(Text(f" Filter: {state.filter_text or '(none)'}", style="dim"))
    rows.append(Text(""))
    if not visible:
        rows.append(Text(" No creatures match the filter.", style="dim"))
    else:
        for group in _GROUP_ORDER:
            members = [s for s in visible if s.state == group]
            if not members:
                continue
            rows.append(Text(f" {_GROUP_LABEL[group]}", style=_GROUP_STYLE[group]))
            for s in members:
                rows.append(_row_text(s, s.creature_id == state.selected_id))
            rows.append(Text(""))
    rows.append(
        Text(
            " ↑↓ select  Space peek  Enter focus  Esc close",
            style="dim",
        )
    )
    return Panel(Group(*rows), title="Agent view", border_style="cyan")


class AgentOverlay:
    """Stateful overlay — wraps :class:`AgentOverlayState` with callbacks."""

    def __init__(
        self,
        get_statuses: Callable[[], list[CreatureStatus]],
        on_focus: Callable[[str], None] | None = None,
        on_peek: Callable[[str | None], None] | None = None,
        on_close: Callable[[], None] | None = None,
    ) -> None:
        self.get_statuses = get_statuses
        self.on_focus = on_focus
        self.on_peek = on_peek
        self.on_close = on_close
        self.state = AgentOverlayState()
        self.visible: bool = False

    def open(self) -> None:
        """Refresh the snapshot + open."""
        self.state.statuses = list(self.get_statuses() or [])
        self.state.ensure_valid_selection()
        self.visible = True

    def close(self) -> None:
        self.visible = False
        self.state.peek_id = None
        if self.on_close is not None:
            self.on_close()

    def handle_key(self, key: str) -> bool:
        """Dispatch a key. Returns True if consumed (UI should refresh)."""
        if not self.visible:
            return False
        result = handle_key(self.state, key)
        if result.action == "close":
            self.close()
            return True
        if result.action == "focus" and result.creature_id:
            if self.on_focus is not None:
                self.on_focus(result.creature_id)
            self.close()
            return True
        if result.action == "peek":
            if self.on_peek is not None:
                self.on_peek(self.state.peek_id)
            return True
        if result.action == "consumed":
            return True
        return False

    def handle_text(self, text: str) -> bool:
        """Append printable text to the filter."""
        if not self.visible:
            return False
        self.state.set_filter(self.state.filter_text + text)
        return True

    def backspace(self) -> bool:
        if not self.visible:
            return False
        self.state.set_filter(self.state.filter_text[:-1])
        return True

    def render(self) -> RenderableType | None:
        if not self.visible:
            return None
        # Refresh the snapshot every render so add/remove topology
        # changes propagate without explicit invalidation calls.
        self.state.statuses = list(self.get_statuses() or [])
        self.state.ensure_valid_selection()
        return render_overlay(self.state)


__all__ = [
    "AgentOverlay",
    "AgentOverlayState",
    "KeyResult",
    "handle_key",
    "render_overlay",
]
