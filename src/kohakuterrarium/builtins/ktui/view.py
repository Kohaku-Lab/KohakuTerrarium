"""One creature's view: its own kohakutui document (a transcript of its own) and its live rows.

Rendering goes through :meth:`CreatureView.render` and always draws into the creature's
document, focused or not, so open tool cells and live ``<ui>`` blocks keep updating while another
creature is shown. Only the shown document is drawn (and, inline, reaches scrollback); switching
shows the other document, whose lines not yet in scrollback are written then. The live region
(spinner, progress, queued messages) is shared: only the focused creature puts rows there.
State that is not drawing (usage, jobs, traces, processing) is updated directly by the output
module.

Consecutive tool calls are batched like the web's tool batch (see ``runs.py``); a sub-agent's own
calls are batched inside its cell the same way.
"""

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from kohakutui import App
from kohakutui.ui import (
    Card,
    Cell,
    Markdown,
    Node,
    Progress,
    Status,
    Text,
)

from kohakuterrarium.builtins.ktui.format import (
    duration,
    one_line,
    result_summary,
)
from kohakuterrarium.builtins.ktui.inline_ui import ReplyStream
from kohakuterrarium.builtins.ktui.ui_block import UiContext
from kohakuterrarium.builtins.ktui.view_subagents import SubagentCells
from kohakuterrarium.builtins.ktui.runs import BATCH_SIZE, ToolRuns, output_box
from kohakuterrarium.builtins.ktui.trace import Traces

UNREAD_KINDS = {
    "user_message",
    "assistant_end",
    "tool_start",
    "subagent_start",
    "notice",
}
# rows in the shared live region: only the focused creature shows them
LIVE_ONLY = {"status_on", "compact_start"}
# a new transcript entry (or turn) freezes the live <ui> blocks above it, inline (D26)
FREEZE_ON = {
    "divider", "user_message", "text", "notice", "markdown_notice", "error", "card",
    "status_on", "compact_start", "tool_start", "subagent_start",
}  # fmt: skip


