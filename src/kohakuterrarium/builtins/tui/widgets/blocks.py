import time

from textual.containers import Vertical
from textual.widgets import Collapsible, Static

from kohakuterrarium.builtins.tui.widgets.helpers import _summarize_output

# ── Tool Call Block ─────────────────────────────────────────────


class ToolBlock(Collapsible):
    """A single tool call displayed as a collapsible accordion.

    Collapsed title shows: icon + name + args + (summary)
    Expanded body shows: full tool output sent to LLM
    """

    DEFAULT_CSS = """
    ToolBlock {
        height: auto;
        margin: 0;
        padding: 0;
    }
    ToolBlock > Contents {
        height: auto;
        max-height: 8;
        overflow-y: auto;
        padding: 0 1;
    }
    ToolBlock > CollapsibleTitle {
        background: transparent;
    }
    ToolBlock > CollapsibleTitle:hover {
        background: #5A4FCF 15%;
    }
    ToolBlock > CollapsibleTitle:focus {
        background: #5A4FCF 15%;
    }
    ToolBlock.-running > CollapsibleTitle {
        color: #D4920A;
    }
    ToolBlock.-done > CollapsibleTitle {
        color: #5A4FCF;
    }
    ToolBlock.-error > CollapsibleTitle {
        color: #E74C3C;
    }
    .tool-output {
        height: auto;
        color: $text-muted;
    }
    """

    BUTTON_OPEN = "[-]"
    BUTTON_CLOSED = "[+]"

    def __init__(
        self,
        tool_name: str,
        args_preview: str = "",
        tool_id: str = "",
        **kwargs,
    ):
        self.tool_name = tool_name
        self.tool_id = tool_id
        self.args_preview = args_preview
        self.state = "running"
        self.result_summary = ""
        self.start_time = time.monotonic()
        self._output_widget = Static("", classes="tool-output")
        title = self._build_title()
        super().__init__(self._output_widget, title=title, collapsed=True, **kwargs)
        self.add_class("-running")

    def _build_title(self) -> str:
        if self.state == "running":
            elapsed = time.monotonic() - self.start_time
            parts = [f"\u25cb {self.tool_name}"]
            if self.args_preview:
                parts.append(f"  {self.args_preview[:50]}")
            if elapsed >= 0.5:
                parts.append(f"  ({elapsed:.1f}s)")
            return "".join(parts)
        elif self.state == "done":
            parts = [f"\u25cf {self.tool_name}"]
            if self.args_preview:
                parts.append(f"  {self.args_preview[:50]}")
            if self.result_summary:
                parts.append(f"  ({self.result_summary})")
            return "".join(parts)
        elif self.state == "error":
            parts = [f"\u2717 {self.tool_name}"]
            if self.result_summary:
                parts.append(f"  {self.result_summary[:60]}")
            return "".join(parts)
        return f"? {self.tool_name}"

    def mark_done(self, output: str = "", summary: str = "") -> None:
        self.state = "done"
        self.result_summary = summary or _summarize_output(output)
        if output:
            try:
                self._output_widget.update(output[:3000])
            except Exception as e:
                _ = e  # fallback: Textual markup parse failure, use raw Content
                # Escape markup-like content that Textual can't parse
                from textual.content import Content

                self._output_widget.update(Content(output[:3000]))
        self.remove_class("-running")
        self.add_class("-done")
        self.title = self._build_title()

    def mark_error(self, error: str = "") -> None:
        self.state = "error"
        self.result_summary = error[:80]
        if error:
            try:
                self._output_widget.update(error[:3000])
            except Exception as e:
                _ = e  # fallback: Textual markup parse failure, use raw Content
                from textual.content import Content

                self._output_widget.update(Content(error[:3000]))
        self.remove_class("-running")
        self.add_class("-error")
        self.title = self._build_title()


