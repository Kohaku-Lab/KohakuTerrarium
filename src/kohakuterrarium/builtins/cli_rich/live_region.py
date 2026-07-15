"""Live region — state holder for the streaming chat region.

Holds:
  - Optional compaction banner (top)
  - The currently streaming assistant message
  - Active top-level tool blocks (sub-agents nest their children inline)
  - Background-promoted tasks (compact strip)

Renders to ANSI text via ``to_ansi(width)`` so the prompt_toolkit
``FormattedTextControl`` in the Application layout can display it.
"""

import time
from io import StringIO

from rich.console import Console, Group, RenderableType
from rich.panel import Panel
from rich.text import Text

from kohakuterrarium.builtins.cli_rich.blocks.footer import FooterBlock
from kohakuterrarium.builtins.cli_rich.blocks.message import AssistantMessageBlock
from kohakuterrarium.builtins.cli_rich.blocks.tool import ToolCallBlock
from kohakuterrarium.builtins.cli_rich.theme import (
    COLOR_AI,
    COLOR_BG,
    COLOR_COMPACT_BANNER,
    COLOR_USER,
    ICON_BG,
    ICON_COMPACT,
    ICON_USER,
    THINKING_LABEL,
    spinner_frame,
)


def render_to_ansi(renderable: RenderableType, width: int) -> str:
    """Render a Rich renderable to an ANSI-colored string."""
    buf = StringIO()
    console = Console(
        file=buf,
        force_terminal=True,
        color_system="truecolor",
        width=max(20, width),
        legacy_windows=False,
        soft_wrap=False,
        emoji=False,
    )
    console.print(renderable, end="")
    return buf.getvalue()


