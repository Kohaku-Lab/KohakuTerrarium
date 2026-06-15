"""prompt_toolkit startup picker for ``kt-cli`` (rich inline mode).

A small, self-contained full-screen ``Application`` shown *before* the
engine is built: it renders the :class:`~kohakuterrarium.cli.select.NavState`
as an expandable package→creature list, drives it from the keyboard, and
returns the chosen ``ref`` (or ``None`` if cancelled).  Rendering reuses
the same Rich→ANSI bridge the in-session ``ModelPicker`` uses, so the look
is consistent across the picker and the running CLI.

This module owns the prompt_toolkit dependency; the navigation logic lives
in the UI-free :mod:`.select` module and is unit-tested there.
"""

import shutil
from io import StringIO

from prompt_toolkit.application import Application
from prompt_toolkit.formatted_text import ANSI
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.keys import Keys
from prompt_toolkit.layout import Layout, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from rich.console import Console, Group
from rich.panel import Panel
from rich.text import Text

from kohakuterrarium.cli.select import NavState, RunnableGroup

_MIN_VISIBLE = 5
_CHROME_ROWS = 8  # title + filter + hint + borders + breathing room


def _viewport(total: int, cursor: int, height: int) -> tuple[int, int]:
    """Return (start, end) row indices keeping ``cursor`` on screen."""
    visible = max(_MIN_VISIBLE, height - _CHROME_ROWS)
    if total <= visible:
        return 0, total
    half = visible // 2
    start = max(0, min(cursor - half, total - visible))
    return start, start + visible


def _render_entry(runnable, selected: bool) -> Text:
    line = Text()
    line.append(
        "    › " if selected else "      ", style="bold bright_cyan" if selected else ""
    )
    name_style = "bold" if selected else ""
    if runnable.kind == "terrarium":
        line.append("[team] ", style="magenta")
    line.append(runnable.name, style=name_style)
    detail = runnable.description
    if runnable.kind == "terrarium" and runnable.members:
        members = ", ".join(runnable.members)
        detail = f"({members})" + (f"  {detail}" if detail else "")
    if detail:
        shown = detail if len(detail) <= 60 else detail[:57] + "…"
        line.append(f"   {shown}", style="dim")
    if runnable.model:
        line.append(f"   ·{runnable.model}", style="dim cyan")
    return line


def _render(state: NavState, width: int, height: int) -> str:
    rows = state.rows
    body: list = []
    if not rows:
        body.append(Text("  no matches", style="dim yellow"))
    else:
        start, end = _viewport(len(rows), state.cursor, height)
        if start > 0:
            body.append(Text(f"  ↑ {start} more", style="dim bright_black"))
        for i in range(start, end):
            row = rows[i]
            selected = i == state.cursor
            if row.kind == "header":
                marker = (
                    "▾"
                    if (row.source in state.expanded or state.filter.strip())
                    else "▸"
                )
                style = "bold bright_cyan" if selected else "bold magenta"
                body.append(Text(f" {marker} {row.source}", style=style))
            else:
                body.append(_render_entry(row.runnable, selected))
        if end < len(rows):
            body.append(Text(f"  ↓ {len(rows) - end} more", style="dim bright_black"))

    filter_line = Text()
    filter_line.append("  filter: ", style="dim")
    filter_line.append(
        state.filter or "(type to filter)", style="cyan" if state.filter else "dim"
    )

    hint = Text()
    for key, label in (
        ("↑↓", " move  "),
        ("←→", " fold  "),
        ("type", " filter  "),
        ("enter", " run  "),
        ("esc", " cancel"),
    ):
        hint.append(key, style="cyan")
        hint.append(label, style="dim")

    panel = Panel(
        Group(*body, Text(""), filter_line, hint),
        title=Text("Select an agent", style="bold magenta"),
        border_style="magenta",
        padding=(0, 1),
        expand=True,
    )
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


def _build_keybindings(state: NavState, app_ref: dict) -> KeyBindings:
    kb = KeyBindings()

    def _exit(result):
        app_ref["app"].exit(result=result)

    @kb.add("up")
    @kb.add("c-p")
    def _(event):
        state.move(-1)

    @kb.add("down")
    @kb.add("c-n")
    def _(event):
        state.move(1)

    @kb.add("pageup")
    def _(event):
        state.move(-8)

    @kb.add("pagedown")
    def _(event):
        state.move(8)

    @kb.add("left")
    def _(event):
        state.collapse()

    @kb.add("right")
    def _(event):
        state.expand()

    @kb.add("enter")
    def _(event):
        ref = state.toggle()
        if ref:
            _exit(ref)

    @kb.add("backspace")
    def _(event):
        state.backspace()

    @kb.add("escape")
    @kb.add("c-c")
    @kb.add("c-d")
    def _(event):
        _exit(None)

    @kb.add(Keys.Any)
    def _(event):
        data = event.data
        if data and len(data) == 1 and data.isprintable():
            state.add_char(data)

    return kb


def run_cli_picker(groups: list[RunnableGroup]) -> str | None:
    """Run the full-screen picker; return the chosen ``ref`` or ``None``."""
    state = NavState(groups=groups)
    app_ref: dict = {}

    def _text():
        size = shutil.get_terminal_size((80, 24))
        return ANSI(_render(state, size.columns, size.lines))

    body = Window(content=FormattedTextControl(_text, focusable=True))
    app: Application = Application(
        layout=Layout(body),
        key_bindings=_build_keybindings(state, app_ref),
        full_screen=True,
        mouse_support=False,
    )
    app_ref["app"] = app
    return app.run()
