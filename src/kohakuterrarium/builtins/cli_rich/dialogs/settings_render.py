"""Rendering helpers for ``SettingsOverlay``.

Split out of ``settings.py`` to keep the main file under the 600-line
soft limit. All functions here are pure read-only views over overlay
state — they take the overlay and return Rich renderables.
"""

from io import StringIO
from typing import TYPE_CHECKING, Any

from rich.console import Console, Group, RenderableType
from rich.panel import Panel
from rich.text import Text

if TYPE_CHECKING:
    from kohakuterrarium.builtins.cli_rich.dialogs.settings import (
        FormField,
        FormState,
        SettingsOverlay,
    )


VISIBLE_ROWS = 10


def render_overlay(overlay: "SettingsOverlay", width: int) -> str:
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


def _build_panel(overlay: "SettingsOverlay") -> RenderableType:
    tab_line = _render_tab_line(overlay)
    if overlay.mode == "confirm":
        body = _render_confirm_body(overlay._confirm)
    elif overlay.mode == "form":
        body = _render_form_body(overlay._form)
    else:
        body = _render_list_body(overlay)

    hint = _render_hint(overlay.mode)
    flash_line = Text()
    if overlay._flash:
        flash_line.append("  " + overlay._flash, style="yellow")

    panel_body = Group(tab_line, Text(""), body, Text(""), flash_line, hint)
    title = Text("Settings", style="bold magenta")
    return Panel(
        panel_body,
        title=title,
        border_style="magenta",
        padding=(0, 1),
        expand=True,
    )


def _render_tab_line(overlay: "SettingsOverlay") -> Text:
    from kohakuterrarium.builtins.cli_rich.dialogs.settings import TABS

    line = Text()
    for i, tab in enumerate(TABS):
        if tab == overlay.tab:
            line.append(f" {tab} ", style="bold cyan reverse")
        else:
            line.append(f" {tab} ", style="dim")
        if i < len(TABS) - 1:
            line.append("│", style="bright_black")
    return line


def _render_list_body(overlay: "SettingsOverlay") -> RenderableType:
    rows: list[RenderableType] = []
    entries = overlay._entries.get(overlay.tab) or []
    if not entries:
        rows.append(Text("  (empty)", style="dim"))
        return Group(*rows)

    total = len(entries)
    cursor = overlay._cursor[overlay.tab]
    if total <= VISIBLE_ROWS:
        start, end = 0, total
    else:
        half = VISIBLE_ROWS // 2
        start = max(0, min(cursor - half, total - VISIBLE_ROWS))
        end = start + VISIBLE_ROWS
    if start > 0:
        rows.append(Text(f"  ↑ {start} more above", style="dim bright_black"))
    for i in range(start, end):
        rows.append(_render_row(overlay.tab, entries[i], i == cursor))
    if end < total:
        rows.append(Text(f"  ↓ {total - end} more below", style="dim bright_black"))
    return Group(*rows)


def _render_row(tab: str, row: dict[str, Any], selected: bool) -> Text:
    line = Text()
    prefix = "  › " if selected else "    "
    prefix_style = "bold bright_cyan" if selected else "dim"
    line.append(prefix, style=prefix_style)
    if tab == "Keys":
        line.append(row["provider"].ljust(14), style="bold" if selected else "")
        masked_style = "green" if row["has_key"] else "dim"
        line.append(row["masked"], style=masked_style)
        if row.get("env"):
            line.append(f"   env:{row['env']}", style="dim")
    elif tab == "Providers":
        if row.get("is_add_row"):
            line.append(row["name"], style="bold green" if selected else "green")
            return line
        line.append(row["name"].ljust(14), style="bold" if selected else "")
        line.append(row["backend_type"].ljust(8), style="cyan")
        if row.get("base_url"):
            line.append(row["base_url"], style="dim")
        if row.get("built_in"):
            line.append("  (built-in)", style="dim bright_black")
    elif tab == "Models":
        marker = "✓ " if row["is_default"] else "  "
        line.append(marker, style="green" if row["is_default"] else "dim")
        name_style = "bold" if selected else ""
        if not row.get("available", True):
            name_style = "dim"
        line.append(row["name"].ljust(28), style=name_style)
        line.append(row["model"].ljust(32), style="dim")
        line.append(f"({row.get('provider') or '—'})", style="magenta")
        if row.get("user_defined"):
            line.append("  user", style="yellow")
        if not row.get("available", True):
            line.append("  (no key)", style="dim yellow")
    elif tab == "MCP":
        if row.get("is_add_row"):
            line.append(row["name"], style="bold green" if selected else "green")
            return line
        line.append(row["name"].ljust(18), style="bold" if selected else "")
        line.append(row["transport"].ljust(8), style="cyan")
        tgt = row.get("command") or row.get("url") or ""
        line.append(tgt, style="dim")
    return line


def _render_form_body(form: "FormState | None") -> RenderableType:
    assert form is not None
    rows: list[RenderableType] = [
        Text(f"  {form.title}", style="bold magenta"),
        Text(""),
    ]
    for i, fld in enumerate(form.fields):
        is_current = i == form.cursor
        prefix = "  › " if is_current else "    "
        style = "bold" if is_current else ""
        line = Text()
        line.append(prefix, style="bright_cyan" if is_current else "dim")
        line.append(f"{fld.label}: ", style=style)
        line.append_text(_render_field_value(fld, is_current))
        if fld.hint and is_current:
            line.append(f"    {fld.hint}", style="dim")
        rows.append(line)
    if form.message:
        rows.append(Text(""))
        rows.append(Text(f"  ! {form.message}", style="red"))
    return Group(*rows)


def _render_field_value(fld: "FormField", is_current: bool) -> Text:
    out = Text()
    if fld.options:
        for opt in fld.options:
            if opt == fld.value:
                out.append(f"[{opt}]", style="cyan bold")
            else:
                out.append(f" {opt} ", style="dim")
        return out
    value = fld.value
    shown = ("•" * len(value)) if (fld.secret and value) else value
    if not shown:
        out.append("(empty)", style="dim italic")
    else:
        out.append(shown, style="cyan" if is_current else "")
    if is_current:
        out.append("█", style="cyan")
    return out


def _render_confirm_body(confirm) -> RenderableType:
    assert confirm is not None
    return Group(
        Text(""),
        Text(f"  {confirm.message}", style="bold yellow"),
        Text(""),
        Text("  [Y]es   [N]o / esc", style="dim"),
    )


def _render_hint(mode: str) -> Text:
    hint = Text()
    if mode == "list":
        segments: list[tuple[str, str]] = [
            ("↑↓", "navigate"),
            ("tab", "switch tab"),
            ("enter", "edit/select"),
            ("d", "delete"),
            ("esc", "close"),
        ]
    elif mode == "form":
        segments = [
            ("tab/↑↓", "field"),
            ("←→", "cycle"),
            ("enter", "next/save"),
            ("esc", "cancel"),
        ]
    else:
        segments = [
            ("y", "confirm"),
            ("n/esc", "cancel"),
        ]
    for i, (k, label) in enumerate(segments):
        if i > 0:
            hint.append("  ")
        hint.append(k, style="cyan")
        hint.append(f" {label}", style="dim")
    return hint
