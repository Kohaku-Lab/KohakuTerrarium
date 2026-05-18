"""Roster widget — the horizontal slot row above the input box.

Lays out one slot per creature with focus marker, state glyph, name,
activity summary. Width-adaptive: when the terminal is too narrow to
show every creature by name, idle / stopped collapse to a count and
working / waiting always stay visible (the "what needs my attention"
priority).

The widget is render-only — it reads a snapshot list of
:class:`CreatureStatus` and the focus id and returns a Rich
``Text``. Pure function semantics: no internal mutable state, so
unit tests just call ``render`` at known widths and snapshot the
output.

Width budget (per render):

::

    term_width = chrome (4 cols: leading "  ", trailing "  ") +
                 N visible slots × (slot_width + 1 sep) +
                 optional collapsed-tail ("  +N idle  +N stopped")

The ``min_slot_width`` floor is 14 chars — enough for
``▸name ● short..``. Below that the slot is dropped to the
collapsed tail.
"""

from collections.abc import Callable
from typing import Literal

from rich.text import Text

from kohakuterrarium.builtins.cli_rich.creature_status import (
    STATE_PRIORITY,
    CreatureStatus,
    StatusState,
)

_GLYPH: dict[StatusState, str] = {
    "working": "●",
    "idle": "○",
    "waiting": "⚠",
    "failed": "✗",
    "stopped": "■",
}

_STYLE: dict[StatusState, str] = {
    "working": "bold green",
    "idle": "dim",
    "waiting": "bold yellow",
    "failed": "bold red",
    "stopped": "dim white",
}

# Focus marker — `▸` (Unicode triangle) before the name when focused.
_FOCUS_MARKER = "▸"
_FOCUS_PAD = " "  # space when not focused, to keep slots aligned

_MIN_SLOT_WIDTH = 14
_NAME_MAX = 10
_CHROME = 4  # 2 leading + 2 trailing spaces
_SLOT_SEP = "  "  # two-space separator between slots


def _truncate_name(name: str) -> str:
    return name if len(name) <= _NAME_MAX else name[: _NAME_MAX - 1] + "…"


def _truncate_activity(activity: str, max_width: int) -> str:
    if max_width <= 0:
        return ""
    if len(activity) <= max_width:
        return activity
    if max_width == 1:
        return "…"
    return activity[: max_width - 1] + "…"


def _render_slot(status: CreatureStatus, is_focused: bool, slot_width: int) -> Text:
    """Render one slot as Rich ``Text``.

    Layout: ``<focus><name>[<●unread>] <glyph> <activity>``. Activity
    is truncated to fit ``slot_width``. The unread badge ``●N``
    appears between name and state glyph only when the creature is
    NOT focused and has unread > 0 (focused creatures are presumed
    seen).
    """
    out = Text()
    out.append(_FOCUS_MARKER if is_focused else _FOCUS_PAD, style="bold cyan")
    name = _truncate_name(status.name)
    out.append(name, style="bold" if is_focused else "")
    badge_len = 0
    if not is_focused and status.unread > 0:
        # Cap at 99+ to keep the slot width predictable.
        if status.unread < 100:
            badge_text = f"●{status.unread}"
        else:
            badge_text = "●99+"
        out.append(" ")
        out.append(badge_text, style="bold cyan")
        badge_len = 1 + len(badge_text)
    out.append(" ")
    out.append(_GLYPH[status.state], style=_STYLE[status.state])
    used = 1 + len(name) + badge_len + 1 + 1  # focus + name + badge + space + glyph
    remaining = slot_width - used - 1
    if remaining > 0:
        out.append(" ")
        out.append(_truncate_activity(status.activity, remaining), style="dim")
    return out


