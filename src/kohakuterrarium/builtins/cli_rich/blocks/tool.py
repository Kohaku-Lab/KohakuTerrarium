"""Tool / sub-agent call block — shows status, args, output preview.

Live form is truncated for compactness. ``to_committed()`` returns the
full content for scrollback. Tool blocks support nesting (sub-agent
children), background promotion, and language-aware syntax highlighting.
"""

import time

from rich.console import Group, RenderableType
from rich.padding import Padding
from rich.panel import Panel
from rich.syntax import Syntax
from rich.text import Text

from kohakuterrarium.builtins.cli_rich.theme import (
    COLOR_BG,
    COLOR_DONE,
    COLOR_ERROR,
    COLOR_RUNNING,
    COLOR_SUBAGENT_BORDER,
    COLOR_TOOL_BORDER,
    ICON_BG,
    ICON_DONE,
    ICON_ERROR,
    ICON_RUNNING,
    ICON_SUBAGENT,
)

# Aggressive defaults — Claude Code shows ~5-8 lines per tool block.
# Long output is still in the agent's conversation; the CLI just truncates
# the visual representation so scrollback doesn't get drowned out.
LIVE_PREVIEW_LINES = 5
COMMITTED_PREVIEW_LINES = 8

# Max child blocks rendered inside a sub-agent panel — anything older
# is collapsed into a "… N earlier" line.
LIVE_MAX_CHILDREN = 5
COMMITTED_MAX_CHILDREN = 12

# Tool name → syntax language for output highlighting
_TOOL_LANG_HINTS = {
    "bash": "bash",
    "shell": "bash",
    "python": "python",
    "py": "python",
    "read": None,  # let extension drive it
    "write": None,
    "edit": "diff",
    "multi_edit": "diff",
    "patch": "diff",
    "grep": None,
    "glob": None,
    "search": None,
}


def _truncate_lines(text: str, n: int) -> tuple[str, int]:
    """Return (truncated, total_lines)."""
    if not text:
        return "", 0
    lines = text.splitlines()
    total = len(lines)
    if total <= n:
        return text, total
    return "\n".join(lines[:n]), total


def _detect_lang(tool_name: str, content: str) -> str | None:
    """Best-effort language detection for syntax highlighting."""
    base = tool_name.split("[")[0].split(".")[-1].lower()
    if base in _TOOL_LANG_HINTS:
        hint = _TOOL_LANG_HINTS[base]
        if hint is not None:
            return hint
    # Look for shebang
    if content.startswith("#!"):
        first = content.splitlines()[0]
        if "python" in first:
            return "python"
        if "bash" in first or "sh" in first:
            return "bash"
    # Heuristic: looks like a unified diff
    if content.startswith(("---", "+++", "@@")):
        return "diff"
    return None


class ToolCallBlock:
    """A single tool or sub-agent call as a Rich Panel."""

    def __init__(
        self,
        job_id: str,
        name: str,
        args_preview: str = "",
        kind: str = "tool",  # "tool" or "subagent"
        parent_job_id: str = "",
    ):
        self.job_id = job_id
        self.name = name
        self.args_preview = args_preview
        self.kind = kind
        self.parent_job_id = parent_job_id
        self.status = "running"  # running | done | error
        self.output: str = ""
        self.error: str | None = None
        self.started_at = time.monotonic()
        self.finished_at: float | None = None
        self.is_background = False
        # Sub-agent metadata (final, set on done)
        self.tools_used: list[str] = []
        self.turns: int = 0
        self.total_tokens: int = 0
        self.prompt_tokens: int = 0
        self.completion_tokens: int = 0
        # Sub-agent running token tally (updated as it works)
        self.running_prompt_tokens: int = 0
        self.running_completion_tokens: int = 0
        self.running_total_tokens: int = 0
        # Children (sub-agent's nested tool blocks)
        self.children: list["ToolCallBlock"] = []

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
        elapsed_str = f" ({self.elapsed:.0f}s)" if self.elapsed >= 0.5 else ""
        bg_tag = " (bg)" if self.is_background else ""
        header = Text()
        header.append(f"{icon} ", style=color)
        header.append(f"{kind_glyph}{self.name}{bg_tag}", style="bold")
        if self.args_preview:
            header.append(f" {self.args_preview[:80]}", style="dim")
        if elapsed_str:
            header.append(elapsed_str, style="dim")
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
        """Render output with syntax highlighting if a language was detected."""
        body, total = _truncate_lines(content, max_lines)
        lang = _detect_lang(self.name, body)
        rendered: RenderableType
        if lang:
            try:
                rendered = Syntax(
                    body,
                    lang,
                    theme="ansi_dark",
                    background_color="default",
                    line_numbers=False,
                    word_wrap=True,
                )
            except Exception as e:
                _ = e  # fallback: syntax highlighting failed, render as plain text
                rendered = Text(body)
        else:
            rendered = Text(body)
        if total > max_lines:
            return Group(
                rendered,
                Text(f"  … ({total - max_lines} more lines)", style="dim"),
            )
        return rendered

    def _live_body(self) -> RenderableType | None:
        # Collapsed by default: tool calls show only the header line
        # (status icon + name + args + elapsed). Children of sub-agents
        # take over as the progress indicator.
        if self.children:
            return None
        if self.status == "running":
            if self.is_background:
                return Text("(running in background…)", style="dim")
            if self.is_subagent:
                return Text("(thinking…)", style="dim")
            return None
        if self.status == "error":
            return Text(self.error or "error", style=COLOR_ERROR)
        return None

    def _committed_body(self) -> RenderableType | None:
        # Errors always get the message.
        if self.status == "error":
            return Text(self.error or "error", style=COLOR_ERROR)
        # Sub-agents ALWAYS show their result body (with max height) so
        # the user can see what came back without having to read the
        # main agent's quoted summary. Background tool results too —
        # they arrive after the model moves on, so otherwise invisible.
        # Direct plain-tool results stay collapsed because the main
        # agent's reply integrates them.
        if (self.is_subagent or self.is_background) and self.output:
            return self._render_output(self.output, COMMITTED_PREVIEW_LINES)
        return None

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
        # Children of a sub-agent (parent_job_id set) render as a single
        # line, no panel — that's the compact list look inside sub-agents.
        if self.parent_job_id:
            return self._build_header()

        header = self._build_header()
        stats = self._build_subagent_stats_line()
        body = self._live_body()
        children = self._render_children()
        items: list[RenderableType] = [header]
        if stats is not None:
            items.append(stats)
        # Chronological: tool list FIRST (sub-agent ran them), then the
        # output body appears as they finish.
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

    def build_dispatch_notice(self) -> RenderableType:
        """Single-line notice committed when this block is dispatched
        in background. Mirrors a tool-call line but with a distinct
        icon/color so the user knows the agent ran in bg.
        """
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
        """Render full version for scrollback commit."""
        # Children of a sub-agent commit (with their parent) as a single
        # line — same as the live form.
        if self.parent_job_id:
            return self._build_header()

        header = self._build_header()
        body = self._committed_body()

        # Sub-agent metadata footer — turns / tools / in↑ out↓ tokens
        meta_line: Text | None = None
        if self.is_subagent and self.status == "done":
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
        # Chronological order: sub-agent called its tools FIRST, then
        # produced the output. Render as header → tool list → body → meta.
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

        return Panel(
            Group(*items),
            border_style=self._border_color(),
            padding=(0, 1),
            expand=True,
        )
