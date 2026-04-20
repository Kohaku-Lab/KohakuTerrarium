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

# Aggressive defaults — Claude Code shows ~5-8 lines per tool block.
# Long output is still in the agent's conversation; the CLI just truncates
# the visual representation so scrollback doesn't get drowned out.
LIVE_PREVIEW_LINES = 5
COMMITTED_PREVIEW_LINES = 8

# Max child blocks rendered inside a sub-agent panel — anything older
# is collapsed into a "… N earlier" line.
LIVE_MAX_CHILDREN = 5
COMMITTED_MAX_CHILDREN = 12

# Tool-name → lexer / renderer routing now lives in ``tool_renderers``.
# This module just delegates via ``get_renderer(tool_name)``.

# Tools whose output is already a structured diff — the diff renderer
# has its own gutter + filename header so we do NOT wrap it in the
# plain ``⎿ `` gutter, which would double up.
_DIFF_TOOLS = {"edit", "multi_edit", "multiedit", "patch", "apply_patch"}

# Per-tool commit-body line policy.
#
#   None  → unlimited, render full body (no overflow hint)
#   0     → never render a body (header only)
#   int N → cap at N lines, overflow hint below
#
# Tools not listed fall back to ``COMMITTED_PREVIEW_LINES`` (8).
#
# Guiding principle (USER-centric, not LLM-centric):
#
#   Ask "does the user have this content outside the CLI?"
#     - YES, trivially (they can open the file, they see the tree) → 0
#     - NO, it's external / internal-state / a change → a few lines
#     - NO, and it's STRUCTURED where truncation destroys meaning → None
#
# Users are blind to what the LLM saw or generated. We surface what
# they'd otherwise miss; we skip what they could trivially verify.
_TOOL_COMMIT_POLICY: dict[str, int | None] = {
    # Diffs — structured; truncating a diff hides real changes. Users
    # have no other way to know what was modified. Show everything.
    "edit": None,
    "multi_edit": None,
    "multiedit": None,
    "patch": None,
    "apply_patch": None,
    # Content the user can open themselves — a file path, a directory
    # listing, a tool's own docs. Panel would repeat what they could
    # just `cat` / `ls`. Header + ✓ tells them it happened.
    "read": 0,
    "view": 0,
    "cat": 0,
    "info": 0,
    "tree": 0,
    # External / hidden-state tools: user can't easily see this content
    # themselves, so we show a few lines. Default 8 is a readable
    # snippet without burying the conversation.
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
    """Resolve the commit-body line cap for *tool_base*.

    Unknown tools use *default* (``COMMITTED_PREVIEW_LINES``). The
    policy table above is intentionally small — we only add an entry
    when a tool's reading behaviour differs meaningfully from the
    default. Most tools are fine at 8 lines.
    """
    return _TOOL_COMMIT_POLICY.get(tool_base, default)


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
        # When True, the live view skips the preview truncation and
        # renders the full output. Toggled by Ctrl+O from the composer.
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
        """Render output via the per-tool renderer registry.

        The registry routes edit / multi_edit / patch through the unified
        diff renderer (filename header, gutter, sign column, syntax
        highlight), bash / python through language-aware Syntax, grep /
        glob through match-formatted text, and everything else through a
        plain-text fallback. See ``blocks.tool_renderers``.
        """
        renderer = get_renderer(self.name)
        try:
            return renderer(content, max_lines)
        except Exception:
            # Rendering must never raise into the live region — fall back
            # to plain text so the user still sees the output.
            return Text(content)

    def _wrap_body_with_gutter(
        self, body: str, max_lines: int
    ) -> RenderableType | None:
        """Wrap a plain-text body with the claude-code-style ``⎿`` gutter.

        Shape::

            ⎿  first line of output
               second line
               third line
               … +N more lines

        One ``⎿ `` glyph at the start of the body block, continuation
        lines indent to match the content column. Produces a single
        Text renderable — Rich layouts it as multiple lines. This is
        only used for plain-text tool bodies (bash, read, grep, etc.);
        structured renderers (diff) keep their own inner layout.
        """
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
        """Return a one-line metadata hint for tools with no text output.

        e.g. a successful `write` tool produces no stdout but we want
        the user to know it did something — show ``(wrote N lines)``.
        """
        # These hints live in tool result metadata; we don't have that
        # wired here yet, so we only return one for the most common
        # case: known-empty-output tools that we nevertheless want to
        # acknowledge. Extend as needed.
        return None

    def _live_body(self) -> RenderableType | None:
        # Collapsed by default: tool calls show only the header line
        # (status icon + name + args + elapsed). Children of sub-agents
        # take over as the progress indicator.
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
        # When expanded and we have output already, show it even in the
        # live view (normally live view keeps tool bodies collapsed).
        if self.expanded and self.output:
            # 999 lines ≈ "no truncation" for any realistic tool output.
            return self._render_output(self.output, 999)
        return None

    def _committed_body(self) -> RenderableType | None:
        # Errors always get the message, regardless of policy.
        if self.status == "error":
            error_text = Text()
            error_text.append(GUTTER_GLYPH, style="bright_black")
            error_text.append(self.error or "error", style=COLOR_ERROR)
            return error_text

        base = _normalise_tool_name(self.name)
        limit = _commit_line_limit(base, COMMITTED_PREVIEW_LINES)

        # Policy == 0 → header-only, no body at all. Used for tools
        # whose content the LLM narrates (read/info/tree) — repeating
        # the content in a panel is just noise.
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

        # Policy == None → unlimited. We pick a very large cap so the
        # truncation-notice branch in the renderer / gutter helper is
        # effectively disabled.
        max_lines = 9_999_999 if limit is None else limit

        # Structured diff tools keep their own gutter; plain-text tools
        # get wrapped with a single ``⎿ `` gutter glyph at the top of
        # the body + indented continuation lines.
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

    def build_compact_line(self) -> Text:
        """One-line compact rendering for the background strip.

        Omits the panel and body — just the status glyph, name, args
        preview, and elapsed time. Multiple backgrounded tools stack
        vertically inside the strip.
        """
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
        """Render full version for scrollback commit.

        Three shapes:

        - **Sub-agent child line** — one-liner, no panel.
        - **Sub-agent panel** — full Panel with children list + summary
          body + meta footer. Defer to ``_to_committed_subagent``.
        - **Direct tool panel** — Panel wrapping a header + ``⎿`` gutter
          body. No internal blank row (the gutter is the separator).
          Defer to ``_to_committed_direct``.
        """
        if self.parent_job_id:
            return self._build_header()
        if self.is_subagent:
            return self._to_committed_subagent()
        return self._to_committed_direct()

    def _to_committed_direct(self) -> RenderableType:
        """Header + ``⎿`` gutter body — no Panel, no rules.

        The committer owns the top/bottom rules around tool/sub-agent
        blocks now (so consecutive blocks can share a single rule
        between them and save vertical space). The block itself just
        produces its inner content.
        """
        header = self._build_header()
        body = self._committed_body()
        if body is None:
            return header
        return Group(header, body)

    def _to_committed_subagent(self) -> RenderableType:
        """Sub-agent committed content: header + children + body + meta.

        No surrounding rules — the committer adds them, sharing with
        adjacent tool/sub-agent commits where possible. See
        ``_to_committed_direct`` for the rationale.
        """
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
