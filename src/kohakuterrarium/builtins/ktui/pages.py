"""Full-screen pages beside Chat, as the web's session tabs: Graph, Status, Debug and
Settings. Each is a kohakutui document shown where the chat is, kept up to date while shown.

* **Graph**: the focused graph as a tree (enter on a creature or channel opens its chat),
  and its edits: add a creature or a channel, connect or disconnect a channel.
* **Status** (``page_status.py``): Session, Usage, context bars, Health, then Agents,
  Channels, Drives and Jobs whose entries act (open, switch model, stop / start, details).
* **Debug** (``page_debug.py``): logs, recorded events, the system prompt, the tool trace.
* **Settings**: the session sections (``page_settings.py``: model per creature, modules,
  workspace, triggers, cost, extensions, env), then ktui's display choices as fields (←/→
  change, applied at once), models, API keys, providers and MCP servers.
"""

import time
from typing import TYPE_CHECKING

from kohakutui.ui import Node

import kohakuterrarium.builtins.ktui.chrome_data as data
from kohakuterrarium.builtins.ktui.graph_edit import GRAPH_ACTIONS
from kohakuterrarium.builtins.ktui.logtail import LogTail
from kohakuterrarium.builtins.ktui.page_debug import DebugPage
from kohakuterrarium.builtins.ktui.page_settings import SessionSettings
from kohakuterrarium.builtins.ktui.page_status import status_sections
from kohakuterrarium.builtins.ktui.side import Section, Sections

if TYPE_CHECKING:
    from kohakuterrarium.builtins.ktui.shell import KtuiShell

PAGES = ("graph", "status", "debug", "settings")


class Pages:
    def __init__(self, shell: "KtuiShell", drives: data.Drives, logs: LogTail):
        self.shell = shell
        self.drives = drives
        self.logs = logs
        self.debug = DebugPage(shell, logs)
        self.session = SessionSettings(shell)
        self.docs: dict[str, Node] = {}
        self.sections: dict[str, Sections] = {}
        self.shown: str | None = None
        self.started = time.monotonic()

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
        if page == "debug" and not force and not self.debug.changed():
            return
        # settings change only through the page itself (or a dialog it opened)
        if page == "settings" and not force:
            return
        try:
            content = self._content(page)
        except Exception as exc:  # a page must never take the UI down
            content = [("error", None, "text", f"unavailable: {exc}")]
        self.sections[page].show(content)

    def field_changed(self, name: str, value: str) -> bool:
        """A page's own fields (the debug view, the settings target); False for others."""
        match name:
            case "debug_view":
                self.debug.view = value
            case "target":
                self.session.choose_target(value)
            case _:
                return False
        self.refresh(force=True)
        # the page may have been laid out again: the keys stay on the field just used
        node = self.sections[self.shown].field_node(name) if self.shown else None
        if node is not None:
            node.focus()
        # another view of the page starts at its top (Debug's logs follow their end)
        self.shell.app.scroll_to(top=not (name == "debug_view" and value == "logs"))
        return True

    def _content(self, page: str) -> list[Section]:
        shell = self.shell
        match page:
            case "graph":
                tree = data.graph_tree(shell)
                hint = "enter or a click on a creature or channel opens its chat"
                return [
                    ("edit", None, "tree", GRAPH_ACTIONS),
                    ("tree", "Graph", "tree", tree),
                    ("hint", None, "text", hint),
                ]
            case "status":
                self.drives.poll(lambda: self.refresh())
                return status_sections(shell, self.drives, self.started)
            case "settings":
                return self._settings()
            case "debug":
                return self.debug.sections()
        return []

    def _settings(self) -> list[Section]:
        settings = self.shell.settings
        out: list[Section] = list(self.session.sections())
        out.append(
            ("display", "Display · ←/→ change", "fields", settings.display_fields())
        )
        for title, rows in settings.sections():
            # each creature's model is in the Model section above
            rows = [r for r in rows if r["value"] != "model:session"]
            if rows:
                out.append((title, title, "tree", rows))
        hint = "tab or F6 moves between entries · enter acts on one"
        out.append(("hint", None, "text", hint))
        return out
