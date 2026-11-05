"""Full-screen chrome, after the web's v2 session view.

* **Pages** (the tab strip on top): Chat · Graph · Status · Debug · Settings. Chat is the
  conversation; the others are whole pages (``pages.py``).
* **Conversation rail** (left, Chat only): agents, then channels, each with a status mark and
  an unread count. Picking one shows its conversation; a channel's chat sends as ``human``.
* **Side panel** (right, Chat only): what is going on, in sections (``side.py``).

The chrome exists in every mode but only full screen draws it, so inline is unchanged.
Pane widths and collapsed state are saved in ``ktui.json``.
"""

from typing import TYPE_CHECKING, Any

from kohakutui import Event
from kohakutui.ui import Card, Node, Pane, TabStrip, Tree

import kohakuterrarium.builtins.ktui.chrome_data as data
from kohakuterrarium.builtins.ktui.channels import ChannelView
from kohakuterrarium.builtins.ktui.logtail import LogTail
from kohakuterrarium.builtins.ktui.pages import Pages
from kohakuterrarium.builtins.ktui.side import Side

if TYPE_CHECKING:
    from kohakuterrarium.builtins.ktui.shell import KtuiShell

PAGE_TABS = (
    ("chat", "Chat"),
    ("graph", "Graph"),
    ("status", "Status"),
    ("debug", "Debug"),
    ("settings", "Settings"),
)
DEFAULT_WIDTH = {"rail": 22, "side": 34}


