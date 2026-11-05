"""What the full-screen chrome shows, read from the shell: running tools, sub-agents, the
graph, the scratchpad, open widgets, drives and status figures. Plain data (tree items, rows);
``side.py`` and ``pages.py`` draw it."""

import time
from typing import TYPE_CHECKING, Any

from kohakuterrarium.builtins.cli_rich.dialogs.drive.format import (
    project_view,
    status_label,
)
from kohakuterrarium.builtins.ktui.format import duration, one_line, tokens
from kohakuterrarium.builtins.ktui.trace import board_detail, board_label
from kohakuterrarium.terrarium.drive.models import ActorRef
from kohakuterrarium.terrarium.service.service import LocalTerrariumService
from kohakuterrarium.utils.logging import get_logger

if TYPE_CHECKING:
    from kohakuterrarium.builtins.ktui.shell import KtuiShell

logger = get_logger(__name__)

# drives are read from the service at most this often (seconds)
DRIVE_EVERY = 3.0
# finished sub-agents listed beside the running ones
FINISHED_SHOWN = 5


def names(shell: "KtuiShell") -> dict[str, str]:
    return {cid: e.view.name for cid, e in shell.entries.items()}


def graph_of(shell: "KtuiShell") -> Any:
    entry = shell.entries.get(shell.focus_id)
    try:
        return shell.engine.get_graph(entry.creature.graph_id) if entry else None
    except (KeyError, AttributeError):
        return None


def jobs(shell: "KtuiShell") -> list[dict]:
    """Running tools of every creature, as tree items (enter: Stop / Background)."""
    out = []
    now = time.monotonic()
    many = len(shell.entries) > 1
    for cid, entry in shell.entries.items():
        for job in entry.view.jobs.values():
            if job.kind != "tool":
                continue
            where = f" · {entry.view.name}" if many else ""
            out.append(
                {
                    "label": job.name,
                    "icon": "⏵" if job.background else "⚙",
                    "tone": "info",
                    "detail": f"{duration(now - job.started)}{where}",
                    "value": f"job:{cid}:{job.job_id}",
                }
            )
    return out


def subagents(shell: "KtuiShell") -> list[dict]:
    """Running sub-agents, then the last few finished ones (enter: the trace)."""
    running, done = [], []
    for cid, entry in shell.entries.items():
        for trace in reversed(list(entry.view.traces.items.values())):
            item = {
                "label": one_line(board_label(trace), 60),
                "tone": "info" if trace.running else None,
                "value": f"trace:{cid}:{trace.job_id}",
            }
            (running if trace.running else done).append(item)
    return running + done[:FINISHED_SHOWN]


def running_subagents(shell: "KtuiShell") -> int:
    return sum(
        1
        for e in shell.entries.values()
        for t in e.view.traces.items.values()
        if t.running
    )


def subagent_rows(shell: "KtuiShell") -> list[dict]:
    rows = []
    for _, entry in shell.entries.items():
        for trace in reversed(list(entry.view.traces.items.values())):
            rows.append(
                {
                    "agent": trace.name,
                    "state": board_detail(trace, entry.view.name),
                }
            )
    return rows


def members(shell: "KtuiShell", channel: str) -> list[dict]:
    """Who listens and who sends on ``channel``."""
    graph = graph_of(shell)
    if graph is None:
        return []
    known = names(shell)
    out = []
    for cid in sorted(graph.creature_ids, key=lambda c: known.get(c, c)):
        listens = channel in graph.listen_edges.get(cid, ())
        sends = channel in graph.send_edges.get(cid, ())
        if listens or sends:
            how = " · ".join(
                w for w, on in (("listens", listens), ("sends", sends)) if on
            )
            out.append(
                {
                    "label": known.get(cid, cid),
                    "icon": "●",
                    "detail": how,
                    "value": f"creature:{cid}",
                }
            )
    return out


def graph_tree(shell: "KtuiShell") -> list[dict]:
    """The focused graph: creatures with what they listen and send on, channels with members."""
    graph = graph_of(shell)
    if graph is None:
        return []
    known = names(shell)
    creatures = []
    for cid in sorted(graph.creature_ids, key=lambda c: known.get(c, c)):
        listen = sorted(graph.listen_edges.get(cid, ()))
        send = sorted(graph.send_edges.get(cid, ()))
        parent = graph.parent_links.get(cid)
        kids = [{"label": f"listens  #{c}", "value": f"channel:{c}"} for c in listen]
        kids += [{"label": f"sends    #{c}", "value": f"channel:{c}"} for c in send]
        if parent:
            kids.append(
                {
                    "label": f"child of {known.get(parent, parent)}",
                    "value": f"creature:{parent}",
                }
            )
        busy = cid in shell.entries and shell.is_busy(shell.entries[cid])
        creatures.append(
            {
                "label": known.get(cid, cid),
                "icon": "⚙" if busy else "●",
                "tone": "info" if busy else "success",
                "value": f"creature:{cid}",
                "open": True,
                "children": kids or None,
            }
        )
    channels = []
    for name, info in sorted(graph.channels.items()):
        who = members(shell, name)
        channels.append(
            {
                "label": f"#{name}",
                "value": f"channel:{name}",
                "detail": one_line(info.description, 40) if info.description else None,
                "open": True,
                "children": [
                    {"label": f"{m['label']}  {m['detail']}", "value": m["value"]}
                    for m in who
                ]
                or None,
            }
        )
    return [
        {
            "label": f"creatures  ({len(creatures)})",
            "value": "group:creatures",
            "open": True,
            "children": creatures or None,
        },
        {
            "label": f"channels  ({len(channels)})",
            "value": "group:channels",
            "open": True,
            "children": channels or None,
        },
    ]