@dataclass
class Usage:
    """Token and context figures for the footer."""

    model: str = ""
    tokens_in: int = 0
    tokens_out: int = 0
    cached: int = 0
    last_prompt: int = 0
    max_context: int = 0

    def context_pct(self) -> int | None:
        if not self.max_context or not self.last_prompt:
            return None
        return min(100, self.last_prompt * 100 // self.max_context)


@dataclass
class Job:
    """A running tool or sub-agent, foreground or background."""

    job_id: str
    name: str
    kind: str
    detail: str = ""
    background: bool = False
    started: float = field(default_factory=time.monotonic)


class CreatureView(SubagentCells):
    """Draws one creature into its own document and, while focused, the live region."""

    def __init__(
        self,
        app: App,
        creature_id: str,
        name: str,
        on_change: Callable[[], None] = lambda: None,
        collapse_tools: bool = True,
        batch_size: int = BATCH_SIZE,
        ui: UiContext | None = None,
        focused: bool = True,
    ):
        self.app = app
        self.creature_id = creature_id
        self.name = name
        self.on_change = on_change
        self.collapse_tools = collapse_tools
        self.batch_size = batch_size
        self.usage = Usage()
        self.jobs: dict[str, Job] = {}
        self.traces = Traces()
        self.processing = False
        self.focused = focused
        self.unread = 0

        # this creature's transcript; shown while it has the focus
        self.doc = app.add_document()
        if focused:
            app.show_document(self.doc)
        self._assistant: Node | None = None
        self._reply: ReplyStream | None = None
        self.ui = ui
        # replies whose <ui> blocks still take input: their cells stay open until they freeze
        self._lingering: list[tuple[ReplyStream, Node]] = []
        self._next_turn: tuple[int, int] | None = None  # a replayed reply's own turn
        self._cells: dict[str, Node] = {}
        self._children: dict[str, list[tuple[str, Node]]] = {}
        # calls already closed (cancelled, interrupted): their late end events are dropped
        self._closed: set[str] = set()
        self._status: Node | None = None
        self._compacting: Node | None = None
        self._progress: dict[str, Node] = {}
        self._queued: dict[str, tuple[str, Node | None]] = {}
        self._runs = ToolRuns(self.doc, batch_size)
        self._held = ""
        self._sub_batch: dict[str, Node] = {}

    # -- dispatch -------------------------------------------------------

    def render(self, method: str, *args: Any) -> None:
        """Draws into this creature's document, shown or not; counts unread while not."""
        if not self.focused:
            if method in LIVE_ONLY:
                return
            if method in UNREAD_KINDS:
                self.unread += 1
                self.on_change()
        if method in FREEZE_ON and self._lingering and self._inline():
            self.freeze_blocks()
        getattr(self, "_" + method)(*args)

    def set_focused(self, focused: bool, note: str | None = None) -> None:
        """Shows this creature's document (``note``: inline, the divider written before it) or
        takes its rows out of the shared live region."""
        if focused == self.focused:
            return
        if not focused:
            self._leave()
            self.focused = False
            return

        self.focused = True
        self.unread = 0
        self.app.show_document(self.doc, note)
        for pending_id, (text, _) in list(self._queued.items()):
            self._queue_add(pending_id, text)
        if self.processing:
            self._status_on()

    def _reply_turn(self, turn: tuple[int, int] | None) -> None:
        self._next_turn = turn

    def _inline(self) -> bool:
        return self.ui is None or self.ui.inline()

    def mode_changed(self, inline: bool) -> None:
        """Inline: the live <ui> blocks freeze (scrollback again); full screen: new hints."""
        if inline:
            self.freeze_blocks()
        for reply, _ in self._lingering:
            reply.refresh_hints()

    @property
    def has_live_blocks(self) -> bool:
        return bool(self._lingering)

    def freeze_blocks(self) -> None:
        """Freezes the live <ui> blocks and closes their replies (they go to scrollback)."""
        for reply, cell in self._lingering:
            reply.freeze()
            cell.seal()
        self._lingering.clear()

    def _leave(self) -> None:
        # the document keeps everything, open cells included; only the live rows go
        for node in (self._status, self._compacting, *self._progress.values()):
            if node is not None:
                node.remove()
        self._status = self._compacting = None
        self._progress.clear()
        for pending_id, (text, node) in list(self._queued.items()):
            if node is not None:
                node.remove()
            self._queued[pending_id] = (text, None)

    # -- messages -------------------------------------------------------

    def _seal(self, spec: Any) -> None:
        self.doc.add(spec).seal()

    def _divider(self, label: str) -> None:
        # closes an open run first, so later calls never join a batch above the divider
        self._assistant_end()
        self._seal(
            Cell(
                role="system", icon="─", children=[Text(f"── {label} ──", tone="muted")]
            )
        )

    def _user_message(self, text: str, pending_id: str | None = None) -> None:
        self._assistant_end()
        if pending_id:
            self._queue_remove(pending_id)
        self._seal(Cell(role="user", children=[Text(text)]))

    def _text(self, chunk: str) -> None:
        if self._assistant is None:
            # whitespace between tool calls is not text: it neither opens a cell nor ends a run
            self._held += chunk
            if not self._held.strip():
                return
            chunk, self._held = self._held.lstrip("\n"), ""
            self._runs.end()
            self._assistant = self.doc.add(Cell(role="assistant", stream_flush=True))
            self._reply = ReplyStream(
                self.app, self._assistant, self.ui, self._next_turn
            )
            self._next_turn = None
        self._reply.feed(chunk)

    def _assistant_end(self, end_run: bool = True) -> None:
        self._held = ""
        if self._assistant is not None:
            self._reply.close()
            if self._reply.interactive:
                self._lingering.append((self._reply, self._assistant))
            else:
                self._assistant.seal()
        self._assistant = self._reply = None
        if end_run:
            self._runs.end()

    def _notice(self, text: str, tone: str = "muted", icon: str | None = None) -> None:
        self._assistant_end()
        self._seal(Cell(role="system", icon=icon, children=[Text(text, tone=tone)]))

    def _markdown_notice(self, title: str, body: str, collapsed: bool = False) -> None:
        self._assistant_end()
        spec = Cell(
            role="system",
            title=title,
            tone="accent",
            collapsed=collapsed or None,
            summary=one_line(body, 70) if collapsed else None,
            children=[Markdown(source=body)],
        )
        self._seal(spec)

    def _error(self, text: str) -> None:
        self._assistant_end()
        self._seal(Cell(role="error", children=[Text(text)]))

    def _card(self, title: str, body: str) -> None:
        self._assistant_end()
        self._seal(Card(title=title or None, body=body))

    # -- turn status ----------------------------------------------------

    def _status_on(self) -> None:
        if self._status is None and self.focused:
            label = "kohakUwUing · esc to interrupt"
            self._status = self.app.live.add(
                Status(label=label, tone="running"), index=0
            )

    def _status_off(self) -> None:
        self._assistant_end()
        if self._status is not None:
            self._status.remove()
            self._status = None

    def _compact_start(self, round_num: int) -> None:
        if self._compacting is None:
            label = f"compacting context · round {round_num} · you can keep chatting"
            self._compacting = self.app.live.add(Status(label=label, tone="attention"))

    def _compact_end(self, round_num: int, summary: str, skipped: str) -> None:
        if self._compacting is not None:
            self._compacting.remove()
            self._compacting = None
        if skipped:
            self._notice(f"compaction skipped: {skipped}")
        elif summary:
            self._markdown_notice(
                f"context compacted · round {round_num}", summary, True
            )
        else:
            self._notice(f"context compacted · round {round_num}", tone="accent")

    def _progress_update(
        self, key: str, label: str, value: Any, total: Any, done: bool
    ) -> None:
        node = self._progress.get(key)
        if done:
            if node is not None:
                node.remove()
                self._progress.pop(key, None)
            self._notice(f"✓ {label}", tone="ok")
            return
        if node is None:
            if not self.focused:
                return
            node = self.app.live.add(Progress(label=label, total=total))
            self._progress[key] = node
        if value is not None:
            node.update(label=label, total=total, value=value)

    # -- queued input ---------------------------------------------------

    def queue(self, pending_id: str, text: str) -> None:
        self._queued[pending_id] = (text, None)
        if self.focused:
            self._queue_add(pending_id, text)

    def _queue_add(self, pending_id: str, text: str) -> None:
        row = Text(
            f"  queued · {one_line(text, 70)}  (ctrl+x cancels the last)", tone="faint"
        )
        node = self.app.live.add(row) if self.focused else None
        self._queued[pending_id] = (text, node)

    def _queue_remove(self, pending_id: str) -> None:
        _, node = self._queued.pop(pending_id, ("", None))
        if node is not None:
            node.remove()

    def unqueue_last(self) -> str | None:
        """Forgets the newest queued message and returns its id (the caller cancels it in KT)."""
        if not self._queued:
            return None
        pending_id = next(reversed(self._queued))
        self._queue_remove(pending_id)
        return pending_id

    @property
    def queued_count(self) -> int:
        return len(self._queued)

    # -- tools and sub-agents ---------------------------------------------

    def _tool_start(
        self, job_id: str, name: str, detail: str, background: bool
    ) -> None:
        self._assistant_end(end_run=False)
        if background:
            self._notice(
                f"⏵ {name}  {detail} · running in background (ctrl+b)", tone="running"
            )
            return
        spec = Cell(
            role="tool",
            title=name,
            detail=detail or None,
            status="running",
            tone="tool",
            collapsed=self.collapse_tools or None,
        )
        self._cells[job_id] = self._runs.parent(job_id).add(spec)

    def _tool_end(
        self, job_id: str, name: str, output: str, error: str, took: float
    ) -> None:
        if job_id in self._closed:
            self._closed.discard(job_id)
            return
        cell = self._cells.pop(job_id, None)
        status = "error" if error else "ok"
        summary = one_line(error, 70) if error else result_summary(output)
        if cell is None:
            self._runs.done(job_id)
        if cell is None:
            # backgrounded: the call's cell was never opened
            self._assistant_end()
            spec = Cell(
                role="tool", title=name, status=status, tone="tool", detail=summary
            )
            self._seal(spec)
            return

        if error:
            cell.add(Text(error, tone="danger"))
        elif output.strip():
            cell.add(output_box(output))
        cell.update(status=status, summary=summary, trailer=duration(took))
        cell.seal()
        self._runs.done(job_id)

    def _promote(self, job_id: str, name: str) -> None:
        """A running foreground call moved to the background: close its cell now."""
        cell = self._cells.pop(job_id, None)
        self._children.pop(job_id, None)
        self._sub_batch.pop(job_id, None)
        if cell is not None:
            cell.update(status="background", summary="moved to the background (ctrl+b)")
            cell.seal()
            self._runs.done(job_id)
        else:
            self._notice(f"⏵ {name} moved to the background", tone="running")

    def _close_open(self, reason: str) -> None:
        """Seals every still-open call cell (children seal with their parent)."""
        self._assistant_end()
        for job_id, cell in self._cells.items():
            cell.update(status="interrupted", summary=reason)
            cell.seal()
            self._closed.add(job_id)
        self._cells.clear()
        self._children.clear()
        self._runs.seal_all()
        self._sub_batch.clear()

    def _cancelled(self, job_id: str, name: str, kind: str, took: float) -> None:
        cell = self._cells.pop(job_id, None)
        self._children.pop(job_id, None)
        self._sub_batch.pop(job_id, None)
        self._closed.add(job_id)
        if cell is not None:
            cell.update(
                status="interrupted",
                summary="cancelled by user",
                trailer=duration(took),
            )
            cell.seal()
            self._runs.done(job_id)
            return
        icon = "◆" if kind == "subagent" else "○"
        self._notice(f"{icon} {name} cancelled by user", tone="attention")
