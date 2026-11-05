"""Full-screen chrome: the rail, the tab strip over the chat and the aux pane.

* The rail (left pane) lists the graph: each creature with a status dot and unread count, and
  the channels. Enter or a click focuses a creature or opens a channel's tab.
* The tab strip (the head row) has one tab per open conversation: every creature, plus the
  channel tabs the user opened (closable). Selecting a tab shows its document.
* The aux pane (right) has its own tab strip (Jobs, Sub-agents, Status, Graph, Scratchpad,
  Drives, Widgets, Files, Logs, see ``aux_tabs.py``) over the chosen tab's body.

The panes exist in every mode but only full screen draws them, so inline is unchanged; ctrl+t
shows them. Pane widths, collapsed state and the aux tab are saved in ``ktui.json``.
"""

from typing import TYPE_CHECKING, Any

from kohakutui import Event
from kohakutui.ui import Node, Pane, TabStrip, Tree

from kohakuterrarium.builtins.ktui.aux_tabs import Aux
from kohakuterrarium.builtins.ktui.channels import ChannelView

if TYPE_CHECKING:
    from kohakuterrarium.builtins.ktui.shell import KtuiShell

DEFAULT_WIDTH = {"rail": 24, "aux": 36}


class Chrome:
    """Builds the panes once and keeps them in step with the shell's creatures and channels."""

    def __init__(self, shell: "KtuiShell"):
        self.shell = shell
        self.rail: Node | None = None
        self.tree: Node | None = None
        self.tabs: Node | None = None
        self.aux_pane: Node | None = None
        self.aux = Aux(shell)
        # open channel tabs, by channel name, in the order opened
        self.channels: dict[str, ChannelView] = {}
        # the channel whose document is shown (None: the focused creature's)
        self.channel: ChannelView | None = None
        self._shown: tuple = ()

    # -- building -------------------------------------------------------

    def build(self) -> None:
        app = self.shell.app
        rail = self._pane_config("rail")
        self.rail = app.panels.add(Pane(side="left", title="Terrarium", min=14, **rail))
        self.tree = self.rail.add(Tree(items=[]))
        self.tabs = app.head.add(TabStrip(tabs=[]))
        aux = self._pane_config("aux")
        self.aux_pane = app.panels.add(Pane(side="right", min=20, **aux))
        self.aux.build(self.aux_pane)
        self.refresh()

    def _pane_config(self, name: str) -> dict:
        saved = self.shell.config.panes.get(name, {})
        width = saved.get("width") or DEFAULT_WIDTH[name]
        return {"width": width, "collapsed": bool(saved.get("collapsed", False))}

    # -- state ----------------------------------------------------------

    def refresh(self) -> None:
        """Redraws the rail and the tab strip when what they show changed; the aux body too.
        Inline nothing of it is drawn: it catches up when full screen starts."""
        if self.tree is None or self.tabs is None or not self.shell.entries:
            return
        if self.shell.mode != "fullscreen":
            return
        items, tabs = self._rail_items(), self._tabs()
        active = self._active(tabs)
        shown = (repr(items), repr(tabs), active)
        if shown != self._shown:
            self._shown = shown
            self.tree.update(items=items)
            self.tabs.update(tabs=tabs, active=active)
        self.aux.refresh()

    def _status(self, entry: Any) -> tuple[str, str]:
        """(icon, tone) of a creature: running, waiting for the user, or idle."""
        shell = self.shell
        if any(p.view is entry.view for p in shell.interactions.items):
            return "!", "warn"
        if shell.is_busy(entry):
            return "⚙", "info"
        return "●", "success"

    def _rail_items(self) -> list[dict]:
        shell = self.shell
        creatures = []
        for cid, entry in shell.entries.items():
            icon, tone = self._status(entry)
            view = entry.view
            current = cid == shell.focus_id and self.channel is None
            creatures.append(
                {
                    "label": view.name + ("  ◂" if current else ""),
                    "value": f"creature:{cid}",
                    "icon": icon,
                    "tone": tone,
                    "detail": str(view.unread) if view.unread else None,
                }
            )
        items = [
            {
                "label": "creatures",
                "value": "group:creatures",
                "open": True,
                "children": creatures,
            }
        ]
        names = self.shell_channels()
        if names:
            channel_items = []
            for name in names:
                tab = self.channels.get(name)
                current = tab is not None and tab is self.channel
                channel_items.append(
                    {
                        "label": f"#{name}" + ("  ◂" if current else ""),
                        "value": f"channel:{name}",
                        "detail": str(tab.unread) if tab and tab.unread else None,
                        "tone": "info" if current else None,
                    }
                )
            items.append(
                {
                    "label": "channels",
                    "value": "group:channels",
                    "open": True,
                    "children": channel_items,
                }
            )
        return items

    def shell_channels(self) -> list[str]:
        """Channel names in the focused creature's graph."""
        entry = self.shell.entries.get(self.shell.focus_id)
        return ChannelView.names(self.shell.engine, entry) if entry else []

    def _tabs(self) -> list[dict]:
        tabs = []
        for cid, entry in self.shell.entries.items():
            _, tone = self._status(entry)
            unread = entry.view.unread
            tabs.append(
                {
                    "label": entry.view.name,
                    "value": f"creature:{cid}",
                    "dot": tone,
                    "badge": str(unread) if unread else None,
                }
            )
        for name, tab in self.channels.items():
            tabs.append(
                {
                    "label": f"#{name}",
                    "value": f"channel:{name}",
                    "badge": str(tab.unread) if tab.unread else None,
                    "closable": True,
                }
            )
        return tabs

    def _active(self, tabs: list[dict] | None = None) -> int:
        values = [t["value"] for t in (tabs if tabs is not None else self._tabs())]
        current = (
            f"channel:{self.channel.name}"
            if self.channel is not None
            else f"creature:{self.shell.focus_id}"
        )
        return values.index(current) if current in values else 0

    # -- acting ---------------------------------------------------------

    def handle(self, ev: Event) -> bool:
        """Takes the chrome's own events (selects, closes, pane resizes)."""
        if self.aux.handle(ev):
            return True
        if ev.kind == "pane":
            for name, node in (("rail", self.rail), ("aux", self.aux_pane)):
                if node is not None and ev.id == node.id:
                    self.shell.config.panes[name] = {
                        "width": int(ev.data.get("width", 0) or 0),
                        "collapsed": bool(ev.data.get("collapsed", False)),
                    }
                    self.shell.config.save()
                    return True
            return False
        mine = {n.id for n in (self.tree, self.tabs) if n is not None}
        if ev.id not in mine:
            return False
        match ev.kind:
            case "select":
                self.open(str(ev.value or ""))
            case "close":
                self.close_channel(
                    str(ev.data.get("reason", "")).removeprefix("channel:")
                )
            case "change":
                pass
        return True

    def open(self, value: str) -> None:
        """Shows a creature (``creature:<id>``) or a channel (``channel:<name>``)."""
        kind, _, rest = value.partition(":")
        match kind:
            case "creature":
                self.show_creature(rest)
            case "channel":
                self.show_channel(rest)

    def show_creature(self, cid: str) -> None:
        self.shell.focus(cid)
        self.refresh()

    def leave_channel(self) -> bool:
        """Hides the shown channel (the caller shows a creature); False when none was."""
        if self.channel is None:
            return False
        self.channel.focused = False
        self.channel = None
        return True

    def show_channel(self, name: str) -> None:
        shell = self.shell
        tab = self.channels.get(name)
        if tab is None:
            tab = ChannelView(shell, name)
            self.channels[name] = tab
        if self.channel is not None:
            self.channel.focused = False
        else:
            # the creature keeps working unseen: its unread count and notices run
            shell.focused.view.set_focused(False)
        self.channel = tab
        tab.focused = True
        tab.unread = 0
        shell.app.show_document(tab.doc, f"── #{name} ──")
        self.refresh()
        shell.refresh()

    def close_channel(self, name: str) -> None:
        tab = self.channels.pop(name, None)
        if tab is None:
            return
        if tab is self.channel:
            # back to the focused creature first, so the removed document is never the shown one
            self.shell.focus("")
        tab.close()
        self.refresh()

    def action(self, name: str) -> None:
        """Keys: ``toggle_rail`` / ``toggle_aux`` collapse or open a pane (saved like a
        click), ``next_tab`` / ``prev_tab`` step through the conversation tabs, ``tab_<n>``
        opens the n-th."""
        match name:
            case "toggle_rail" | "toggle_aux":
                pane_name = "rail" if name == "toggle_rail" else "aux"
                node = self.rail if pane_name == "rail" else self.aux_pane
                if node is None:
                    return
                saved = self.shell.config.panes.setdefault(
                    pane_name, self._pane_config(pane_name)
                )
                saved["collapsed"] = not saved.get("collapsed", False)
                node.update(collapsed=saved["collapsed"])
                self.shell.config.save()
            case _ if name.startswith("tab_"):
                values = [t["value"] for t in self._tabs()]
                index = int(name.removeprefix("tab_")) - 1
                if 0 <= index < len(values):
                    self.open(values[index])
            case "next_tab" | "prev_tab":
                values = [t["value"] for t in self._tabs()]
                if not values:
                    return
                step = 1 if name == "next_tab" else -1
                self.open(values[(self._active() + step) % len(values)])

    def channel_changed(self, tab: ChannelView) -> None:
        self.refresh()

    async def submit(self, text: str) -> bool:
        """Sends composer text to the shown channel; False when a creature is shown."""
        if self.channel is None:
            return False
        await self.channel.send(text)
        return True

    def close(self) -> None:
        for tab in self.channels.values():
            tab.close()
        self.channels.clear()
        self.aux.close()