def scratchpad(shell: "KtuiShell") -> list[dict]:
    pad = getattr(shell.focused.agent, "scratchpad", None)
    data = pad.to_dict() if pad is not None else {}
    return [{"label": k, "value": one_line(str(v), 200)} for k, v in data.items()]


def widgets(shell: "KtuiShell") -> list[dict]:
    out = []
    for key, opened in shell.tool_widgets.open.items():
        title = getattr(opened.block, "title", None) or key
        out.append({"label": str(title), "icon": "▣", "value": f"widget:{key}"})
    return out


def status_fields(shell: "KtuiShell") -> list[dict]:
    """The focused creature's figures."""
    entry = shell.focused
    view = entry.view
    usage = view.usage
    pct = usage.context_pct()
    context = f"{pct}% of {tokens(usage.max_context)}" if pct is not None else "—"
    rows = [
        ("creature", view.name),
        ("model", usage.model or "?"),
        ("state", "running" if shell.is_busy(entry) else "idle"),
        ("context", context),
        ("tokens in", f"{tokens(usage.tokens_in)} (cache {tokens(usage.cached)})"),
        ("tokens out", tokens(usage.tokens_out)),
        ("queued", str(view.queued_count)),
    ]
    return [{"label": k, "value": v} for k, v in rows]


def agent_rows(shell: "KtuiShell") -> list[dict]:
    rows = []
    for _, entry in shell.entries.items():
        view, usage = entry.view, entry.view.usage
        pct = usage.context_pct()
        rows.append(
            {
                "agent": view.name,
                "state": "running" if shell.is_busy(entry) else "idle",
                "model": one_line(usage.model or "?", 32),
                "context": f"{pct}%" if pct is not None else "—",
                "in": tokens(usage.tokens_in),
                "out": tokens(usage.tokens_out),
                "unread": str(view.unread or ""),
            }
        )
    return rows


def channel_rows(shell: "KtuiShell") -> list[dict]:
    graph = graph_of(shell)
    if graph is None:
        return []
    rows = []
    for name, info in sorted(graph.channels.items()):
        who = members(shell, name)
        rows.append(
            {
                "channel": f"#{name}",
                "members": ", ".join(m["label"] for m in who) or "—",
                "about": one_line(info.description or "", 40),
            }
        )
    return rows


def job_rows(shell: "KtuiShell") -> list[dict]:
    now = time.monotonic()
    rows = []
    for _, entry in shell.entries.items():
        for job in entry.view.jobs.values():
            rows.append(
                {
                    "job": job.name,
                    "kind": job.kind,
                    "where": "background" if job.background else "foreground",
                    "agent": entry.view.name,
                    "time": duration(now - job.started),
                }
            )
    return rows


class Drives:
    """The focused graph's drives, read from the service in the background (never more often
    than every few seconds); ``rows`` is what the last read found."""

    def __init__(self, shell: "KtuiShell"):
        self.shell = shell
        self.rows: list[dict] = []
        self.error = ""
        self._at = 0.0
        self._task: Any = None

    def poll(self, on_change: Any) -> None:
        """Starts a read when the last one is old; ``on_change`` runs when it lands."""
        if getattr(self.shell.engine, "drives", None) is None:
            return
        busy = self._task is not None and not self._task.done()
        if busy or time.monotonic() - self._at < DRIVE_EVERY:
            return
        self._at = time.monotonic()
        self._task = self.shell.spawn(self._read(on_change))

    async def _read(self, on_change: Any) -> None:
        # a failure is shown where drives are, never raised (it would land in the transcript)
        try:
            views = await LocalTerrariumService(self.shell.engine).list_drives(
                actor=ActorRef("user", "local"),
                graph_id=self.shell.focused.creature.graph_id,
                include_terminal=True,
                is_privileged=True,
            )
            rows, error = [project_view(v) for v in views], ""
        except Exception as exc:
            logger.debug("drives not listed", error=str(exc))
            rows, error = [], one_line(str(exc), 60)
        if (rows, error) != (self.rows, self.error):
            self.rows, self.error = rows, error
            on_change()

    def items(self) -> list[dict]:
        known = names(self.shell)
        out = []
        for r in self.rows:
            assignee = str(r["assignee_creature_id"] or "")
            out.append(
                {
                    "label": one_line(str(r["title"]), 40),
                    "detail": status_label(str(r["status"])),
                    "value": f"drive:{r['drive_id']}",
                    "children": [
                        {
                            "label": f"{r['kind']} · {known.get(assignee, assignee or '—')}",
                            "value": f"drive:{r['drive_id']}",
                        }
                    ]
                    + (
                        [
                            {
                                "label": one_line(str(r["status_reason"]), 60),
                                "value": f"drive:{r['drive_id']}",
                            }
                        ]
                        if r.get("status_reason")
                        else []
                    ),
                }
            )
        return out

    def table(self) -> list[dict]:
        known = names(self.shell)
        return [
            {
                "drive": one_line(str(r["title"]), 40),
                "status": status_label(str(r["status"])),
                "kind": str(r["kind"]),
                "assignee": known.get(
                    str(r["assignee_creature_id"] or ""),
                    str(r["assignee_creature_id"] or "—"),
                ),
            }
            for r in self.rows
        ]

    def close(self) -> None:
        if self._task is not None:
            self._task.cancel()
