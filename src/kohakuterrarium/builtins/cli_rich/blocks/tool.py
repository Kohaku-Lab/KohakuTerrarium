"""Tool / sub-agent call block — shows status, args, output preview.

Live form is truncated for compactness. ``to_committed()`` returns the
full content for scrollback. Tool blocks support nesting (sub-agent
children), background promotion, and language-aware syntax highlighting.
"""

import time

from rich.console import Group, RenderableType
from rich.padding import Padding
from rich.panel import Panel
from rich.text import Text

from kohakuterrarium.builtins.cli_rich.blocks.tool_renderers import get_renderer
from kohakuterrarium.builtins.cli_rich.theme import (
    COLOR_BG,
    COLOR_DONE,
    COLOR_ERROR,
    COLOR_RUNNING,
    COLOR_SUBAGENT_BORDER,
    COLOR_TOOL_BORDER,
    GUTTER_GLYPH,
    GUTTER_INDENT,
    ICON_BG,
    ICON_DONE,
    ICON_ERROR,
    ICON_RUNNING,
    ICON_SUBAGENT,
    fmt_elapsed_compact,
)

# Preview limits preserve conversational scrollback without discarding agent context.
LIVE_PREVIEW_LINES = 5
COMMITTED_PREVIEW_LINES = 8

# Older child blocks collapse behind an earlier-count summary.
LIVE_MAX_CHILDREN = 5
COMMITTED_MAX_CHILDREN = 12

# Structured diff renderers already provide their own gutter.
_DIFF_TOOLS = {"edit", "multi_edit", "multiedit", "patch", "apply_patch"}

# None means full output, zero means header-only, and integers cap lines.
_TOOL_COMMIT_POLICY: dict[str, int | None] = {
    "edit": None,
    "multi_edit": None,
    "multiedit": None,
    "patch": None,
    "apply_patch": None,
    "read": 0,
    "view": 0,
    "cat": 0,
    "info": 0,
    "tree": 0,
    "bash": 8,
    "shell": 8,
    "sh": 8,
    "web_fetch": 8,
    "web_search": 8,
    "search_memory": 8,
    "stop_task": 8,
}


def _normalise_tool_name(name: str) -> str:
    """Strip namespace prefix + bracket id from a tool name."""
    base = name.split("[")[0].split(".")[-1]
    return base.replace("-", "_").lower()


def _commit_line_limit(tool_base: str, default: int) -> int | None:
    """Return a tool-specific commit line limit or the default."""
    return _TOOL_COMMIT_POLICY.get(tool_base, default)