class SubAgentBlock(Collapsible):
    """A sub-agent collapsible. Nested tools are plain text lines, not accordions."""

    DEFAULT_CSS = """
    SubAgentBlock {
        height: auto;
        margin: 0;
        padding: 0;
        background: $boost;
    }
    SubAgentBlock > Contents {
        height: auto;
        max-height: 10;
        overflow-y: auto;
        padding: 0 0 0 2;
    }
    SubAgentBlock > CollapsibleTitle {
        background: transparent;
    }
    SubAgentBlock > CollapsibleTitle:hover {
        background: #A57EAE 15%;
    }
    SubAgentBlock > CollapsibleTitle:focus {
        background: #A57EAE 15%;
    }
    SubAgentBlock.-running > CollapsibleTitle {
        color: #A57EAE;
    }
    SubAgentBlock.-done > CollapsibleTitle {
        color: #A57EAE;
    }
    SubAgentBlock.-error > CollapsibleTitle {
        color: #E74C3C;
    }
    SubAgentBlock.-interrupted > CollapsibleTitle {
        color: #D4920A;
    }
    .sa-tools {
        height: auto;
    }
    .sa-tool-line {
        height: 1;
        margin: 0 0 0 2;
        color: #0F52BA;
    }
    .sa-tool-line.-done {
        color: #5A4FCF;
    }
    .sa-tool-line.-error {
        color: #E74C3C;
    }
    .sa-result {
        height: auto;
        max-height: 6;
        overflow-y: auto;
        color: $text-muted;
        padding: 0 1;
    }
    """

    BUTTON_OPEN = "[-]"
    BUTTON_CLOSED = "[+]"

    def __init__(
        self,
        agent_name: str,
        sa_task: str = "",
        agent_id: str = "",
        **kwargs,
    ):
        self.agent_name = agent_name
        self.sa_task = sa_task
        self.agent_id = agent_id
        self.state = "running"
        self.result_summary = ""
        self.start_time = time.monotonic()
        self._tools_container = Vertical(classes="sa-tools")
        self._result_widget = Static("", classes="sa-result")
        # Track tool lines by unique key for updating
        self._tool_lines: dict[str, Static] = {}
        self._tool_counter: int = 0
        # Map: tool_name -> list of keys (for matching done events by name)
        self._tool_name_keys: dict[str, list[str]] = {}
        super().__init__(
            self._tools_container,
            self._result_widget,
            title=self._build_title(),
            collapsed=False,
            **kwargs,
        )
        self.add_class("-running")

    def _build_title(self) -> str:
        if self.state == "running":
            elapsed = time.monotonic() - self.start_time
            parts = [f"\u25cb {self.agent_name}"]
            if self.sa_task:
                parts.append(f"  {self.sa_task[:40]}")
            if elapsed >= 0.5:
                parts.append(f"  ({elapsed:.1f}s)")
            return "".join(parts)
        elif self.state == "done":
            parts = [f"\u25cf {self.agent_name}"]
            if self.result_summary:
                parts.append(f"  ({self.result_summary})")
            return "".join(parts)
        elif self.state == "interrupted":
            return f"\u25cb {self.agent_name}  (interrupted)"
        else:
            parts = [f"\u2717 {self.agent_name}"]
            if self.result_summary:
                parts.append(f"  {self.result_summary[:50]}")
            return "".join(parts)

    def add_tool_line(self, tool_name: str, args_preview: str = "") -> str:
        """Add a single-line tool entry. Returns unique key for update."""
        key = f"{tool_name}_{self._tool_counter}"
        self._tool_counter += 1
        text = f"\u25cb {tool_name}"
        if args_preview:
            text += f"  {args_preview[:50]}"
        line = Static(text, classes="sa-tool-line")
        line._raw_text = text
        self._tool_lines[key] = line
        self._tool_name_keys.setdefault(tool_name, []).append(key)
        try:
            self._tools_container.mount(line)
        except Exception as e:
            _ = e  # fallback: widget not yet mounted, defer to on_mount
            if not hasattr(self, "_pending_tool_lines"):
                self._pending_tool_lines = []
            self._pending_tool_lines.append(line)
        return key

    def on_mount(self) -> None:
        """Mount pending tool lines and apply deferred CSS classes."""
        if hasattr(self, "_pending_tool_lines"):
            for line in self._pending_tool_lines:
                self._tools_container.mount(line)
                # Apply CSS class that was set before mount
                if hasattr(line, "_deferred_class"):
                    line.add_class(line._deferred_class)
            del self._pending_tool_lines

    def update_tool_line(
        self, tool_name: str, done: bool = True, error: bool = False
    ) -> None:
        """Update the first unfinished tool line matching tool_name."""
        # Find first key for this tool_name that's still running (○)
        keys = self._tool_name_keys.get(tool_name, [])
        line = None
        for key in keys:
            candidate = self._tool_lines.get(key)
            if candidate and getattr(candidate, "_raw_text", "").startswith("\u25cb "):
                line = candidate
                break
        if not line:
            return
        old_text = getattr(line, "_raw_text", "")
        new_text = old_text.replace("\u25cb ", "\u25cf " if done else "\u2717 ", 1)
        line._raw_text = new_text
        line.update(new_text)
        # Store desired class for deferred application after mount
        cls = "-error" if error else "-done"
        line._deferred_class = cls
        # Try to apply now (works if already mounted, harmless if not)
        line.add_class(cls)

    def mark_done(
        self,
        output: str = "",
        tools_used: list[str] | None = None,
        turns: int = 0,
        duration: float = 0,
    ) -> None:
        self.state = "done"
        parts = []
        if tools_used:
            parts.append(", ".join(tools_used))
        if turns:
            parts.append(f"{turns} turns")
        if duration >= 0.1:
            parts.append(f"{duration:.1f}s")
        self.result_summary = "; ".join(parts) if parts else ""
        if output:
            self._result_widget.update(output[:2000])
        self.remove_class("-running")
        self.add_class("-done")
        self.title = self._build_title()

    def mark_error(self, error: str = "") -> None:
        self.state = "error"
        self.result_summary = error[:60]
        self.remove_class("-running")
        self.add_class("-error")
        self.title = self._build_title()

    def mark_interrupted(self) -> None:
        self.state = "interrupted"
        self.remove_class("-running")
        self.add_class("-interrupted")
        self.title = self._build_title()