class LiveRegion:
    """Manages the live (re-rendered) region above the prompt."""

    def __init__(self):
        self.assistant_msg: AssistantMessageBlock | None = None
        self.tool_blocks: dict[str, ToolCallBlock] = {}
        # Background promotion must not reorder the live region.
        self._top_order: list[str] = []
        self.footer = FooterBlock()
        self._compacting = False
        # Per-call and per-turn flags keep activity visible between LLM calls.
        self._active = False
        self._turn_active = False
        self._active_started_at: float = 0.0
        self._sa_child_counter = 0
        # Injected inputs remain visible here until acknowledged by the agent.
        self._queued_inputs: list[str] = []

    def start_message(self) -> None:
        if self.assistant_msg is None or self.assistant_msg._finished:
            self.assistant_msg = AssistantMessageBlock()
        self.set_active(True)

    def append_chunk(self, chunk: str) -> None:
        if self.assistant_msg is None:
            self.start_message()
        if self.assistant_msg is not None:
            self.assistant_msg.append(chunk)

    def finish_message(self) -> RenderableType | None:
        """Finish the current assistant message and return its committed form."""
        self.set_active(False)
        if self.assistant_msg is None:
            return None
        if self.assistant_msg.is_empty:
            self.assistant_msg = None
            return None
        committed = self.assistant_msg.to_committed()
        self.assistant_msg.finish()
        self.assistant_msg = None
        return committed

    def set_active(self, value: bool) -> None:
        # Do not reset elapsed time between LLM calls in the same turn.
        if value and not self._active and not self._turn_active:
            self._active_started_at = time.monotonic()
        self._active = value

    # External callers still use the legacy name.
    def set_thinking(self, value: bool) -> None:
        self.set_active(value)

    def add_tool(
        self,
        job_id: str,
        name: str,
        args_preview: str = "",
        kind: str = "tool",
        parent_job_id: str = "",
    ) -> None:
        if not job_id:
            job_id = name  # Preserve a stable lookup key when metadata omits an id.

        # Duplicate delivery may add metadata but must not create another panel.
        existing = self.tool_blocks.get(job_id)
        if existing is not None:
            if args_preview:
                existing.args_preview = args_preview
            if name and not existing.name:
                existing.name = name
            return

        block = ToolCallBlock(
            job_id=job_id,
            name=name,
            args_preview=args_preview,
            kind=kind,
            parent_job_id=parent_job_id,
        )
        self.tool_blocks[job_id] = block

        parent = self.tool_blocks.get(parent_job_id) if parent_job_id else None
        if parent is not None:
            parent.add_child(block)
        else:
            self._top_order.append(job_id)

    def update_tool_done(
        self, job_id: str, output: str = "", **metadata
    ) -> RenderableType | None:
        block = self.tool_blocks.get(job_id)
        if block is None:
            return None
        block.set_done(output, **metadata)
        return self._finalize_block(block)

    def update_tool_error(self, job_id: str, error: str = "") -> RenderableType | None:
        block = self.tool_blocks.get(job_id)
        if block is None:
            return None
        block.set_error(error)
        return self._finalize_block(block)

    def _finalize_block(self, block: ToolCallBlock) -> RenderableType | None:
        """Finalize a top-level block while leaving children for parent commit."""
        job_id = block.job_id

        if block.parent_job_id and block.parent_job_id in self.tool_blocks:
            return None

        if job_id in self._top_order:
            self._top_order.remove(job_id)
        self._drop_subtree(block)
        return block.to_committed()

    def _drop_subtree(self, block: ToolCallBlock) -> None:
        """Remove block and all its descendants from tool_blocks."""
        for child in block.children:
            self._drop_subtree(child)
        self.tool_blocks.pop(block.job_id, None)

    def add_subagent_tool(
        self, parent_id: str, tool_name: str, args_preview: str = ""
    ) -> str | None:
        """Add a child tool block under a sub-agent's parent block.

        Returns the synthetic child id, or None if the parent doesn't exist.
        """
        parent = self.tool_blocks.get(parent_id)
        if parent is None:
            return None
        self._sa_child_counter += 1
        child_id = f"{parent_id}::sub::{tool_name}::{self._sa_child_counter}"
        child = ToolCallBlock(
            job_id=child_id,
            name=tool_name,
            args_preview=args_preview,
            kind="tool",
            parent_job_id=parent_id,
        )
        self.tool_blocks[child_id] = child
        parent.add_child(child)
        return child_id

    def _find_open_subagent_tool(
        self, parent_id: str, tool_name: str
    ) -> ToolCallBlock | None:
        """Find the oldest matching child because sub-agent tools finish FIFO."""
        parent = self.tool_blocks.get(parent_id)
        if parent is None:
            return None
        for child in parent.children:
            if child.name == tool_name and child.status == "running":
                return child
        return None

    def update_subagent_tool_done(
        self, parent_id: str, tool_name: str, output: str = ""
    ) -> None:
        block = self._find_open_subagent_tool(parent_id, tool_name)
        if block is None:
            return
        block.set_done(output)

    def update_subagent_tool_error(
        self, parent_id: str, tool_name: str, error: str = ""
    ) -> None:
        block = self._find_open_subagent_tool(parent_id, tool_name)
        if block is None:
            return
        block.set_error(error)

    def update_subagent_tokens(
        self, parent_id: str, prompt: int, completion: int, total: int
    ) -> None:
        parent = self.tool_blocks.get(parent_id)
        if parent is None:
            return
        parent.update_running_tokens(prompt, completion, total)

    def promote_tool(self, job_id: str) -> None:
        """Mark a running tool as backgrounded without removing its visibility."""
        block = self.tool_blocks.get(job_id)
        if block is None:
            return
        block.promote_to_background()

    def remove_tool(self, job_id: str) -> None:
        block = self.tool_blocks.pop(job_id, None)
        if block is None:
            return
        if job_id in self._top_order:
            self._top_order.remove(job_id)

    def cancel_tool(self, job_id: str) -> RenderableType | None:
        """Mark a tool as cancelled and finalize it."""
        block = self.tool_blocks.get(job_id)
        if block is None:
            return None
        block.set_error("cancelled")
        return self._finalize_block(block)

    def toggle_latest_tool_expand(self) -> bool:
        """Toggle expansion of the most recent top-level tool block."""
        for job_id in reversed(self._top_order):
            block = self.tool_blocks.get(job_id)
            if block is None:
                continue
            block.expanded = not block.expanded
            return True
        return False

    def latest_running_direct_job_id(self) -> str | None:
        """Return the most recently started, still-running, non-background
        top-level job. Used by Ctrl+B backgroundify."""
        for job_id in reversed(self._top_order):
            block = self.tool_blocks.get(job_id)
            if block is None:
                continue
            if block.status == "running" and not block.is_background:
                return job_id
        return None

    def latest_running_bg_job_id(self) -> tuple[str, str] | None:
        """Return (job_id, name) for the most recent running, backgrounded
        top-level job. Used by Ctrl+X cancel."""
        for job_id in reversed(self._top_order):
            block = self.tool_blocks.get(job_id)
            if block is None:
                continue
            if block.status == "running" and block.is_background:
                return job_id, block.name
        return None

    def update_footer_tokens(
        self,
        prompt: int,
        completion: int,
        max_ctx: int = 0,
        cached: int = 0,
    ) -> None:
        self.footer.update_tokens(prompt, completion, max_ctx, cached)

    def update_footer_model(self, model: str) -> None:
        self.footer.update_model(model)

    def set_compacting(self, value: bool) -> None:
        self._compacting = value
        self.footer.set_compacting(value)

    def set_processing(self, value: bool) -> None:
        """Toggle activity across the entire turn, including tool gaps."""
        if value and not self._turn_active:
            self._active_started_at = time.monotonic()
        self._turn_active = value
        self.footer.set_processing(value)

    def _render_compaction_banner(self) -> RenderableType:
        return Panel(
            Text(
                f"{ICON_COMPACT} Compacting conversation history…",
                style=COLOR_COMPACT_BANNER,
            ),
            border_style="yellow",
            padding=(0, 1),
            expand=True,
        )

    @property
    def has_content(self) -> bool:
        """True if the live region has anything to render (besides the footer)."""
        if self._compacting:
            return True
        if self._active or self._turn_active:
            return True
        if self.assistant_msg is not None and not self.assistant_msg.is_empty:
            return True
        if self._top_order:
            return True
        return False

    @property
    def needs_animation(self) -> bool:
        """Return whether periodic redraws are required for active indicators."""
        if self._compacting:
            return True
        if self._active or self._turn_active:
            return True
        for job_id in self._top_order:
            block = self.tool_blocks.get(job_id)
            if block is None:
                continue
            if block.status == "running":
                return True
        return False

    def _activity_label(self) -> str:
        """Describe whether the turn is thinking, generating, or running tools."""
        running: list[str] = []
        for job_id in self._top_order:
            block = self.tool_blocks.get(job_id)
            if block is None:
                continue
            if block.status != "running" or block.is_background:
                continue
            running.append(block.name)
        if running:
            names = ", ".join(running[:2])
            suffix = f" (+{len(running) - 2})" if len(running) > 2 else ""
            return f"running {names}{suffix}"
        if self.assistant_msg is not None and not self.assistant_msg.is_empty:
            return "generating"
        return "thinking"

    def _render_activity_line(self) -> RenderableType:
        """Render the turn-wide activity pulse and elapsed time."""
        now = time.monotonic()
        elapsed = now - self._active_started_at if self._active_started_at else 0.0
        frame = spinner_frame(now)
        label = self._activity_label()
        line = Text()
        line.append(f"{frame} ", style="bold magenta")
        line.append(f"{THINKING_LABEL}…", style=COLOR_AI)
        line.append(f"  {label}", style="dim")
        if elapsed >= 1.0:
            line.append(f"  ({elapsed:.0f}s)", style="dim")
        return line

    def _render_bg_strip(self) -> RenderableType | None:
        """Collapse running background tools into a compact status strip."""
        bg_blocks = [
            self.tool_blocks[job_id]
            for job_id in self._top_order
            if job_id in self.tool_blocks
            and self.tool_blocks[job_id].is_background
            and self.tool_blocks[job_id].status == "running"
        ]
        if not bg_blocks:
            return None
        lines: list[RenderableType] = [
            Text(f"{ICON_BG} Background ({len(bg_blocks)})", style=COLOR_BG),
        ]
        for block in bg_blocks:
            lines.append(block.build_compact_line())
        return Group(*lines)

    def add_queued_input(self, text: str) -> None:
        if text:
            self._queued_inputs.append(text)

    def remove_queued_input(self, text: str) -> bool:
        try:
            self._queued_inputs.remove(text)
            return True
        except ValueError:
            return False

    def _render_queued_inputs(self) -> RenderableType | None:
        if not self._queued_inputs:
            return None
        lines: list[RenderableType] = []
        for text in self._queued_inputs:
            body = Text()
            body.append(f"{ICON_USER} ", style=COLOR_USER)
            body.append(text, style="dim")
            body.append("  (queued)", style="dim italic")
            lines.append(body)
        return Group(*lines)

    def _build_renderable(self) -> RenderableType:
        """Build live-region content with single blank-line separators."""
        items: list[RenderableType] = []

        def _add(item: RenderableType) -> None:
            """Append with automatic blank-line separation."""
            if items:
                items.append(Text(""))
            items.append(item)

        if self._compacting:
            _add(self._render_compaction_banner())

        if self.assistant_msg is not None and not self.assistant_msg.is_empty:
            _add(self.assistant_msg)

        for job_id in self._top_order:
            block = self.tool_blocks.get(job_id)
            if block is None:
                continue
            if block.is_background and block.status == "running":
                continue
            _add(block)

        if self._active or self._turn_active:
            _add(self._render_activity_line())

        bg_strip = self._render_bg_strip()
        if bg_strip is not None:
            _add(bg_strip)

        queued = self._render_queued_inputs()
        if queued is not None:
            _add(queued)

        if not items:
            return Text("")
        return Group(*items)

    def __rich__(self) -> RenderableType:
        return self._build_renderable()

    def to_ansi(self, width: int) -> str:
        """Render live items (no footer) to an ANSI-colored string."""
        if not self.has_content:
            return ""
        return render_to_ansi(self._build_renderable(), width).rstrip("\n")

    def footer_to_ansi(self, width: int) -> str:
        """Render the footer to an ANSI-colored string."""
        return render_to_ansi(self.footer, width).rstrip("\n")