class Chrome:
    def __init__(self, shell: "KtuiShell"):
        self.shell = shell
        self.logs = LogTail()
        self.drives = data.Drives(shell)
        self.side = Side(shell, self.drives)
        self.pages = Pages(shell, self.drives, self.logs)
        self.rail: Node | None = None
        self.tree: Node | None = None
        self.tabs: Node | None = None
        self.side_pane: Node | None = None
        self.page = "chat"
        # channel conversations opened so far, and the one shown (None: a creature's)
        self.channels: dict[str, ChannelView] = {}
        self.channel: ChannelView | None = None
        self._shown: tuple = ()

    # -- building -------------------------------------------------------

    def build(self) -> None:
        app = self.shell.app
        rail = Pane(side="left", min=14, title="Chats", **self._saved("rail"))
        self.rail = app.panels.add(rail)
        self.tree = self.rail.add(Tree(items=[]))
        self.tabs = app.head.add(TabStrip(tabs=self._page_tabs(), active=0))
        side = Pane(side="right", min=20, title="Activity", **self._saved("side"))
        self.side_pane = app.panels.add(side)
        self.side.build(self.side_pane)
        self.refresh()

    def _saved(self, name: str) -> dict:
        saved = self.shell.config.panes.get(name, {})
        width = saved.get("width") or DEFAULT_WIDTH[name]
        return {"width": width, "collapsed": bool(saved.get("collapsed", False))}

    def capture(self) -> None:
        """Stray prints, warnings and log records go to the Debug page (every mode)."""
        self.logs.capture()

    def release(self) -> None:
        self.logs.release()
        self.drives.close()

    # -- state ----------------------------------------------------------

    def refresh(self) -> None:
        """Brings what is drawn up to date; inline nothing is, so nothing is done."""
        shell = self.shell
        if self.tree is None or not shell.entries or shell.mode != "fullscreen":
            return
        items, tabs = self._rail_items(), self._page_tabs()
        shown = (repr(items), repr(tabs))
        if shown != self._shown:
            self._shown = shown
            self.tree.update(items=items)
            self.tabs.update(tabs=tabs)
        if self.page == "chat":
            self.side.refresh()
        else:
            self.pages.refresh()

    def _page_tabs(self) -> list[dict]:
        unread = sum(e.view.unread for e in self.shell.entries.values())
        unread += sum(c.unread for c in self.channels.values())
        warnings = self.logs.unseen
        badges = {
            "chat": str(unread) if unread and self.page != "chat" else None,
            "debug": str(warnings) if warnings and self.page != "debug" else None,
        }
        return [
            {"label": label, "value": value, "badge": badges.get(value)}
            for value, label in PAGE_TABS
        ]

    def _status(self, entry: Any) -> tuple[str, str]:
        shell = self.shell
        if any(p.view is entry.view for p in shell.interactions.items):
            return "!", "warn"
        if shell.is_busy(entry):
            return "⚙", "info"
        return "●", "success"

    def _rail_items(self) -> list[dict]:
        shell = self.shell
        agents = []
        for cid, entry in shell.entries.items():
            icon, tone = self._status(entry)
            current = cid == shell.focus_id and self.channel is None
            agents.append(
                {
                    "label": entry.view.name + ("  ◂" if current else ""),
                    "value": f"creature:{cid}",
                    "icon": icon,
                    "tone": tone,
                    "detail": str(entry.view.unread) if entry.view.unread else None,
                }
            )
        items = [
            {
                "label": f"agents  {len(agents)}",
                "value": "group:agents",
                "open": True,
                "children": agents,
            }
        ]
        names = self._channel_names()
        if names:
            channels = []
            for name in names:
                tab = self.channels.get(name)
                current = tab is not None and tab is self.channel
                unread = tab.unread if tab else 0
                channels.append(
                    {
                        "label": f"#{name}" + ("  ◂" if current else ""),
                        "value": f"channel:{name}",
                        "tone": "info" if current else None,
                        "detail": str(unread) if unread else None,
                    }
                )
            items.append(
                {
                    "label": f"channels  {len(channels)}",
                    "value": "group:channels",
                    "open": True,
                    "children": channels,
                }
            )
        return items

    def _channel_names(self) -> list[str]:
        entry = self.shell.entries.get(self.shell.focus_id)
        return ChannelView.names(self.shell.engine, entry) if entry else []

    def _conversations(self) -> list[str]:
        """Rail order: agents, then channels."""
        values = [f"creature:{cid}" for cid in self.shell.entries]
        return values + [f"channel:{n}" for n in self._channel_names()]

    def _current(self) -> str:
        if self.channel is not None:
            return f"channel:{self.channel.name}"
        return f"creature:{self.shell.focus_id}"

    # -- events ---------------------------------------------------------

    def handle(self, ev: Event) -> bool:
        """Takes the chrome's own events: pane resizes, rail, page tabs, panel and page lists."""
        if ev.kind == "pane":
            return self._pane(ev)
        if self.tabs is not None and ev.id == self.tabs.id:
            if ev.kind == "select" and ev.value:
                self.show_page(str(ev.value))
            return True
        if self.tree is not None and ev.id == self.tree.id:
            if ev.kind == "select":
                self.open(str(ev.value or ""))
            return True
        if ev.id in self.side.lists() or ev.id in self.pages.trees():
            if ev.kind == "select":
                self.choose(str(ev.value or ""))
            return True
        name = self.pages.fields().get(ev.id)
        if name is not None:
            if ev.kind == "change" and ev.value is not None:
                self.shell.spawn(self.shell.settings.apply(name, str(ev.value)))
            return True
        return False

    def _pane(self, ev: Event) -> bool:
        for name, node in (("rail", self.rail), ("side", self.side_pane)):
            if node is not None and ev.id == node.id:
                self.shell.config.panes[name] = {
                    "width": int(ev.data.get("width", 0) or 0),
                    "collapsed": bool(ev.data.get("collapsed", False)),
                }
                self.shell.config.save()
                return True
        return False

    def choose(self, value: str) -> None:
        """An entry picked in the side panel or on a page."""
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
                self.open(value)
            case "model" | "key" | "info":
                shell.spawn(self._setting(value))

    async def _setting(self, value: str) -> None:
        await self.shell.settings.act(value)
        # a model or key changed in the dialog: the page shows the new value
        if self.page == "settings":
            self.pages.refresh(force=True)

    def open(self, value: str) -> None:
        """Shows a conversation: ``creature:<id>`` or ``channel:<name>``."""
        kind, _, rest = value.partition(":")
        match kind:
            case "creature":
                self.shell.focus(rest)
            case "channel":
                self.show_channel(rest)
        self.refresh()

    # -- pages ----------------------------------------------------------

    def show_page(self, page: str) -> None:
        shell = self.shell
        values = [v for v, _ in PAGE_TABS]
        if shell.mode != "fullscreen":
            # pages are full screen only (a page shown inline would go to scrollback)
            return
        if page == "chat":
            self.to_chat()
            return
        if page == self.page or page not in values:
            return
        self.page = page
        self._panes(hidden=True)
        # keys left in a hidden panel come back to the input
        if shell.composer is not None:
            shell.composer.focus()
        # a page opens at its top; Debug follows its newest lines like a chat
        shell.app.show_document(self.pages.doc(page), top=page != "debug")
        self.tabs.update(active=values.index(page))
        self.refresh()

    def to_chat(self) -> None:
        """Back to the Chat page with the current conversation (no-op when there)."""
        if self.page == "chat":
            return
        self.page = "chat"
        self.pages.shown = None
        self._panes(hidden=False)
        if self.tabs is not None:
            self.tabs.update(active=0)
        doc = self.channel.doc if self.channel else self.shell.focused.view.doc
        self.shell.app.show_document(doc)
        self.refresh()

    def _panes(self, hidden: bool) -> None:
        for node in (self.rail, self.side_pane):
            if node is not None:
                node.update(hidden=hidden)

    # -- channels -------------------------------------------------------

    def show_channel(self, name: str) -> None:
        shell = self.shell
        self.to_chat()
        tab = self.channels.get(name)
        if tab is None:
            tab = ChannelView(shell, name)
            self.channels[name] = tab
        if tab is self.channel:
            return
        if self.channel is not None:
            self.channel.focused = False
        else:
            # the creature keeps working unseen: its unread count and notices run
            shell.focused.view.set_focused(False)
        self.channel = tab
        tab.focused = True
        tab.unread = 0
        shell.app.show_document(tab.doc, f"── #{name} ──")
        shell.refresh()

    def leave_channel(self) -> bool:
        """Stops showing the channel (the caller shows a creature); False when none was."""
        if self.channel is None:
            return False
        self.channel.focused = False
        self.channel = None
        return True

    def channel_changed(self, tab: ChannelView) -> None:
        self.refresh()

    async def submit(self, text: str) -> bool:
        """Composer text: to the shown channel (True), else the caller sends it."""
        self.to_chat()
        if self.channel is None:
            return False
        await self.channel.send(text)
        return True

    # -- keys -----------------------------------------------------------

    def action(self, name: str) -> None:
        """``toggle_rail`` / ``toggle_side`` collapse or open a panel (saved),
        ``page_<n>`` opens the n-th page, ``next_chat`` / ``prev_chat`` step through the
        conversations in rail order."""
        match name:
            case "toggle_rail" | "toggle_side":
                pane = "rail" if name == "toggle_rail" else "side"
                node = self.rail if pane == "rail" else self.side_pane
                if node is None:
                    return
                saved = self.shell.config.panes.setdefault(pane, self._saved(pane))
                saved["collapsed"] = not saved.get("collapsed", False)
                node.update(collapsed=saved["collapsed"])
                self.shell.config.save()
            case _ if name.startswith("page_"):
                index = int(name.removeprefix("page_")) - 1
                if 0 <= index < len(PAGE_TABS):
                    self.show_page(PAGE_TABS[index][0])
            case "next_chat" | "prev_chat":
                values = self._conversations()
                if not values:
                    return
                current = self._current()
                at = values.index(current) if current in values else 0
                step = 1 if name == "next_chat" else -1
                self.open(values[(at + step) % len(values)])

    async def show_logs(self) -> None:
        """``/logs``: the log tail docked at the input (inline has no Debug page)."""
        lines = self.logs.snapshot(40)
        self.logs.unseen = 0
        body = "```\n" + ("\n".join(lines) or "no log lines yet") + "\n```"
        await self.shell.panel(
            Card(title="Logs", body=body, actions=[{"id": "close", "label": "Close"}])
        )

    def close(self) -> None:
        for tab in self.channels.values():
            tab.close()
        self.channels.clear()
        self.release()