class CompactSummaryBlock(Collapsible):
    """Compact summary displayed as a collapsible accordion.

    Amber while compacting, aquamarine when done.
    """

    DEFAULT_CSS = """
    CompactSummaryBlock {
        height: auto;
        margin: 1 0;
        padding: 0;
    }
    CompactSummaryBlock > Contents {
        height: auto;
        max-height: 12;
        overflow-y: auto;
        padding: 0 1;
    }
    CompactSummaryBlock > CollapsibleTitle {
        background: transparent;
    }
    CompactSummaryBlock > CollapsibleTitle:hover {
        background: #0F52BA 15%;
    }
    CompactSummaryBlock > CollapsibleTitle:focus {
        background: #0F52BA 15%;
    }
    CompactSummaryBlock.-running > CollapsibleTitle {
        color: #D4920A;
    }
    CompactSummaryBlock.-done > CollapsibleTitle {
        color: #0F52BA;
    }
    .compact-body {
        height: auto;
        color: $text-muted;
    }
    """

    BUTTON_OPEN = "[-]"
    BUTTON_CLOSED = "[+]"

    def __init__(self, summary: str, done: bool = False, **kwargs):
        self._body = Static(summary, classes="compact-body")
        if done:
            title = "\u25cf Context auto-compact"
        else:
            title = "\u25cb Context auto-compact"
        super().__init__(self._body, title=title, collapsed=True, **kwargs)
        self.add_class("-done" if done else "-running")

    def mark_done(self, summary: str) -> None:
        """Transition from running (amber) to done (sapphire)."""
        self._body.update(summary)
        self.title = "\u25cf Context auto-compact"
        self.remove_class("-running")
        self.add_class("-done")
