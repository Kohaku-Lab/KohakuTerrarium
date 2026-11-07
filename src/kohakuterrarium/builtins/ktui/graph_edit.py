"""Graph edits, as the web's Add dialog and graph editor: add a creature (an installed one or a
config path) or a channel to the focused graph, connect or disconnect a channel between two
creatures, remove a creature. Reached from the Graph page and the rail's ``+`` entries; each
step is a docked picker or prompt.
"""

from typing import TYPE_CHECKING

from kohakutui.ui import Confirm, Picker, Prompt

from kohakuterrarium.builtins.ktui.format import one_line
from kohakuterrarium.studio.catalog.packages.scan import scan_catalog
from kohakuterrarium.utils.logging import get_logger

if TYPE_CHECKING:
    from kohakuterrarium.builtins.ktui.shell import KtuiShell

logger = get_logger(__name__)

GRAPH_ACTIONS = [
    {"label": "+ add a creature", "value": "graph:add_creature", "tone": "info"},
    {"label": "+ add a channel", "value": "graph:add_channel", "tone": "info"},
    {"label": "⇄ connect two creatures", "value": "graph:connect"},
    {"label": "✂ disconnect", "value": "graph:disconnect"},
    {"label": "✗ remove a creature", "value": "graph:remove"},
]


class GraphEdit:
    def __init__(self, shell: "KtuiShell"):
        self.shell = shell

    def _graph_id(self) -> str:
        return self.shell.focused.creature.graph_id

    async def act(self, what: str) -> None:
        try:
            match what:
                case "add_creature":
                    await self.add_creature()
                case "add_channel":
                    await self.add_channel()
                case "connect":
                    await self.connect(True)
                case "disconnect":
                    await self.connect(False)
                case "remove":
                    await self.remove()
        except Exception as exc:
            logger.warning("graph edit failed", action=what, error=str(exc))
            self.shell.app.notify(
                f"{what.replace('_', ' ')}: {one_line(str(exc), 90)}", "error"
            )
        self.shell.sync_creatures()

    async def _pick_creature(self, title: str, exclude: str = "") -> str | None:
        items = [
            {"label": e.view.name, "value": cid}
            for cid, e in self.shell.entries.items()
            if cid != exclude
        ]
        if not items:
            return None
        answer = await self.shell.panel(Picker(items=items, title=title))
        return str(answer) if answer else None

    async def add_creature(self) -> None:
        shell = self.shell
        try:
            entries = [e for e in scan_catalog() if e.type == "creature"]
        except Exception:
            entries = []
        items = [
            {
                "label": e.name,
                "detail": one_line(e.description or str(e.path), 60),
                "value": str(e.path),
                "group": e.source or "installed",
            }
            for e in entries
        ]
        items.append({"label": "a config path…", "value": "path:", "group": "other"})
        picked = await shell.panel(
            Picker(
                items=items,
                title="Add a creature",
                placeholder="type to filter installed creatures",
                sidebar=True,
                max_visible=10,
            )
        )
        if not picked:
            return
        config = str(picked)
        if config == "path:":
            config = str(
                await shell.panel(
                    Prompt(question="Creature config path", placeholder="./creatures/x")
                )
                or ""
            ).strip()
            if not config:
                return
        name = await shell.panel(
            Prompt(question="Name (empty: the config's)", placeholder="")
        )
        creature = await shell.engine.add_creature(
            config,
            graph=self._graph_id(),
            io="none",
            name=str(name).strip() or None if name else None,
        )
        shell.app.notify(f"added {creature.name}", "success")

    async def add_channel(self) -> None:
        shell = self.shell
        name = await shell.panel(Prompt(question="Channel name", placeholder="tasks"))
        name = str(name or "").strip().lstrip("#")
        if not name:
            return
        about = await shell.panel(Prompt(question=f"What #{name} is for (optional)"))
        await shell.engine.add_channel(self._graph_id(), name, str(about or "").strip())
        shell.app.notify(
            f"#{name} added · connect creatures to it from the Graph page", "success"
        )

    async def connect(self, on: bool) -> None:
        shell = self.shell
        verb = "Connect" if on else "Disconnect"
        sender = await self._pick_creature(f"{verb}: who sends")
        if not sender:
            return
        receiver = await self._pick_creature(f"{verb}: who receives", exclude=sender)
        if not receiver:
            return
        channel = await shell.panel(
            Prompt(
                question="Channel (empty: a new one, or every link when disconnecting)"
            )
        )
        channel = str(channel or "").strip().lstrip("#") or None
        if on:
            await shell.engine.connect(sender, receiver, channel=channel)
        else:
            await shell.engine.disconnect(sender, receiver, channel=channel)
        shell.app.notify(f"{verb.lower()}ed", "success")

    async def remove(self) -> None:
        shell = self.shell
        if len(shell.entries) < 2:
            shell.app.notify("the last creature stays (use /exit to leave)", "info")
            return
        cid = await self._pick_creature("Remove which creature")
        if not cid:
            return
        name = shell.entries[cid].view.name if cid in shell.entries else cid
        if not await shell.panel(
            Confirm(question=f"Remove {name}? Its session stays saved.")
        ):
            return
        await shell.engine.remove_creature(cid)
        shell.app.notify(f"removed {name}", "success")
