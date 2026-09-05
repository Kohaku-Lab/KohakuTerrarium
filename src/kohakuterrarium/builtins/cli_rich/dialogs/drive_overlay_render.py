"""Rendering for :class:`DriveOverlay`.

These helpers are read-only views over overlay state.
"""

from io import StringIO
from typing import TYPE_CHECKING, Any

from rich.console import Console, Group, RenderableType
from rich.panel import Panel
from rich.text import Text

from kohakuterrarium.builtins.cli_rich.dialogs.drive_format import (
    next_wake,
    owner_assignee,
    status_meta,
    warning_badges,
)

if TYPE_CHECKING:
    from kohakuterrarium.builtins.cli_rich.dialogs.drive_overlay import DriveOverlay

VISIBLE_ROWS = 10


def render_drive_overlay(overlay: "DriveOverlay", width: int) -> str:
    if not overlay.visible:
        return ""
    buf = StringIO()
    console = Console(
        file=buf,
        force_terminal=True,
        color_system="truecolor",
        width=max(50, width),
        legacy_windows=False,
        soft_wrap=False,
        emoji=False,
    )
    console.print(_build_panel(overlay), end="")
    return buf.getvalue().rstrip("\n")


def _build_panel(overlay: "DriveOverlay") -> RenderableType:
    header = _render_header(overlay)
    if overlay.mode == "confirm":
        body = _render_confirm(overlay)
    elif overlay.mode == "progress":
        body = _render_progress(overlay)
    elif overlay.mode == "create":
        body = _render_create(overlay)
    elif overlay.mode == "detail":
        body = _render_detail(overlay)
    else:
        body = _render_list(overlay)

    lines: list[RenderableType] = [header, Text("")]
    if overlay._error:
        lines.append(Text(f"  {overlay._error}", style="bright_red"))
    lines.append(body)
    if overlay._flash:
        lines.append(Text(""))
        lines.append(Text(f"  {overlay._flash}", style="yellow"))
    lines.append(Text(""))
    lines.append(_render_hint(overlay.mode))
    return Panel(
        Group(*lines),
        title=Text("Drives", style="bold magenta"),
        border_style="magenta",
        padding=(0, 1),
        expand=True,
    )


def _render_header(overlay: "DriveOverlay") -> Text:
    line = Text()
    line.append("creature: ", style="dim")
    line.append(overlay.creature_label(), style="bold cyan")
    line.append("   scope: ", style="dim")
    line.append(
        "assigned to me" if overlay.scope == "mine" else "whole graph",
        style="bold cyan",
    )
    line.append("   filter: ", style="dim")
    line.append(overlay.status_filter_label, style="bold cyan")
    line.append(f"   {_count_summary(overlay)}", style="dim")
    return line


def _count_summary(overlay: "DriveOverlay") -> str:
    active = sum(1 for r in overlay._rows if r["status"] in ("active", "waiting"))
    blocked = sum(1 for r in overlay._rows if r["status"] == "blocked")
    return f"{len(overlay._rows)} shown • {active} active • {blocked} blocked"


def _render_list(overlay: "DriveOverlay") -> RenderableType:
    if not overlay._rows:
        return Text("  (no drives)", style="dim")
    rows: list[RenderableType] = []
    total = len(overlay._rows)
    cursor = overlay._cursor
    if total <= VISIBLE_ROWS:
        start, end = 0, total
    else:
        half = VISIBLE_ROWS // 2
        start = max(0, min(cursor - half, total - VISIBLE_ROWS))
        end = start + VISIBLE_ROWS
    if start > 0:
        rows.append(Text(f"  ↑ {start} more above", style="dim bright_black"))
    for i in range(start, end):
        rows.append(_render_row(overlay._rows[i], i == cursor))
    if end < total:
        rows.append(Text(f"  ↓ {total - end} more below", style="dim bright_black"))
    return Group(*rows)


def _render_row(row: dict[str, Any], selected: bool) -> Text:
    icon, label, style = status_meta(row["status"])
    line = Text()
    line.append(
        "  › " if selected else "    ", style="bold bright_cyan" if selected else "dim"
    )
    line.append(f"{icon} ", style=style)
    line.append(label.ljust(10), style=style)
    title = row["title"]
    if len(title) > 34:
        title = title[:33] + "…"
    line.append(title.ljust(35), style="bold" if selected else "")
    line.append(owner_assignee(row), style="dim")
    for text, badge_style in warning_badges(row):
        line.append(f"  [{text}]", style=badge_style)
    wake = next_wake(row)
    if wake:
        line.append(f"  wake {wake}", style="cyan")
    return line


