"""The aux pane (full screen, right): a tab strip over the chosen tab's body.

Tabs, after the web's side panels and kt-tui's tabs:

* **Jobs** — running tools of every creature; enter: Stop / Background.
* **Agents** — sub-agents, running and finished; enter: the live trace.
* **Status** — the focused creature: model, context, tokens, engine state.
* **Graph** — the focused graph: creatures with what they listen and send on, channels with
  their members; enter on a creature focuses it, on a channel opens its tab.
* **Pad** — the focused creature's scratchpad.
* **Drives** — the graph's drives (goals) with their status.
* **Widgets** — the open tool widgets.
* **Files** — the working directory as a tree (directories open on demand); enter previews a
  file.
* **Logs** — the newest log lines of this process.

The body is rebuilt only when what it shows changed.
"""

import time
from typing import TYPE_CHECKING, Any

from kohakutui import Event
from kohakutui.ui import KV, Card, Node, TabStrip, Text, Tree

from kohakuterrarium.builtins.cli_rich.dialogs.drive.format import (
    project_view,
    status_label,
)
from kohakuterrarium.builtins.ktui.aux_files import FileTree
from kohakuterrarium.builtins.ktui.logtail import LogTail
from kohakuterrarium.builtins.ktui.format import duration, one_line, tokens
from kohakuterrarium.builtins.ktui.trace import board_detail, board_label
from kohakuterrarium.terrarium.drive.models import ActorRef
from kohakuterrarium.terrarium.service.service import LocalTerrariumService
from kohakuterrarium.utils.logging import get_logger

if TYPE_CHECKING:
    from kohakuterrarium.builtins.ktui.shell import KtuiShell

logger = get_logger(__name__)

AUX_TABS = (
    ("jobs", "Jobs"),
    ("agents", "Agents"),
    ("status", "Status"),
    ("graph", "Graph"),
    ("scratchpad", "Pad"),
    ("drives", "Drives"),
    ("widgets", "Widgets"),
    ("files", "Files"),
    ("logs", "Logs"),
)
# drives are loaded from the service at most this often (seconds)
DRIVE_EVERY = 3.0


