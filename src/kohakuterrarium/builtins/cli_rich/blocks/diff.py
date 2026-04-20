"""Unified-diff renderer for tool outputs.

Used by edit / multi_edit / patch tool renderers in
``blocks.tool_renderers``. Parses a unified-diff text into per-file
hunks, then renders each line with:

  - left gutter of right-aligned line numbers (old for `-` / context,
    new for `+` / context)
  - sign column (``+`` / ``-`` / space)
  - syntax-highlighted content (language guessed from the filename
    extension — falls back to plain text)

Colors:
  - ``+`` lines: green content, green sign
  - ``-`` lines: red content, red sign
  - context:   dim foreground
  - hunk headers (``@@ … @@``): cyan

Truncation behaviour: if the total rendered line count exceeds
``max_lines``, the renderer cuts at a hunk boundary where possible and
appends ``… (N more lines)`` at the end.
"""

import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from rich.console import Group, RenderableType
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text


@dataclass
class Hunk:
    """One @@ block of a unified diff."""

    old_start: int = 0
    old_count: int = 0
    new_start: int = 0
    new_count: int = 0
    heading: str = ""
    lines: list[tuple[str, str]] = field(default_factory=list)  # (sign, content)


@dataclass
class FileDiff:
    """All hunks that touched a single file."""

    old_path: str = ""
    new_path: str = ""
    hunks: list[Hunk] = field(default_factory=list)

    @property
    def display_name(self) -> str:
        # Prefer the new path; strip leading "a/" or "b/" git prefixes
        # and any "/dev/null" sentinels.
        for candidate in (self.new_path, self.old_path):
            if not candidate or candidate == "/dev/null":
                continue
            name = candidate
            if name.startswith(("a/", "b/")):
                name = name[2:]
            return name
        return "(unknown)"


_HUNK_HEADER_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)$")


def parse_unified_diff(text: str) -> list[FileDiff]:
    """Parse a unified diff into per-file FileDiff objects.

    Tolerant of missing ``---`` / ``+++`` headers (produces a single
    FileDiff with empty paths). Unknown / malformed lines are skipped.
    """
    files: list[FileDiff] = []
    current_file: FileDiff | None = None
    current_hunk: Hunk | None = None

    def _ensure_file() -> FileDiff:
        nonlocal current_file
        if current_file is None:
            current_file = FileDiff()
            files.append(current_file)
        return current_file

    for raw in text.splitlines():
        if raw.startswith("--- "):
            # New file starts — close previous hunk, flush previous file.
            current_file = FileDiff(old_path=raw[4:].strip())
            current_hunk = None
            files.append(current_file)
            continue
        if raw.startswith("+++ "):
            f = _ensure_file()
            f.new_path = raw[4:].strip()
            current_hunk = None
            continue
        if raw.startswith("@@"):
            match = _HUNK_HEADER_RE.match(raw)
            if not match:
                continue
            old_start, old_count, new_start, new_count, heading = match.groups()
            hunk = Hunk(
                old_start=int(old_start),
                old_count=int(old_count or 1),
                new_start=int(new_start),
                new_count=int(new_count or 1),
                heading=heading.strip(),
            )
            _ensure_file().hunks.append(hunk)
            current_hunk = hunk
            continue
        if raw.startswith(
            (
                "index ",
                "diff --git",
                "similarity ",
                "rename ",
                "new file",
                "deleted file",
                "Binary files",
            )
        ):
            # Git-specific metadata we don't render.
            continue
        if current_hunk is None:
            # Hunk body can't start without a header — ignore.
            continue
        if raw.startswith("+"):
            current_hunk.lines.append(("+", raw[1:]))
        elif raw.startswith("-"):
            current_hunk.lines.append(("-", raw[1:]))
        elif raw.startswith(" "):
            current_hunk.lines.append((" ", raw[1:]))
        elif raw == "":
            # Blank line inside a hunk — treat as context.
            current_hunk.lines.append((" ", ""))
        # Anything else (e.g. "\ No newline at end of file") is dropped.

    return files


def _guess_lexer(filename: str) -> str:
    """Map a filename to a Pygments / Rich Syntax lexer name."""
    if not filename:
        return "text"
    suffix = PurePosixPath(filename).suffix.lower().lstrip(".")
    table = {
        "py": "python",
        "pyi": "python",
        "ts": "typescript",
        "tsx": "tsx",
        "js": "javascript",
        "jsx": "jsx",
        "md": "markdown",
        "rs": "rust",
        "go": "go",
        "java": "java",
        "c": "c",
        "h": "c",
        "cpp": "cpp",
        "hpp": "cpp",
        "cc": "cpp",
        "rb": "ruby",
        "sh": "bash",
        "zsh": "bash",
        "bash": "bash",
        "fish": "fish",
        "toml": "toml",
        "yaml": "yaml",
        "yml": "yaml",
        "json": "json",
        "html": "html",
        "css": "css",
        "scss": "scss",
        "sql": "sql",
        "xml": "xml",
        "vue": "html",
    }
    return table.get(suffix, "text")


