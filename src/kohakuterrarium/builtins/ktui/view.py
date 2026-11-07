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

from collections.abc import Callable
from typing import Any

from kohakutui import App
from kohakutui.ui import (
    Cell,
    Node,
    Status,
    Text,
)

from kohakuterrarium.builtins.ktui.format import (
    duration,
    one_line,
    result_summary,
    tokens,
)
from kohakuterrarium.builtins.ktui.inline_ui import ReplyStream
from kohakuterrarium.builtins.ktui.ui_block import UiContext
from kohakuterrarium.builtins.ktui.view_notices import NoticeCells
from kohakuterrarium.builtins.ktui.view_subagents import SubagentCells
from kohakuterrarium.builtins.ktui.view_state import (
    REPLY_ACTIONS,
    USER_ACTIONS,
    VERSION_ACTIONS,
    Job,
    Message,
    Usage,
)
from kohakuterrarium.builtins.ktui.runs import BATCH_SIZE, ToolRuns, output_box
from kohakuterrarium.builtins.ktui.trace import Traces
from kohakuterrarium.builtins.ktui.tool_bodies import MediaPlace, tool_body

STATUS_LABEL = "kohakUwUing · esc to interrupt"
UNREAD_KINDS = {
    "user_message",
    "assistant_end",
    "tool_start",
    "subagent_start",
    "notice",
}
# rows in the shared live region: only the focused creature shows them
LIVE_ONLY = {"status_on", "compact_start", "args_progress"}
# a new transcript entry (or turn) freezes the live <ui> blocks above it, inline (D26)
FREEZE_ON = {
    "divider", "user_message", "text", "notice", "markdown_notice", "error", "card",
    "status_on", "compact_start", "tool_start", "subagent_start",
}  # fmt: skip


class CreatureView(SubagentCells, NoticeCells):
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
        # node id → message, for the message actions; the turn of the newest user message
        self.messages: dict[int, Message] = {}
        # pictures, videos and sounds of the conversation (the side panel's Canvas)
        self.artifacts: list[tuple[str, str, str]] = []
        # where its media files are (set by the shell for the creature's session)
        self.media: MediaPlace | None = None
        self._turn: int | None = None
        # turn → (version shown, versions) when a turn has several (edits, retries)
        self.versions: dict[int, tuple[int, int]] = {}

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
        last = len(self._lingering) - 1
        for i, (reply, _) in enumerate(self._lingering):
            reply.refresh_hints(quiet=i < last)

    @property
    def has_live_blocks(self) -> bool:
        return bool(self._lingering)

    def freeze_blocks(self) -> None:
        """Freezes the live <ui> blocks and closes their replies (they go to scrollback)."""
        for reply, cell in self._lingering:
            reply.freeze()
            cell.seal()
        self._lingering.clear()

    def reset(self) -> None:
        """A fresh, empty document in place of this one (a branch redrawn from history)."""
        old = self.doc
        self.doc = self.app.add_document()
        if self.focused:
            self.app.show_document(self.doc)
        old.remove()
        self._assistant = self._reply = None
        self._lingering.clear()
        self._cells.clear()
        self._children.clear()
        self._closed.clear()
        self._sub_batch.clear()
        self._held = ""
        self._runs = ToolRuns(self.doc, self.batch_size)
        self.messages.clear()
        self.artifacts.clear()
        self._turn = None

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

    def _user_message(
        self, text: str, pending_id: str | None = None, turn: int | None = None
    ) -> None:
        self._assistant_end()
        if pending_id:
            self._queue_remove(pending_id)
        if turn is not None:
            self._turn = turn
        actions = list(USER_ACTIONS) if turn is not None else USER_ACTIONS[:1]
        trailer = None
        shown, count = self.versions.get(turn, (0, 0)) if turn is not None else (0, 0)
        if count > 1:
            actions += VERSION_ACTIONS
            trailer = f"version {shown}/{count} · [ ] switch"
        spec = Cell(
            role="user", children=[Text(text)], actions=actions, trailer=trailer
        )
        node = self.doc.add(spec)
        node.seal()
        self.messages[node.id] = Message("user", text, turn, node)

    def adopt_turn(self, turn: int) -> None:
        """The turn of the last message, learnt after it was drawn (a session in a server
        names the turn with the frames that follow the message)."""
        if self._turn == turn:
            return
        last = next(
            (m for m in reversed(self.messages.values()) if m.kind == "user"), None
        )
        if last is None or last.turn is not None:
            return
        last.turn = turn
        last.node.update(actions=list(USER_ACTIONS))
        self._turn = turn

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
            if self._reply.text.strip():
                self._assistant.update(
                    actions=(
                        REPLY_ACTIONS if self._turn is not None else REPLY_ACTIONS[:1]
                    )
                )
                self.messages[self._assistant.id] = Message(
                    "assistant", self._reply.text.strip(), self._turn, self._assistant
                )
            if self._reply.interactive:
                for older, _ in self._lingering:
                    older.refresh_hints(quiet=True)
                self._lingering.append((self._reply, self._assistant))
            else:
                self._assistant.seal()
        self._assistant = self._reply = None
        if end_run:
            self._runs.end()

    # -- turn status ----------------------------------------------------

    def _status_on(self) -> None:
        if self._status is None and self.focused:
            self._status = self.app.live.add(
                Status(label=STATUS_LABEL, tone="running"), index=0
            )

    def _args_progress(self, name: str, chars: int) -> None:
        if self._status is not None:
            label = f"kohakUwUing · writing {name} · {tokens(chars)} chars · esc to interrupt"
            self._status.update(label=label)

    def _status_label(self) -> None:
        if self._status is not None:
            self._status.update(label=STATUS_LABEL)

    def _status_off(self) -> None:
        self._assistant_end()
        if self._status is not None:
            self._status.remove()
            self._status = None

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
        self._status_label()
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
        self,
        job_id: str,
        name: str,
        output: str,
        error: str,
        took: float,
        args: Any = None,
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

        body = None if error else tool_body(name, args, output)
        if error:
            cell.add(Text(error, tone="danger"))
        elif body is not None:
            cell.add(body)
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