def _render_detail(overlay: "DriveOverlay") -> RenderableType:
    row = overlay._detail_row
    if row is None:
        return Text("  (drive unavailable)", style="dim")
    icon, label, style = status_meta(row["status"])
    rows: list[RenderableType] = []
    title_line = Text()
    title_line.append(f"  {icon} ", style=style)
    title_line.append(row["title"], style="bold")
    rows.append(title_line)
    rows.append(_field("id", row["drive_id"]))
    rows.append(_field("kind", f"{row['kind']}  (rev {row['revision']})"))
    status_field = Text()
    status_field.append("    status: ", style="dim")
    status_field.append(label, style=style)
    if row.get("status_reason"):
        status_field.append(f"  — {row['status_reason']}", style="dim")
    rows.append(status_field)
    rows.append(_field("owner → assignee", owner_assignee(row)))
    rows.append(_field("scope", row.get("scope_type", "?")))
    rows.append(_field("priority", str(row.get("priority", 0))))
    rows.append(_field("durability", str(row.get("durability", "unknown"))))
    badges = warning_badges(row)
    if badges:
        warn = Text("    warnings: ", style="dim")
        for text, badge_style in badges:
            warn.append(f"[{text}] ", style=badge_style)
        rows.append(warn)
    wake = next_wake(row)
    if wake:
        rows.append(_field("next wake", wake))
    rows.append(Text(""))
    rows.append(_render_progress_list(overlay))
    rows.append(Text(""))
    rows.append(_render_actions(overlay))
    return Group(*rows)


def _render_progress_list(overlay: "DriveOverlay") -> RenderableType:
    entries = overlay._detail_progress
    if not entries:
        return Text("    progress: (none)", style="dim")
    rows: list[RenderableType] = [Text("    progress:", style="dim")]
    for p in entries[-4:]:
        line = Text()
        line.append("      • ", style="dim")
        line.append(getattr(p, "summary", "")[:60])
        rows.append(line)
    return Group(*rows)


def _render_actions(overlay: "DriveOverlay") -> RenderableType:
    actions = overlay.enabled_actions()
    if not actions:
        return Text("    actions: (none available)", style="dim")
    line = Text("    actions: ", style="dim")
    for action in actions:
        line.append(f"[{action['key']}]", style="cyan")
        line.append(f" {action['label']}   ", style="")
    return line


def _render_confirm(overlay: "DriveOverlay") -> RenderableType:
    confirm = overlay._confirm or {}
    action = confirm.get("action", {})
    row = overlay._detail_row or {}
    msg = f"{action.get('label', '?')} drive {row.get('drive_id', '')}?"
    return Group(
        Text(""),
        Text(f"  {msg}", style="bold yellow"),
        Text(""),
        Text("  [Y]es   [N]o / esc", style="dim"),
    )


def _render_progress(overlay: "DriveOverlay") -> RenderableType:
    return Group(
        Text("  Log progress note:", style="bold"),
        Text(""),
        _cursor_line(overlay._progress_text),
        Text(""),
        Text("  enter submit · esc cancel", style="dim"),
    )


def _render_create(overlay: "DriveOverlay") -> RenderableType:
    return Group(
        Text(
            f"  New goal for {overlay.creature_label()} (manual autonomy; "
            "/goal resume wakes the next turn):",
            style="bold",
        ),
        Text(""),
        _cursor_line(overlay._create_text),
        Text(""),
        Text("  enter create · esc cancel", style="dim"),
    )


def _cursor_line(text: str) -> Text:
    out = Text("  ")
    out.append(text or "", style="cyan")
    out.append("█", style="cyan")
    return out


def _field(label: str, value: str) -> Text:
    line = Text()
    line.append(f"    {label}: ", style="dim")
    line.append(value)
    return line


def _render_hint(mode: str) -> Text:
    hint = Text()
    if mode == "list":
        segments = [
            ("↑↓", "navigate"),
            ("tab", "scope"),
            ("m", "creature"),
            ("s", "filter"),
            ("n", "new goal"),
            ("enter", "open"),
            ("r", "refresh"),
            ("esc", "close"),
        ]
    elif mode == "detail":
        segments = [
            ("p/r/w/c", "pause/resume/wake/cancel"),
            ("g", "progress"),
            ("esc", "back"),
        ]
    elif mode == "confirm":
        segments = [("y", "confirm"), ("n/esc", "cancel")]
    elif mode == "create":
        segments = [("type", "objective"), ("enter", "create"), ("esc", "cancel")]
    else:
        segments = [("type", "note"), ("enter", "submit"), ("esc", "cancel")]
    for i, (key, label) in enumerate(segments):
        if i > 0:
            hint.append("  ")
        hint.append(key, style="cyan")
        hint.append(f" {label}", style="dim")
    return hint


__all__ = ["render_drive_overlay"]