def _partition(
    statuses: list[CreatureStatus],
    focus_id: str,
    term_width: int,
) -> tuple[list[CreatureStatus], int, int]:
    """Decide which statuses get a full slot vs collapse to a count.

    Always-shown:
        - the focused creature (if present)
        - every ``waiting`` and ``working`` creature

    Collapse-eligible (when budget runs out):
        - ``idle`` first, then ``stopped`` / ``failed``

    Returns ``(visible, collapsed_idle_count, collapsed_stopped_count)``.
    ``visible`` preserves the input order EXCEPT the focused creature
    keeps its position; ``waiting`` creatures rise to the leftmost
    slot only when not focused (so the user always sees urgent work).
    """
    if not statuses:
        return [], 0, 0

    # Always-show buckets.
    always = []
    optional = []
    for s in statuses:
        is_focus = s.creature_id == focus_id
        if is_focus or s.state in ("waiting", "working"):
            always.append(s)
        else:
            optional.append(s)

    # If everything fits, return as-is in original order.
    budget = max(0, term_width - _CHROME)
    total_width = _estimate_width(always + optional)
    if total_width <= budget:
        # Keep the original order, but float "waiting" creatures to
        # the leftmost slot UNLESS one is focused (the focused
        # creature stays in its definition slot so Tab feels stable).
        ordered = _reorder_for_visibility(statuses, focus_id)
        return ordered, 0, 0

    # Drop optional creatures (idle / stopped / failed) until the
    # always-show set fits; count what we dropped per bucket.
    visible = list(always)
    dropped_idle = 0
    dropped_stopped = 0
    for s in optional:
        # Try adding — does it still fit at min_slot_width?
        candidate = visible + [s]
        if _estimate_width(candidate) <= budget:
            visible.append(s)
            continue
        if s.state == "idle":
            dropped_idle += 1
        else:
            dropped_stopped += 1

    visible = _reorder_for_visibility(visible, focus_id)
    return visible, dropped_idle, dropped_stopped


def _reorder_for_visibility(
    statuses: list[CreatureStatus], focus_id: str
) -> list[CreatureStatus]:
    """``waiting`` creatures rise to leftmost; focus stays put if present.

    Other states keep definition order.
    """

    # Sort key: lower comes first.
    # - focused creature: tie-break to its original index (stable).
    # - waiting: priority 0 — leftmost.
    # - working / failed / stopped / idle: priority 5 — keep order.
    def key(item: tuple[int, CreatureStatus]) -> tuple[int, int]:
        idx, s = item
        if s.creature_id == focus_id:
            return (1, idx)
        if s.state == "waiting":
            return (0, idx)
        return (2, idx)

    indexed = list(enumerate(statuses))
    indexed.sort(key=key)
    return [s for _, s in indexed]


def _estimate_width(statuses: list[CreatureStatus]) -> int:
    """Estimate the rendered width if every status got a min-width slot."""
    if not statuses:
        return 0
    return len(statuses) * _MIN_SLOT_WIDTH + (len(statuses) - 1) * len(_SLOT_SEP)


class RosterWidget:
    """Single-line roster of every creature.

    Stateless wrt the creature list — looks it up via the
    ``get_statuses`` / ``get_focus_id`` callables on every render so
    the caller can swap the source without rebuilding the widget.
    """

    def __init__(
        self,
        get_statuses: Callable[[], list[CreatureStatus]],
        get_focus_id: Callable[[], str],
    ) -> None:
        self.get_statuses = get_statuses
        self.get_focus_id = get_focus_id

    def render(self, term_width: int) -> Text:
        statuses = self.get_statuses() or []
        focus_id = self.get_focus_id() or ""
        if len(statuses) <= 1:
            # Single-creature behavior is byte-identical to today:
            # the roster is invisible.
            return Text("")

        visible, dropped_idle, dropped_stopped = _partition(
            statuses, focus_id, term_width
        )

        # Compute per-slot width.
        budget = max(0, term_width - _CHROME)
        n = len(visible)
        if n == 0:
            return Text("")
        seps = (n - 1) * len(_SLOT_SEP)
        # Reserve room for the collapsed tail.
        tail_pieces: list[str] = []
        if dropped_idle:
            tail_pieces.append(f"+{dropped_idle} idle")
        if dropped_stopped:
            tail_pieces.append(f"+{dropped_stopped} stopped")
        tail_width = sum(len(p) for p in tail_pieces) + (
            (len(tail_pieces) - 1) * len(_SLOT_SEP) if tail_pieces else 0
        )
        if tail_pieces:
            tail_width += len(_SLOT_SEP)  # leading sep before tail
        per_slot = max(_MIN_SLOT_WIDTH, (budget - seps - tail_width) // n)

        out = Text("  ")  # leading chrome
        for i, status in enumerate(visible):
            if i > 0:
                out.append(_SLOT_SEP)
            out.append(_render_slot(status, status.creature_id == focus_id, per_slot))
        if dropped_idle:
            out.append(_SLOT_SEP)
            out.append(f"+{dropped_idle} idle", style="dim")
        if dropped_stopped:
            out.append(_SLOT_SEP)
            out.append(f"+{dropped_stopped} stopped", style="dim red")
        return out


# Re-export for typing convenience.
RosterCompressMode = Literal["full", "compressed"]


__all__ = [
    "RosterWidget",
    "STATE_PRIORITY",
]