class Aux:
    """The aux pane's tab strip and body."""

    def __init__(self, shell: "KtuiShell"):
        self.shell = shell
        self.tab = (
            shell.config.aux_tab if shell.config.aux_tab in dict(AUX_TABS) else "jobs"
        )
        self.strip: Node | None = None
        self.body: Node | None = None
        self.list: Node | None = None
        self._shown: str = ""
        self.files = FileTree()
        self._logs = LogTail(self._log_line)
        self._log_dirty = False
        self._drives: list[dict] = []
        self._drives_at = 0.0
        self._drive_task: Any = None
        self._tabs_shown: list[dict] = []

    def _tab_items(self) -> list[dict]:
        """The tabs; Logs carries the count of warnings not seen yet."""
        unseen = self._logs.unseen if self.tab != "logs" else 0
        return [
            {
                "label": label,
                "value": value,
                "badge": str(unseen) if value == "logs" and unseen else None,
            }
            for value, label in AUX_TABS
        ]

    def build(self, pane: Node) -> None:
        active = [v for v, _ in AUX_TABS].index(self.tab)
        self._tabs_shown = self._tab_items()
        self.strip = pane.add(TabStrip(tabs=self._tabs_shown, active=active))
        self.pane = pane
        self.refresh(force=True)

    def capture(self) -> None:
        """Stray prints, warnings and log records go to the Logs tab (every mode)."""
        self._logs.capture()

    def close(self) -> None:
        self._logs.release()
        if self._drive_task is not None:
            self._drive_task.cancel()

    async def show_logs(self) -> None:
        """``/logs``: the tail docked at the input (inline has no aux pane)."""
        lines = self._logs.snapshot(40)
        self._logs.unseen = 0
        body = "```\n" + ("\n".join(lines) or "no log lines yet") + "\n```"
        await self.shell.panel(
            Card(title="Logs", body=body, actions=[{"id": "close", "label": "Close"}])
        )

    # -- events ---------------------------------------------------------

    def handle(self, ev: Event) -> bool:
        if self.strip is not None and ev.id == self.strip.id:
            if ev.kind == "select" and ev.value:
                self.tab = str(ev.value)
                self.shell.config.aux_tab = self.tab
                self.shell.config.save()
                self.refresh(force=True)
            return True
        if self.list is None or ev.id != self.list.id:
            return False
        if ev.kind == "change" and self.tab == "files":
            path = str((ev.data.get("value") or {}).get("expand", ""))
            if path and self.files.expand(path):
                self.refresh(force=True)
        elif ev.kind == "select":
            self._choose(str(ev.value or ""))
        return True

    def _choose(self, value: str) -> None:
        shell = self.shell
        kind, _, rest = value.partition(":")
        match kind:
            case "job":
                cid, _, job_id = rest.partition(":")
                shell.spawn(shell.board.job(cid, job_id))
            case "trace":
                cid, _, job_id = rest.partition(":")
                shell.spawn(shell.board.trace(cid, job_id))
            case "creature" | "channel":
                shell.chrome.open(value)
            case "file":
                shell.spawn(self.files.preview(shell, rest))

    def _log_line(self) -> None:
        self._log_dirty = True

    # -- body -----------------------------------------------------------

    def refresh(self, force: bool = False) -> None:
        """Rebuilds the body when its content changed (or ``force``)."""
        if self.strip is None:
            return
        tabs = self._tab_items()
        if tabs != self._tabs_shown:
            self._tabs_shown = tabs
            self.strip.update(tabs=tabs)
        if self.tab == "logs" and not (force or self._log_dirty):
            return
        self._log_dirty = False
        try:
            spec, rows = self._body()
        except Exception as exc:  # a tab must never take the UI down
            logger.debug("aux tab failed", tab=self.tab, error=str(exc), exc_info=True)
            spec, rows = Text(f"unavailable: {exc}", tone="faint"), None
        key = repr((self.tab, spec))
        if key == self._shown and not force:
            return
        self._shown = key
        if self.body is not None and self.list is not None and rows is not None:
            # same kind of body: update the tree in place so the cursor stays
            if self.body.kind == "tree" and spec.kind == "tree":
                self.list.update(items=rows)
                return
        if self.body is not None:
            self.body.remove()
        self.body = self.pane.add(spec)
        self.list = self.body if spec.kind == "tree" else None

    def _body(self) -> tuple[Any, list | None]:
        match self.tab:
            case "jobs":
                return self._tree(self._jobs(), "no running tools")
            case "agents":
                return self._tree(self._agents(), "no sub-agents yet")
            case "status":
                return KV(fields=self._status()), None
            case "graph":
                return self._tree(self._graph(), "no graph")
            case "scratchpad":
                return self._scratchpad(), None
            case "drives":
                self._load_drives()
                return self._tree(self._drives, "no drives")
            case "widgets":
                return self._tree(self._widgets(), "no open widgets")
            case "files":
                return self._tree(self.files.items(), "empty directory")
            case "logs":
                self._logs.unseen = 0
                lines = self._logs.snapshot(80)
                body = "\n".join(reversed(lines)) or "no log lines yet"
                return Text(body, tone="faint"), None
        return Text(""), None

    @staticmethod
    def _tree(items: list[dict], empty: str) -> tuple[Any, list | None]:
        if not items:
            return Text(empty, tone="faint"), None
        return Tree(items=items), items

    def _jobs(self) -> list[dict]:
        out = []
        now = time.monotonic()
        for cid, entry in self.shell.entries.items():
            for job in entry.view.jobs.values():
                if job.kind != "tool":
                    continue
                icon = "⏵" if job.background else "⚙"
                out.append(
                    {
                        "label": job.name,
                        "icon": icon,
                        "tone": "info",
                        "detail": f"{duration(now - job.started)} · {entry.view.name}",
                        "value": f"job:{cid}:{job.job_id}",
                        "children": (
                            [
                                {
                                    "label": one_line(job.detail, 60),
                                    "value": f"job:{cid}:{job.job_id}",
                                }
                            ]
                            if job.detail
                            else None
                        ),
                    }
                )
        return out

    def _agents(self) -> list[dict]:
        out = []
        for cid, entry in self.shell.entries.items():
            for trace in reversed(list(entry.view.traces.items.values())):
                out.append(
                    {
                        "label": one_line(board_label(trace), 60),
                        "tone": "info" if trace.running else None,
                        "detail": None,
                        "value": f"trace:{cid}:{trace.job_id}",
                        "children": [
                            {
                                "label": board_detail(trace, entry.view.name),
                                "value": f"trace:{cid}:{trace.job_id}",
                            }
                        ],
                    }
                )
        return out

    def _status(self) -> list[dict]:
        shell = self.shell
        entry = shell.focused
        view = entry.view
        usage = view.usage
        pct = usage.context_pct()
        rows = [
            ("creature", view.name),
            ("model", usage.model or "?"),
            ("state", "running" if shell.is_busy(entry) else "idle"),
            (
                "context",
                f"{pct}% of {tokens(usage.max_context)}" if pct is not None else "—",
            ),
            ("tokens in", f"{tokens(usage.tokens_in)} (cache {tokens(usage.cached)})"),
            ("tokens out", tokens(usage.tokens_out)),
            ("jobs", str(len(view.jobs))),
            ("queued", str(view.queued_count)),
            ("creatures", str(len(shell.entries))),
            ("layout", shell.config.renderer),
        ]
        return [{"label": k, "value": v} for k, v in rows]

    def _graph(self) -> list[dict]:
        shell = self.shell
        try:
            graph = shell.engine.get_graph(shell.focused.creature.graph_id)
        except (KeyError, AttributeError):
            return []
        names = {cid: e.view.name for cid, e in shell.entries.items()}
        creatures = []
        for cid in sorted(graph.creature_ids, key=lambda c: names.get(c, c)):
            listen = sorted(graph.listen_edges.get(cid, ()))
            send = sorted(graph.send_edges.get(cid, ()))
            parent = graph.parent_links.get(cid)
            kids = [{"label": f"listens #{c}", "value": f"channel:{c}"} for c in listen]
            kids += [{"label": f"sends #{c}", "value": f"channel:{c}"} for c in send]
            if parent:
                kids.append(
                    {
                        "label": f"child of {names.get(parent, parent)}",
                        "value": f"creature:{parent}",
                    }
                )
            creatures.append(
                {
                    "label": names.get(cid, cid),
                    "icon": "●",
                    "value": f"creature:{cid}",
                    "children": kids or None,
                }
            )
        channels = []
        for name, info in sorted(graph.channels.items()):
            members = [
                names.get(c, c)
                for c in graph.creature_ids
                if name in graph.listen_edges.get(c, ())
                or name in graph.send_edges.get(c, ())
            ]
            channels.append(
                {
                    "label": f"#{name}",
                    "value": f"channel:{name}",
                    "detail": (
                        one_line(info.description, 30) if info.description else None
                    ),
                    "children": (
                        [{"label": ", ".join(members), "value": f"channel:{name}"}]
                        if members
                        else None
                    ),
                }
            )
        return [
            {
                "label": f"graph {graph.graph_id[:8]}",
                "value": "graph",
                "open": True,
                "children": creatures,
            },
            {
                "label": "channels",
                "value": "channels",
                "open": True,
                "children": channels or None,
            },
        ]

    def _scratchpad(self) -> Any:
        pad = getattr(self.shell.focused.agent, "scratchpad", None)
        data = pad.to_dict() if pad is not None else {}
        if not data:
            return Text("the scratchpad is empty", tone="faint")
        return KV(
            fields=[
                {"label": k, "value": one_line(str(v), 200)} for k, v in data.items()
            ]
        )

    def _widgets(self) -> list[dict]:
        out = []
        for key, opened in self.shell.tool_widgets.open.items():
            title = getattr(opened.block, "title", None) or key
            out.append({"label": str(title), "icon": "▣", "value": f"widget:{key}"})
        return out

    # -- drives ---------------------------------------------------------

    def _load_drives(self) -> None:
        """Starts a reload when the last one is old; the body refreshes when it lands."""
        engine = self.shell.engine
        if getattr(engine, "drives", None) is None:
            return
        busy = self._drive_task is not None and not self._drive_task.done()
        if busy or time.monotonic() - self._drives_at < DRIVE_EVERY:
            return
        self._drives_at = time.monotonic()
        self._drive_task = self.shell.spawn(self._fetch_drives())

    async def _fetch_drives(self) -> None:
        # a failure is shown in the tab, never raised (it would land in the transcript)
        try:
            service = LocalTerrariumService(self.shell.engine)
            views = await service.list_drives(
                actor=ActorRef("user", "local"),
                graph_id=self.shell.focused.creature.graph_id,
                include_terminal=True,
                is_privileged=True,
            )
        except Exception as exc:
            logger.debug("drives not listed", error=str(exc))
            self._drives = [
                {
                    "label": f"drives unavailable: {one_line(str(exc), 60)}",
                    "value": "drive:",
                }
            ]
            if self.tab == "drives":
                self.refresh()
            return
        rows = [project_view(v) for v in views]
        names = {cid: e.view.name for cid, e in self.shell.entries.items()}
        self._drives = [
            {
                "label": one_line(str(r["title"]), 40),
                "detail": status_label(str(r["status"])),
                "value": f"drive:{r['drive_id']}",
                "children": [
                    {
                        "label": f"{r['kind']} · {r['drive_id']}",
                        "value": f"drive:{r['drive_id']}",
                    },
                    {
                        "label": "assignee: "
                        + names.get(
                            str(r["assignee_creature_id"]),
                            str(r["assignee_creature_id"] or "—"),
                        ),
                        "value": f"drive:{r['drive_id']}",
                    },
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
            for r in rows
        ]
        if self.tab == "drives":
            self.refresh()
