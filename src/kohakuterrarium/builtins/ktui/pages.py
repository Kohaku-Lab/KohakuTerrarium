"""Full-screen pages beside Chat, as the web's session tabs: Graph, Status, Debug and
Settings. Each is a kohakutui document shown where the chat is, kept up to date while shown.

* **Graph**: the focused graph as a tree; enter or a click on a creature or channel opens
  its chat.
* **Status**: the focused creature's figures, then tables of agents, channels, sub-agents,
  jobs and drives.
* **Debug**: the log tail (KT's log, warnings, stray output), newest at the bottom.
* **Settings**: the display choices as fields (←/→ change, applied at once), then models,
  API keys, providers and MCP servers (enter acts: pick a model, set a key).
"""

from typing import TYPE_CHECKING

from kohakutui.ui import Node

import kohakuterrarium.builtins.ktui.chrome_data as data
from kohakuterrarium.builtins.ktui.logtail import LogTail
from kohakuterrarium.builtins.ktui.side import Section, Sections

if TYPE_CHECKING:
    from kohakuterrarium.builtins.ktui.shell import KtuiShell

PAGES = ("graph", "status", "debug", "settings")
LOG_SHOWN = 400


def _table(columns: list[str], rows: list[dict]) -> tuple[list, list]:
    return ([{"key": c, "label": c} for c in columns], rows)


class Pages:
    def __init__(self, shell: "KtuiShell", drives: data.Drives, logs: LogTail):
        self.shell = shell
        self.drives = drives
        self.logs = logs
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

    def fields(self) -> dict[int, str]:
        return {i: n for s in self.sections.values() for i, n in s.fields.items()}

    def refresh(self, force: bool = False) -> None:
        """Redraws the shown page (only what changed)."""
        page = self.shown
        if page is None or page not in self.sections:
            return
        if page == "debug" and not force and self.logs.count == self._log_count:
            return
        # settings change only through the page itself (or a dialog it opened)
        if page == "settings" and not force:
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
            case "settings":
                return self._settings()
            case "debug":
                self._log_count = self.logs.count
                lines = self.logs.snapshot(LOG_SHOWN)
                self.logs.unseen = 0
                body = "\n".join(lines) or "no log lines yet"
                return [("log", "Log · KT, warnings and stray output", "text", body)]
        return []

    def _settings(self) -> list[Section]:
        settings = self.shell.settings
        out: list[Section] = [
            ("display", "Display · ←/→ change", "fields", settings.display_fields())
        ]
        for title, rows in settings.sections():
            out.append((title, title, "tree", rows))
        hint = "tab or F6 moves between entries · enter acts on one"
        out.append(("hint", None, "text", hint))
        return out

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
