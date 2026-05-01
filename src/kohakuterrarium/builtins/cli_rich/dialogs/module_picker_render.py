"""Rendering helpers for :class:`ModulePicker`.

Split out of ``module_picker.py`` to keep both files under the
600-line soft limit. All functions here take the live overlay and
return Rich renderables — no mutation, pure read.
"""

from io import StringIO
from typing import TYPE_CHECKING, Any

from rich.console import Console, Group, RenderableType
from rich.panel import Panel
from rich.text import Text

from kohakuterrarium.builtins.cli_rich.dialogs.module_picker_model import (
    DEFAULT_TAB_ORDER,
    TAB_LABELS,
    ModuleEntry,
    ModuleFormField,
    ModuleFormState,
)

if TYPE_CHECKING:
    from kohakuterrarium.builtins.cli_rich.dialogs.module_picker import ModulePicker


VISIBLE_ROWS = 10


def render_overlay(overlay: "ModulePicker", width: int) -> str:
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


# ── Panel composition ───────────────────────────────────────────


def _build_panel(overlay: "ModulePicker") -> RenderableType:
    tab_line = _render_tab_line(overlay)
    if overlay.mode == "form":
        body = _render_form_body(overlay._form)
    else:
        body = _render_list_body(overlay)
    flash_line = Text()
    if overlay._flash:
        flash_line.append("  " + overlay._flash, style="yellow")
    hint = _render_hint(overlay)
    panel_body = Group(tab_line, Text(""), body, Text(""), flash_line, hint)
    title_text = (
        "Modules"
        if overlay.mode == "list"
        else f"Edit · {overlay._form.title if overlay._form else ''}"
    )
    return Panel(
        panel_body,
        title=Text(title_text, style="bold magenta"),
        border_style="magenta",
        padding=(0, 1),
        expand=True,
    )


def _render_tab_line(overlay: "ModulePicker") -> Text:
    line = Text()
    order = list(DEFAULT_TAB_ORDER)
    for tid in overlay._entries_by_type:
        if tid not in order:
            order.append(tid)
    for i, tid in enumerate(order):
        label = TAB_LABELS.get(tid, tid)
        count = len(overlay._entries_by_type.get(tid) or [])
        chip = f" {label} ({count}) "
        if tid == overlay.active_type:
            line.append(chip, style="bold cyan reverse")
        elif count == 0:
            line.append(chip, style="dim bright_black")
        else:
            line.append(chip, style="dim")
        if i < len(order) - 1:
            line.append("│", style="bright_black")
    return line


# ── List body ───────────────────────────────────────────────────


def _render_list_body(overlay: "ModulePicker") -> RenderableType:
    rows: list[RenderableType] = []
    entries = overlay._entries_by_type.get(overlay.active_type) or []
    if not entries:
        rows.append(
            Text(
                f"  No {TAB_LABELS.get(overlay.active_type, overlay.active_type).lower()} on this creature.",
                style="dim italic",
            )
        )
        return Group(*rows)

    cursor = overlay._cursor.get(overlay.active_type, 0)
    total = len(entries)
    if total <= VISIBLE_ROWS:
        start, end = 0, total
    else:
        half = VISIBLE_ROWS // 2
        start = max(0, min(cursor - half, total - VISIBLE_ROWS))
        end = start + VISIBLE_ROWS

    if start > 0:
        rows.append(Text(f"  ↑ {start} more above", style="dim bright_black"))

    # For plugins: insert "Enabled" / "Disabled" sub-headers at the
    # transition between groups (within the visible window).
    show_groups = (
        overlay.active_type == "plugin"
        and any(m.enabled is True for m in entries)
        and any(m.enabled is False for m in entries)
    )
    last_group: bool | None = None
    for i in range(start, end):
        m = entries[i]
        if show_groups and m.enabled != last_group:
            label = "Enabled" if m.enabled else "Disabled"
            if last_group is not None:
                rows.append(Text(""))
            rows.append(Text(f"  {label}", style="bold magenta"))
            last_group = m.enabled
        rows.append(_render_row(m, i == cursor))
    if end < total:
        rows.append(Text(f"  ↓ {total - end} more below", style="dim bright_black"))
    return Group(*rows)