def _sign_style(sign: str) -> str:
    if sign == "+":
        return "green"
    if sign == "-":
        return "red"
    return "bright_black"


def _highlight_line(content: str, lexer: str, sign: str) -> Text:
    """Apply syntax highlighting to a single diff body line.

    For a ``+`` / ``-`` line we overlay the sign color as a foreground
    tint on top of whatever the syntax highlighter produced, so additions
    read as "green Python" and deletions as "red Python". Pure-context
    lines are dimmed.
    """
    # Strip the trailing newline the parser preserved on the raw line.
    body = content.rstrip("\n")
    if sign == " ":
        return Text(body, style="bright_black")
    try:
        syntax = Syntax(
            body,
            lexer,
            theme="ansi_dark",
            background_color="default",
            line_numbers=False,
            word_wrap=False,
            code_width=None,
        )
        # Syntax.highlight() ALWAYS appends a trailing newline to the
        # returned Text (it treats the body as a source-code string
        # with an implicit newline at EOF). Inside a Table cell that
        # trailing \n renders as an extra empty line per diff row,
        # which looks like "blank line between every line of the diff"
        # — exactly the bug the user reported. Strip it.
        highlighted = syntax.highlight(body)
        while highlighted.plain.endswith("\n"):
            highlighted = highlighted[:-1]
        # Tint the whole line with the sign color (green/red) while
        # keeping the syntax colors intact under it.
        out = Text()
        out.append(highlighted)
        out.stylize(_sign_style(sign))
        return out
    except Exception:
        return Text(body, style=_sign_style(sign))


def _render_hunk(hunk: Hunk, lexer: str, gutter_width: int) -> RenderableType:
    """Render a single hunk as a Rich Table.

    Columns:
      0. line number (old or new, depending on sign), right-aligned
      1. sign column (``+`` / ``-`` / space)
      2. content
    """
    table = Table.grid(padding=(0, 1))
    table.add_column(width=gutter_width, justify="right", style="bright_black")
    table.add_column(width=1)
    table.add_column(overflow="fold")

    # Hunk heading row — shown once at top of every hunk.
    header = Text()
    header.append(
        f"@@ -{hunk.old_start},{hunk.old_count} "
        f"+{hunk.new_start},{hunk.new_count} @@",
        style="cyan",
    )
    if hunk.heading:
        header.append(f"  {hunk.heading}", style="dim cyan")
    table.add_row("", Text(""), header)

    old_line = hunk.old_start
    new_line = hunk.new_start
    for sign, body in hunk.lines:
        if sign == "+":
            line_no = new_line
            new_line += 1
        elif sign == "-":
            line_no = old_line
            old_line += 1
        else:
            line_no = new_line
            old_line += 1
            new_line += 1
        sign_cell = Text(sign, style=_sign_style(sign))
        content_cell = _highlight_line(body, lexer, sign)
        table.add_row(str(line_no), sign_cell, content_cell)

    return table


def _gutter_width(files: list[FileDiff]) -> int:
    """Compute a shared right-aligned gutter width wide enough for the
    largest line number across all hunks.
    """
    largest = 1
    for f in files:
        for h in f.hunks:
            largest = max(largest, h.old_start + h.old_count, h.new_start + h.new_count)
    return max(2, len(str(largest)))


def render_unified_diff(
    text: str,
    max_lines: int = 40,
    default_filename: str = "",
) -> RenderableType:
    """Public entry point — parse and render a unified diff to Rich.

    Returns a ``Group`` containing one ``Table`` per file, separated by
    the file's display name as a bold magenta header. If more than
    ``max_lines`` body rows would be rendered, the output is truncated
    at the next hunk boundary and a dim ``… (N more lines)`` footer is
    appended. ``default_filename`` is used when the diff has no
    ``---``/``+++`` header — this happens when a tool emits just a
    standalone hunk.
    """
    files = parse_unified_diff(text)
    if not files:
        # Nothing parsable — fall back to plain text rendering so we
        # never drop the raw body entirely.
        return Text(text.rstrip("\n"))

    gutter = _gutter_width(files)
    rendered: list[RenderableType] = []
    lines_used = 0
    total_body_lines = sum(len(h.lines) + 1 for f in files for h in f.hunks)

    for file_diff in files:
        if lines_used >= max_lines:
            break
        name = file_diff.display_name or default_filename or "(inline)"
        lexer = _guess_lexer(name)
        rendered.append(Text(f"📝 {name}", style="bold magenta"))
        lines_used += 1
        for hunk in file_diff.hunks:
            hunk_lines = len(hunk.lines) + 1  # +1 for the @@ header row
            if lines_used + hunk_lines > max_lines:
                break
            rendered.append(_render_hunk(hunk, lexer, gutter))
            lines_used += hunk_lines

    if lines_used < total_body_lines:
        remaining = total_body_lines - lines_used
        rendered.append(Text(f"  … ({remaining} more lines)", style="dim"))

    return Group(*rendered)