class ToolCallBlock:
    """A single tool or sub-agent call as a Rich Panel."""

    def __init__(
        self,
        job_id: str,
        name: str,
        args_preview: str = "",
        kind: str = "tool",
        parent_job_id: str = "",
    ):
        self.job_id = job_id
        self.name = name
        self.args_preview = args_preview
        self.kind = kind
        self.parent_job_id = parent_job_id
        self.status = "running"
        self.output: str = ""
        self.error: str | None = None
        self.started_at = time.monotonic()
        self.finished_at: float | None = None
        self.is_background = False
        self.tools_used: list[str] = []
        self.turns: int = 0
        self.total_tokens: int = 0
        self.prompt_tokens: int = 0
        self.completion_tokens: int = 0
        self.running_prompt_tokens: int = 0
        self.running_completion_tokens: int = 0
        self.running_total_tokens: int = 0
        self.children: list["ToolCallBlock"] = []
        self.expanded: bool = False

    @property
    def is_subagent(self) -> bool:
        return self.kind == "subagent"

    @property
    def elapsed(self) -> float:
        end = self.finished_at if self.finished_at else time.monotonic()
        return end - self.started_at

    def add_child(self, child: "ToolCallBlock") -> None:
        self.children.append(child)

    def update_running_tokens(self, prompt: int, completion: int, total: int) -> None:
        if prompt:
            self.running_prompt_tokens = prompt
        if completion:
            self.running_completion_tokens = completion
        if total:
            self.running_total_tokens = total

    def set_done(self, output: str = "", **metadata) -> None:
        self.status = "done"
        self.output = output or ""
        self.finished_at = time.monotonic()
        if metadata:
            self.tools_used = metadata.get("tools_used", []) or []
            self.turns = metadata.get("turns", 0) or 0
            self.total_tokens = metadata.get("total_tokens", 0) or 0
            self.prompt_tokens = metadata.get("prompt_tokens", 0) or 0
            self.completion_tokens = metadata.get("completion_tokens", 0) or 0

    def set_error(self, error: str = "") -> None:
        self.status = "error"
        self.error = error or "unknown error"
        self.finished_at = time.monotonic()

    def promote_to_background(self) -> None:
        self.is_background = True

    def _icon(self) -> tuple[str, str]:
        if self.is_background and self.status == "running":
            return ICON_BG, COLOR_BG
        if self.status == "done":
            return ICON_DONE, COLOR_DONE
        if self.status == "error":
            return ICON_ERROR, COLOR_ERROR
        return ICON_RUNNING, COLOR_RUNNING

    def _border_color(self) -> str:
        if self.is_background:
            return COLOR_BG
        return COLOR_SUBAGENT_BORDER if self.is_subagent else COLOR_TOOL_BORDER

    def _build_header(self) -> Text:
        icon, color = self._icon()
        kind_glyph = f"{ICON_SUBAGENT} " if self.is_subagent else ""
        bg_tag = " (bg)" if self.is_background else ""
        header = Text()
        header.append(f"{icon} ", style=color)
        header.append(f"{kind_glyph}{self.name}{bg_tag}", style="bold")
        if self.args_preview:
            preview = self.args_preview
            if len(preview) > 80:
                preview = preview[:79] + "…"
            header.append(f" {preview}", style="dim")
        if self.elapsed >= 0.5:
            header.append(f"  {fmt_elapsed_compact(self.elapsed)}", style="dim")
        return header

    def _build_subagent_stats_line(self) -> Text | None:
        """Second line under sub-agent header: tools called · tokens · turns."""
        if not self.is_subagent or self.status != "running":
            return None
        parts: list[str] = []
        tools_called = len(self.children)
        if tools_called:
            parts.append(f"{tools_called} tools")
        if self.running_total_tokens:
            parts.append(
                f"{self.running_prompt_tokens}↑ {self.running_completion_tokens}↓"
            )
        if not parts:
            return None
        return Text("  " + "  ·  ".join(parts), style="dim")

    def _render_output(self, content: str, max_lines: int) -> RenderableType:
        """Render output through the tool-specific renderer registry."""
        renderer = get_renderer(self.name)
        try:
            return renderer(content, max_lines)
        except Exception:
            # Renderer failures must not hide tool output.
            return Text(content)

    def _wrap_body_with_gutter(
        self, body: str, max_lines: int
    ) -> RenderableType | None:
        """Wrap plain output with one leading gutter and aligned continuations."""
        if not body:
            return None
        lines = body.splitlines()
        if not lines:
            return None
        total = len(lines)
        visible = lines[:max_lines]

        text = Text()
        first = True
        for line in visible:
            if first:
                text.append(GUTTER_GLYPH, style="bright_black")
                first = False
            else:
                text.append("\n")
                text.append(GUTTER_INDENT, style="")
            text.append(line)

        if total > max_lines:
            remaining = total - max_lines
            text.append("\n")
            text.append(GUTTER_INDENT, style="")
            text.append(f"… +{remaining} more lines", style="dim")
        return text

    def _summary_hint(self) -> str | None:
        """Return an optional metadata summary for empty output."""
        return None

    def _live_body(self) -> RenderableType | None:
        if self.children and not self.expanded:
            return None
        if self.status == "running":
            if self.is_background:
                return Text("(running in background…)", style="dim")
            if self.is_subagent:
                return Text("(thinking…)", style="dim")
            return None
        if self.status == "error":
            return Text(self.error or "error", style=COLOR_ERROR)
        if self.expanded and self.output:
            return self._render_output(self.output, 999)
        return None

    def _committed_body(self) -> RenderableType | None:
        if self.status == "error":
            error_text = Text()
            error_text.append(GUTTER_GLYPH, style="bright_black")
            error_text.append(self.error or "error", style=COLOR_ERROR)
            return error_text

        base = _normalise_tool_name(self.name)
        limit = _commit_line_limit(base, COMMITTED_PREVIEW_LINES)

        if limit == 0:
            return None

        if not self.output:
            hint = self._summary_hint()
            if hint:
                t = Text()
                t.append(GUTTER_GLYPH, style="bright_black")
                t.append(hint, style="dim")
                return t
            return None

        max_lines = 9_999_999 if limit is None else limit

        if base in _DIFF_TOOLS:
            return self._render_output(self.output, max_lines)
        return self._wrap_body_with_gutter(self.output, max_lines)

    def _render_children(
        self, max_visible: int = LIVE_MAX_CHILDREN
    ) -> RenderableType | None:
        """Render children indented, capped at ``max_visible`` most recent."""
        if not self.children:
            return None
        items: list[RenderableType] = []
        total = len(self.children)
        if total > max_visible:
            hidden = total - max_visible
            items.append(Text(f"… {hidden} earlier", style="dim"))
            visible = self.children[-max_visible:]
        else:
            visible = self.children
        for child in visible:
            items.append(child)
        return Padding(Group(*items), (0, 0, 0, 2))

    def __rich__(self) -> RenderableType:
        if self.parent_job_id:
            return self._build_header()

        header = self._build_header()
        stats = self._build_subagent_stats_line()
        body = self._live_body()
        children = self._render_children()
        items: list[RenderableType] = [header]
        if stats is not None:
            items.append(stats)
        if children is not None:
            items.append(Text(""))
            items.append(children)
        if body is not None:
            items.append(Text(""))
            items.append(body)
        content: RenderableType = Group(*items) if len(items) > 1 else header
        return Panel(
            content,
            border_style=self._border_color(),
            padding=(0, 1),
            expand=True,
        )

    def build_compact_line(self) -> Text:
        """Render a background job as one compact status line."""
        icon, color = self._icon()
        line = Text()
        line.append(f"  {icon} ", style=color)
        line.append(self.name, style="bold")
        if self.args_preview:
            line.append(f" {self.args_preview[:60]}", style="dim")
        if self.elapsed >= 0.5:
            secs = int(self.elapsed)
            line.append(f"  {secs // 60:02d}:{secs % 60:02d}", style="dim")
        return line

    def build_dispatch_notice(self) -> RenderableType:
        """Render a notice when a job is dispatched in the background."""
        kind_glyph = f"{ICON_SUBAGENT} " if self.is_subagent else ""
        line = Text()
        line.append(f"{ICON_BG} ", style=COLOR_BG)
        line.append("dispatched ", style="dim")
        line.append(f"{kind_glyph}{self.name}", style="bold")
        line.append(" in background", style="dim")
        if self.args_preview:
            line.append(f"\n  {self.args_preview[:200]}", style="dim")
        return Panel(
            line,
            border_style=COLOR_BG,
            padding=(0, 1),
            expand=True,
        )

    def to_committed(self) -> RenderableType:
        """Render the appropriate committed child, tool, or sub-agent shape."""
        if self.parent_job_id:
            return self._build_header()
        if self.is_subagent:
            return self._to_committed_subagent()
        return self._to_committed_direct()

    def _to_committed_direct(self) -> RenderableType:
        """Render direct-tool inner content; the committer owns outer rules."""
        header = self._build_header()
        body = self._committed_body()
        if body is None:
            return header
        return Group(header, body)

    def _to_committed_subagent(self) -> RenderableType:
        """Render sub-agent header, children, output, and metadata."""
        header = self._build_header()
        body = self._committed_body()

        meta_line: Text | None = None
        if self.status == "done":
            meta_parts = []
            if self.turns:
                meta_parts.append(f"{self.turns} turns")
            if self.tools_used:
                meta_parts.append(f"tools: {', '.join(self.tools_used[:5])}")
            if self.prompt_tokens or self.completion_tokens:
                meta_parts.append(
                    f"{self.prompt_tokens}↑ {self.completion_tokens}↓ tokens"
                )
            elif self.total_tokens:
                meta_parts.append(f"{self.total_tokens} tokens")
            if meta_parts:
                meta_line = Text("  " + "  ·  ".join(meta_parts), style="dim")

        items: list[RenderableType] = [header]
        children = self._render_children(max_visible=COMMITTED_MAX_CHILDREN)
        if children is not None:
            items.append(Text(""))
            items.append(children)
        if body is not None:
            items.append(Text(""))
            items.append(body)
        if meta_line is not None:
            items.append(Text(""))
            items.append(meta_line)
        return Group(*items)
