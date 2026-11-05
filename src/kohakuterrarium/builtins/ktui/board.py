"""The board behind the active-items row (↓ from the input, ctrl+b, ``/agents``).

A picker docked at the input with a section per kind: creatures, sub-agents (running and
recently finished), jobs and approvals. Enter on a creature focuses it; on a sub-agent it opens
its trace, which follows a running sub-agent live (``s`` stops it, ``e`` lists or groups its
calls, esc goes back to the input); on a job, its Stop / Background card; on an approval, the
approval itself.
"""

import shutil
import time
from typing import TYPE_CHECKING, Any

from kohakutui.ui import Card, Node, Picker

from kohakuterrarium.builtins.ktui.format import duration, one_line
from kohakuterrarium.builtins.ktui.trace import (
    Trace,
    board_detail,
    board_label,
    trace_body,
    trace_view,
)

if TYPE_CHECKING:
    from kohakuterrarium.builtins.ktui.shell import KtuiShell


class Board:
    """Builds the board's items and acts on the chosen one."""

    def __init__(self, shell: "KtuiShell"):
        self.shell = shell

    async def open(self, jobs_only: bool = False, section: str | None = None) -> None:
        """The board; ``section`` (a segment of the active row) opens on that section."""
        shell = self.shell
        match section:
            case "approvals":
                shell.interactions.show_next()
                return
            case "queued":
                shell.app.notify("ctrl+x cancels the newest queued message", "info")
                return
        items = self.items(jobs_only)
        if not items:
            self.shell.app.notify(
                "nothing running" if jobs_only else "nothing active", "info"
            )
            return
        title = "Jobs" if jobs_only else "Agents · jobs · approvals"
        choice = await self.shell.panel(
            Picker(
                items=items, title=title, sidebar=True, max_visible=8, section=section
            )
        )
        if not choice:
            return

        kind, _, rest = str(choice).partition(":")
        match kind:
            case "creature":
                shell.focus(rest)
            case "ask":
                pending = next(
                    (p for p in shell.interactions.items if p.id == rest), None
                )
                shell.interactions.hide()
                shell.interactions.show(pending)
            case "trace":
                cid, _, job_id = rest.partition(":")
                await self.trace(cid, job_id)
            case "job":
                cid, _, job_id = rest.partition(":")
                await self.job(cid, job_id)

    def items(self, jobs_only: bool) -> list[dict]:
        shell = self.shell
        items: list[dict] = []
        if shell.multi and not jobs_only:
            for cid, entry in shell.entries.items():
                view = entry.view
                running = shell.is_busy(entry)
                extra = f" · {view.unread} new" if view.unread else ""
                items.append(
                    {
                        "label": f"{'⚙' if running else '●'} {view.name}",
                        "detail": f"{'running' if running else 'idle'} · {view.usage.model or 'model ?'}{extra}",
                        "value": f"creature:{cid}",
                        "group": "creatures",
                        "current": cid == shell.focus_id,
                    }
                )
        for cid, entry in shell.entries.items():
            # newest first; finished ones stay listed so their trace can still be read
            for trace in reversed(list(entry.view.traces.items.values())):
                if jobs_only and not trace.running:
                    continue
                items.append(
                    {
                        "label": board_label(trace),
                        "detail": board_detail(trace, entry.view.name),
                        "value": f"trace:{cid}:{trace.job_id}",
                        "group": "sub-agents",
                    }
                )
            for job in entry.view.jobs.values():
                if job.kind != "tool":
                    continue
                icon = "⏵" if job.background else "⚙"
                where = "background" if job.background else "foreground"
                items.append(
                    {
                        "label": f"{icon} {job.name}  {one_line(job.detail, 50)}",
                        "detail": f"{entry.view.name} · {where} · {duration(time.monotonic() - job.started)}",
                        "value": f"job:{cid}:{job.job_id}",
                        "group": "jobs",
                    }
                )
        if not jobs_only:
            for pending in shell.interactions.items:
                items.append(
                    {
                        "label": f"! {pending.title}",
                        "detail": f"{pending.view.name} · waiting for you",
                        "value": f"ask:{pending.id}",
                        "group": "approvals",
                    }
                )
        return items

    def segments(self) -> list[dict]:
        """The active-items row under the input, one segment per kind; empty hides it."""
        shell = self.shell
        segments = []
        # creatures are not a segment: the full-screen rail and /focus or @name cover them
        jobs = [j for e in shell.entries.values() for j in e.view.jobs.values()]
        agents = sum(1 for j in jobs if j.kind == "subagent")
        background = sum(1 for j in jobs if j.background and j.kind == "tool")
        if agents:
            plural = "s" if agents > 1 else ""
            segments.append(
                {"label": f"◆ {agents} sub-agent{plural}", "value": "sub-agents"}
            )
        if background:
            plural = "s" if background > 1 else ""
            segments.append(
                {"label": f"⏵ {background} background task{plural}", "value": "jobs"}
            )
        queued = shell.focused.view.queued_count
        if queued:
            segments.append({"label": f"⋯ {queued} queued", "value": "queued"})
        if shell.interactions.count:
            label = f"! {shell.interactions.count} waiting · alt+j"
            segments.append({"label": label, "value": "approvals"})
        return segments

    async def job(self, cid: str, job_id: str) -> None:
        shell = self.shell
        entry = shell.entries.get(cid)
        job = entry.view.jobs.get(job_id) if entry else None
        if entry is None or job is None:
            shell.app.notify("that job already finished", "info")
            return
        actions = [{"id": "stop", "label": "Stop"}]
        if not job.background:
            actions.append({"id": "background", "label": "Move to background"})
        actions.append({"id": "back", "label": "Back"})
        body = f"`{job.detail}`\n\n{entry.view.name} · running {duration(time.monotonic() - job.started)}"
        choice = await shell.panel(Card(title=job.name, body=body, actions=actions))
        match choice:
            case "stop":
                entry.agent._cancel_job(job_id, job.name)
            case "background":
                if not entry.agent._promote_handle(job_id):
                    shell.app.notify(
                        "this job cannot move to the background", "warning"
                    )

    async def trace(self, cid: str, job_id: str) -> None:
        """A sub-agent's trace, docked at the input; follows it while it runs."""
        shell = self.shell
        entry = shell.entries.get(cid)
        trace = entry.view.traces.get(job_id) if entry else None
        if entry is None or trace is None:
            shell.app.notify("that sub-agent is no longer listed", "info")
            return
        grouped = True
        while True:
            choice = await self._show_trace(entry, trace, grouped)
            match choice:
                case "stop" if trace.running:
                    entry.agent._cancel_job(job_id, trace.name)
                    shell.app.notify(f"stopping {trace.name}", "info")
                case "list calls" | "group calls":
                    grouped = not grouped
                case _:
                    return

    async def _show_trace(self, entry: Any, trace: Trace, grouped: bool) -> Any:
        batch = entry.view.batch_size
        height = trace_height()
        state: dict[str, Node] = {}

        def opened(node: Node) -> None:
            state["box"] = node
            state["body"] = node.add(trace_body(trace, batch if grouped else 0))

        def changed(t: Trace) -> None:
            box = state.get("box")
            if t is not trace or box is None:
                return
            state["body"].remove()
            state["body"] = box.add(trace_body(trace, batch if grouped else 0))
            # the stop key goes away once it finished
            if not trace.running:
                box.update(keys={"e": "list calls" if grouped else "group calls"})

        spec = trace_view(trace, entry.view.name, height, batch, grouped)
        spec.children = []
        stop = entry.view.traces.listen(changed)
        try:
            return await self.shell.panel(spec, on_open=opened)
        finally:
            stop()


def trace_height() -> int:
    """Rows of trace shown at once: most of the screen, leaving the transcript tail visible."""
    rows = shutil.get_terminal_size((100, 30)).lines
    return max(6, min(40, rows * 2 // 3 - 4))
