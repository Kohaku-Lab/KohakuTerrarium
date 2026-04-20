"""Per-tool output renderers for ``ToolCallBlock``.

Each tool category (edit / read / bash / grep / glob / write / …) gets
a specialised renderer that picks the right lexer, strips noise, shows
filenames as headers, formats tool-specific meta, etc.

The registry is consulted by ``ToolCallBlock._render_output()``. A tool
whose name doesn't match any entry falls through to ``TextRenderer``
which just returns the raw body as Rich Text.

Lookups are normalised — ``multi_edit``, ``multi-edit``, ``MultiEdit``
and ``MyAgent.multi_edit`` all route to the same renderer.
"""

from typing import Protocol

from rich.console import Group, RenderableType
from rich.syntax import Syntax
from rich.text import Text

from kohakuterrarium.builtins.cli_rich.blocks.diff import render_unified_diff


class ToolRenderer(Protocol):
    """Callable that turns raw tool output into a Rich renderable."""

    def __call__(self, body: str, max_lines: int) -> RenderableType: ...


def _truncate(body: str, max_lines: int) -> tuple[str, int]:
    """Return the body trimmed to ``max_lines`` + the overflow count."""
    if not body:
        return "", 0
    lines = body.splitlines()
    total = len(lines)
    if total <= max_lines:
        return body, 0
    return "\n".join(lines[:max_lines]), total - max_lines


def _append_truncation_notice(
    rendered: RenderableType, overflow: int
) -> RenderableType:
    if overflow <= 0:
        return rendered
    return Group(rendered, Text(f"  … ({overflow} more lines)", style="dim"))


def _render_syntax(body: str, lexer: str) -> RenderableType:
    try:
        return Syntax(
            body,
            lexer,
            theme="ansi_dark",
            background_color="default",
            line_numbers=False,
            word_wrap=True,
        )
    except Exception:
        return Text(body)


# ── Specific renderers ────────────────────────────────────────────────


def render_diff(body: str, max_lines: int) -> RenderableType:
    """edit / multi_edit / patch: unified diff with gutter + highlight."""
    return render_unified_diff(body, max_lines=max_lines)


def render_bash(body: str, max_lines: int) -> RenderableType:
    """bash / shell: stdout as shell-highlighted text with truncation."""
    trimmed, overflow = _truncate(body, max_lines)
    return _append_truncation_notice(_render_syntax(trimmed, "bash"), overflow)


def render_python(body: str, max_lines: int) -> RenderableType:
    """Inline Python output (usually ``python`` tool)."""
    trimmed, overflow = _truncate(body, max_lines)
    return _append_truncation_notice(_render_syntax(trimmed, "python"), overflow)


def render_read(body: str, max_lines: int) -> RenderableType:
    """read: file content preview. First line commonly has a path hint
    ("File: /abs/path") — we keep it verbatim and try python as a
    reasonable default when no hint is present."""
    trimmed, overflow = _truncate(body, max_lines)
    # No filename available here — let the block header supply path info.
    return _append_truncation_notice(Text(trimmed), overflow)


def render_grep(body: str, max_lines: int) -> RenderableType:
    """grep: one-match-per-line, format like `path:line:match`.

    We pass through as styled Text. Highlighting individual matches
    would need the search pattern, which the renderer doesn't have.
    """
    trimmed, overflow = _truncate(body, max_lines)
    t = Text()
    for ln in trimmed.splitlines():
        # ``path:line:rest`` convention (rg / grep -n)
        first, sep, rest = ln.partition(":")
        second, sep2, tail = rest.partition(":")
        if sep and sep2 and second.isdigit():
            t.append(first, style="cyan")
            t.append(":", style="dim")
            t.append(second, style="yellow")
            t.append(":", style="dim")
            t.append(tail + "\n")
        else:
            t.append(ln + "\n")
    return _append_truncation_notice(t, overflow)


def render_glob(body: str, max_lines: int) -> RenderableType:
    """glob: one path per line, shown dim."""
    trimmed, overflow = _truncate(body, max_lines)
    return _append_truncation_notice(Text(trimmed, style="cyan"), overflow)


def render_write(body: str, max_lines: int) -> RenderableType:
    """write: usually terse confirmation + first N lines written."""
    trimmed, overflow = _truncate(body, max_lines)
    return _append_truncation_notice(Text(trimmed), overflow)


def render_plain(body: str, max_lines: int) -> RenderableType:
    """Fallback: raw text with truncation."""
    trimmed, overflow = _truncate(body, max_lines)
    return _append_truncation_notice(Text(trimmed), overflow)


# ── Registry ──────────────────────────────────────────────────────────

_TOOL_RENDERERS: dict[str, ToolRenderer] = {
    # file edit family → unified-diff view
    "edit": render_diff,
    "multi_edit": render_diff,
    "multiedit": render_diff,
    "patch": render_diff,
    "apply_patch": render_diff,
    # shell / python
    "bash": render_bash,
    "shell": render_bash,
    "sh": render_bash,
    "python": render_python,
    "py": render_python,
    # read / grep / glob / write
    "read": render_read,
    "view": render_read,
    "cat": render_read,
    "grep": render_grep,
    "search": render_grep,
    "ripgrep": render_grep,
    "rg": render_grep,
    "glob": render_glob,
    "tree": render_glob,
    "find": render_glob,
    "ls": render_glob,
    "write": render_write,
    "create": render_write,
}


def _normalise(tool_name: str) -> str:
    # Strip namespace prefixes ("my.tools.bash" → "bash"), bracket
    # parameters ("bash[cwd=/tmp]" → "bash"), and normalise separators.
    base = tool_name.split("[")[0].split(".")[-1]
    return base.replace("-", "_").lower()


def get_renderer(tool_name: str) -> ToolRenderer:
    """Resolve a renderer for a tool name, falling back to plain text."""
    return _TOOL_RENDERERS.get(_normalise(tool_name), render_plain)
