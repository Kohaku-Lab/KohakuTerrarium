"""Sub-agent traces: what each sub-agent was asked, the tools it called, what it returned.

The output module records every sub-agent here as its events arrive, focused or not, so the
board can list running and recently finished sub-agents and open one's trace like the web's
sub-agent view. :func:`trace_view` builds that view: a scroll box docked at the input with the
task, the tool calls (batched like the transcript; ``e`` lists them one by one), the result and
the stats. Listeners hear every change, so an open trace follows a running sub-agent.
"""

import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field

from kohakutui.ui import Cell, Markdown, Scroll, Stack, Text

from kohakuterrarium.builtins.ktui.format import BATCH_TITLE, duration, one_line
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

# finished traces kept for the board; running ones are always kept
KEEP_FINISHED = 20


@dataclass
class Step:
    """One tool call inside a sub-agent."""

    tool: str
    detail: str = ""
    status: str = "running"
    summary: str = ""


@dataclass
class Trace:
    """One sub-agent run."""

    job_id: str
    name: str
    task: str = ""
    background: bool = False
    status: str = "running"
    result: str = ""
    error: str = ""
    stats: str = ""
    steps: list[Step] = field(default_factory=list)
    started: float = field(default_factory=time.monotonic)
    ended: float | None = None

    @property
    def running(self) -> bool:
        return self.status == "running"

    def took(self) -> float:
        return (self.ended or time.monotonic()) - self.started


class Traces:
    """Every running sub-agent and the latest finished ones, oldest first."""

    def __init__(self) -> None:
        self.items: OrderedDict[str, Trace] = OrderedDict()
        self._listeners: list[Callable[[Trace], None]] = []

    def get(self, job_id: str) -> Trace | None:
        return self.items.get(job_id)

    def listen(self, fn: Callable[[Trace], None]) -> Callable[[], None]:
        """Calls ``fn`` on every change; returns the function that stops it."""
        self._listeners.append(fn)
        return lambda: self._listeners.remove(fn) if fn in self._listeners else None

    def _changed(self, trace: Trace) -> None:
        # a failing listener (an open trace view) must not stop the transcript's own update
        for fn in list(self._listeners):
            try:
                fn(trace)
            except Exception as exc:
                logger.warning(
                    "ktui trace listener failed", error=str(exc), exc_info=True
                )

    def start(self, job_id: str, name: str, task: str, background: bool) -> None:
        self.items[job_id] = Trace(job_id, name, task, background)
        self._prune()
        self._changed(self.items[job_id])

    def tool(self, job_id: str, activity: str, tool: str, detail: str) -> None:
        trace = self.items.get(job_id)
        if trace is None:
            return
        if activity == "start":
            trace.steps.append(Step(tool, detail))
        else:
            # the oldest running call of that name (KT gives no call id)
            step = next(
                (s for s in trace.steps if s.tool == tool and s.status == "running"),
                None,
            )
            if step is None:
                return
            step.status = "error" if activity == "error" else "ok"
            step.summary = one_line(detail, 80)
        self._changed(trace)

    def end(
        self, job_id: str, result: str = "", error: str = "", stats: str = ""
    ) -> None:
        self._finish(job_id, "error" if error else "ok", result, error, stats)

    def promoted(self, job_id: str) -> None:
        """Moved to the background: it outlives an interrupt of the turn from now on."""
        trace = self.items.get(job_id)
        if trace is not None and trace.running:
            trace.background = True
            self._changed(trace)

    def cancelled(self, job_id: str) -> None:
        self._finish(job_id, "interrupted", "", "", "cancelled by user")

    def interrupted(self) -> None:
        """The turn was interrupted: every running foreground sub-agent stops with it."""
        for trace in list(self.items.values()):
            if trace.running and not trace.background:
                self._finish(trace.job_id, "interrupted", "", "", "interrupted")

    def _finish(
        self, job_id: str, status: str, result: str, error: str, stats: str
    ) -> None:
        trace = self.items.get(job_id)
        if trace is None or not trace.running:
            return
        trace.status, trace.result, trace.error = status, result, error
        trace.stats = stats
        trace.ended = time.monotonic()
        for step in trace.steps:
            if step.status == "running":
                step.status = "interrupted"
        self._prune()
        self._changed(trace)

    def _prune(self) -> None:
        finished = [t.job_id for t in self.items.values() if not t.running]
        for job_id in finished[: max(0, len(finished) - KEEP_FINISHED)]:
            del self.items[job_id]


STATUS_ICON = {"running": "⚙", "ok": "✓", "error": "✗", "interrupted": "○"}


def board_label(trace: Trace) -> str:
    return (
        f"{STATUS_ICON.get(trace.status, '◆')} {trace.name}  {one_line(trace.task, 50)}"
    )


def board_detail(trace: Trace, creature: str) -> str:
    where = "background" if trace.background else "foreground"
    state = trace.status if not trace.running else f"running {where}"
    return f"{creature} · {state} · {len(trace.steps)} calls · {duration(trace.took())}"


def trace_body(trace: Trace, batch_size: int) -> Stack:
    """Task, tool calls, result and stats: the content of the trace view."""
    parts: list = [
        Cell(
            role="user",
            title="task",
            tone="agent",
            children=[Markdown(source=trace.task or "(no task text)")],
        )
    ]
    if trace.steps:
        calls = [
            Cell(
                role="tool",
                title=step.tool,
                detail=" · ".join(p for p in (step.detail, step.summary) if p) or None,
                status=step.status,
                tone="tool",
            )
            for step in trace.steps
        ]
        if batch_size > 0:
            parts.append(
                Cell(role="tool", batch=batch_size, title=BATCH_TITLE, children=calls)
            )
        else:
            parts.extend(calls)
    if trace.error:
        parts.append(Cell(role="error", children=[Text(trace.error)]))
    elif trace.result:
        parts.append(Cell(role="assistant", children=[Markdown(source=trace.result)]))
    elif trace.running:
        parts.append(Text("  working…", tone="running"))
    stats = " · ".join(
        p
        for p in (
            trace.status,
            f"{len(trace.steps)} tool calls",
            duration(trace.took()),
            trace.stats if trace.stats not in ("", "done") else "",
        )
        if p
    )
    parts.append(Text(stats, tone="muted"))
    return Stack(gap=1, children=parts)


def trace_view(
    trace: Trace, creature: str, height: int, batch_size: int, grouped: bool
) -> Scroll:
    """The docked trace dialog; its keys answer with "stop" or "group"."""
    keys = {"e": "list calls" if grouped else "group calls"}
    if trace.running:
        keys["s"] = "stop"
    return Scroll(
        title=f"◆ {trace.name} · {creature} · sub-agent trace",
        height=height,
        keys=keys,
        dock="input",
        children=[trace_body(trace, batch_size if grouped else 0)],
    )
