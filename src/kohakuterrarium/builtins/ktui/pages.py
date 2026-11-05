"""Full-screen pages beside Chat, as the web's session tabs: Graph, Status, Workspace and
Debug. Each is a kohakutui document shown where the chat is, kept up to date while shown.

* **Graph**: the focused graph as a tree; enter or a click on a creature or channel opens
  its chat.
* **Status**: the focused creature's figures, then tables of agents, channels, sub-agents,
  jobs and drives.
* **Workspace**: the working directory (folders open on demand); enter previews a file.
* **Debug**: the log tail (KT's log, warnings, stray output), newest at the bottom.
"""

from typing import TYPE_CHECKING

from kohakutui.ui import Node

import kohakuterrarium.builtins.ktui.chrome_data as data
from kohakuterrarium.builtins.ktui.aux_files import FileTree
from kohakuterrarium.builtins.ktui.logtail import LogTail
from kohakuterrarium.builtins.ktui.side import Section, Sections

if TYPE_CHECKING:
    from kohakuterrarium.builtins.ktui.shell import KtuiShell

PAGES = ("graph", "status", "workspace", "debug")
LOG_SHOWN = 400


def _table(columns: list[str], rows: list[dict]) -> tuple[list, list]:
    return ([{"key": c, "label": c} for c in columns], rows)


class Pages:
    def __init__(self, shell: "KtuiShell", drives: data.Drives, logs: LogTail):
        self.shell = shell
        self.drives = drives
        self.logs = logs
        self.files = FileTree()
        self.docs: dict[str, Node] = {}
        self.sections: dict[str, Sections] = {}
        self.shown: str | None = None
        self._log_count = -1

    def doc(self, page: str) -> Node:
        """The page's document, made and filled the first time it is asked for."""
        if page not in self.docs:
            self.docs[page] = self.shell.app.add_document()
            self.sections[page] = Sections(self.docs[page], spaced=False)
        self.shown = page
        self.refresh(force=True)
        return self.docs[page]

    def trees(self) -> set[int]:
        return {i for s in self.sections.values() for i in s.trees()}

    def refresh(self, force: bool = False) -> None:
        """Redraws the shown page (only what changed)."""
        page = self.shown
        if page is None or page not in self.sections:
            return
        if page == "debug" and not force and self.logs.count == self._log_count:
            return
        try:
            content = self._content(page)
        except Exception as exc:  # a page must never take the UI down
            content = [("error", None, "text", f"unavailable: {exc}")]
        self.sections[page].show(content)

    def _content(self, page: str) -> list[Section]:
        shell = self.shell
        match page:
            case "graph":
                tree = data.graph_tree(shell)
                hint = "enter or a click on a creature or channel opens its chat"
                return [
                    ("tree", "Graph", "tree", tree),
                    ("hint", None, "text", hint),
                ]
            case "status":
                return self._status()
            case "workspace":
                items = self.files.items()
                return [
                    ("tree", str(self.files.root), "tree", items),
                    ("hint", None, "text", "→ opens a folder · enter previews a file"),
                ]
            case "debug":
                self._log_count = self.logs.count
                lines = self.logs.snapshot(LOG_SHOWN)
                self.logs.unseen = 0
                body = "\n".join(lines) or "no log lines yet"
                return [("log", "Log · KT, warnings and stray output", "text", body)]
        return []

    def _status(self) -> list[Section]:
        shell = self.shell
        out: list[Section] = [
            ("focus", shell.focused.view.name, "kv", data.status_fields(shell))
        ]
        tables = [
            (
                "agents",
                "Agents",
                ["agent", "state", "model", "context", "in", "out", "unread"],
                data.agent_rows(shell),
            ),
            (
                "channels",
                "Channels",
                ["channel", "members", "about"],
                data.channel_rows(shell),
            ),
            ("subagents", "Sub-agents", ["agent", "state"], data.subagent_rows(shell)),
            (
                "jobs",
                "Jobs",
                ["job", "kind", "where", "agent", "time"],
                data.job_rows(shell),
            ),
        ]
        self.drives.poll(lambda: self.refresh())
        tables.append(
            (
                "drives",
                "Drives",
                ["drive", "status", "kind", "assignee"],
                self.drives.table(),
            )
        )
        for key, title, columns, rows in tables:
            if rows:
                out.append(
                    (key, f"{title} · {len(rows)}", "table", _table(columns, rows))
                )
        return out