def _render_row(m: ModuleEntry, selected: bool) -> Text:
    line = Text()
    prefix = "  › " if selected else "    "
    line.append(prefix, style="bold bright_cyan" if selected else "dim")

    # Status glyph.
    if m.enabled is True:
        line.append("● ", style="green")
    elif m.enabled is False:
        line.append("○ ", style="dim")
    else:
        line.append("· ", style="dim")

    name_style = "bold" if selected else ""
    line.append(m.name, style=name_style)

    # Priority + opt count chips.
    if m.priority is not None:
        line.append(f"  p{m.priority}", style="dim cyan")
    if m.schema:
        line.append(f"  {len(m.schema)} opt", style="dim")

    # Description tail (only for selected row, to keep the list dense).
    if selected and m.description:
        # Single line, ellipsised so the row never wraps.
        text = m.description.replace("\n", " ")
        if len(text) > 60:
            text = text[:57] + "…"
        line.append(f"   {text}", style="dim italic")
    return line


# ── Form body ───────────────────────────────────────────────────


def _render_form_body(form: "ModuleFormState | None") -> RenderableType:
    if form is None:
        return Text("  (no form)", style="dim")
    rows: list[RenderableType] = [
        Text(f"  {form.title}", style="bold magenta"),
        Text(""),
    ]
    for i, fld in enumerate(form.fields):
        rows.append(_render_field_row(fld, i == form.cursor))
    if form.message:
        rows.append(Text(""))
        rows.append(Text(f"  ! {form.message}", style="red"))
    return Group(*rows)


def _render_field_row(fld: "ModuleFormField", is_current: bool) -> Text:
    line = Text()
    prefix = "  › " if is_current else "    "
    line.append(prefix, style="bright_cyan" if is_current else "dim")
    line.append(f"{fld.label}", style="bold" if is_current else "")
    line.append(f"  ({fld.kind})", style="dim")
    line.append("  ")
    line.append_text(_render_value(fld, is_current))
    if fld.error:
        line.append(f"   ✗ {fld.error}", style="red")
    elif fld.doc and is_current:
        doc = fld.doc.replace("\n", " ")
        if len(doc) > 80:
            doc = doc[:77] + "…"
        line.append(f"   {doc}", style="dim")
    return line


def _render_value(fld: "ModuleFormField", is_current: bool) -> Text:
    out = Text()
    if fld.options:
        for opt in fld.options:
            if opt == fld.value:
                out.append(f"[{opt}]", style="cyan bold")
            else:
                out.append(f" {opt} ", style="dim")
        return out
    if fld.kind == "bool":
        for opt in ("true", "false"):
            if opt == fld.value:
                out.append(f"[{opt}]", style="cyan bold")
            else:
                out.append(f" {opt} ", style="dim")
        return out
    value = fld.value or ""
    if fld.kind in ("list", "dict") and value:
        # Multi-line values: render first line + count of remaining
        # lines so the form stays single-row per field. Full editing
        # still works (Backspace deletes char-by-char).
        lines = value.split("\n")
        display = lines[0][:40] + ("…" if len(lines[0]) > 40 else "")
        if len(lines) > 1:
            display += f"   (+{len(lines) - 1} line{'s' if len(lines) > 2 else ''})"
        out.append(display, style="cyan" if is_current else "")
    elif not value:
        out.append("(empty)", style="dim italic")
    else:
        out.append(value, style="cyan" if is_current else "")
    if is_current:
        out.append("█", style="cyan")
    return out


# ── Hint bar ────────────────────────────────────────────────────


def _render_hint(overlay: "ModulePicker") -> Text:
    hint = Text()
    if overlay.mode == "list":
        segments: list[tuple[str, str]] = [
            ("↑↓", "navigate"),
            ("tab", "switch tab"),
            ("enter", "edit"),
            ("t", "toggle"),
            ("esc", "close"),
        ]
    else:
        segments = [
            ("tab/↑↓", "field"),
            ("←→", "cycle / edit text"),
            ("enter", "next / save"),
            ("esc", "cancel"),
        ]
    for i, (k, label) in enumerate(segments):
        if i > 0:
            hint.append("  ")
        hint.append(k, style="cyan")
        hint.append(f" {label}", style="dim")
    return hint


# Re-export to silence unused-import lint (the typed import is for
# type-checking only; the renderer references `Any` indirectly via
# the Rich return types).
_ = Any
