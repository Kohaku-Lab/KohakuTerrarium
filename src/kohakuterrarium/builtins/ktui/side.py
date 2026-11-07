"""The right side panel of the Chat page, and :class:`Sections`, the titled lists both the
panel and the pages draw with.

The panel shows only what has something in it, one section under another: the open
channel's members, running tools (Jobs), sub-agents, drives, open tool widgets and the
scratchpad. Enter or a click on an entry opens its detail in the panel (``side_detail.py``: a
sub-agent's live trace, a job, a drive; a member opens its chat). With nothing to show the
panel is hidden, and comes back when something starts.
"""

from typing import TYPE_CHECKING, Any

from kohakutui.ui import KV, Bars, Field, Node, Stack, Table, Text, Tree

import kohakuterrarium.builtins.ktui.chrome_data as data
from kohakuterrarium.builtins.ktui.side_detail import SideDetail

if TYPE_CHECKING:
    from kohakuterrarium.builtins.ktui.shell import KtuiShell

EMPTY = "Nothing running."

# (key, title or None, kind: tree | kv | table | text | fields | spec | bars, payload); fields: a
# list of {"name", "label", "options", "value"} select controls; spec: (version, component),
# drawn again when the version changes (only as the last section: it is added at the end)
Section = tuple[str, str | None, str, Any]


def _spec(kind: str, payload: Any) -> Any:
    match kind:
        case "tree":
            return Tree(items=payload)
        case "kv":
            return KV(fields=payload)
        case "table":
            columns, rows = payload
            return Table(columns=columns, rows=rows)
        case "spec":
            return payload[1]
        case "bars":
            return Bars(**payload)
    return Text(str(payload), tone="faint")


def _update(node: Node, kind: str, payload: Any) -> None:
    match kind:
        case "tree":
            node.update(items=payload)
        case "kv":
            node.update(fields=payload)
        case "table":
            columns, rows = payload
            node.update(columns=columns, rows=rows)
        case "bars":
            node.update(**payload)
        case _:
            node.update(value=str(payload))


class Sections:
    """Titled bodies under ``parent``. While the same sections (keys and kinds) are shown,
    their nodes are updated in place, so a list keeps its cursor while its entries tick.
    """

    def __init__(self, parent: Node, spaced: bool = True):
        """``spaced``: a blank line above every title but the first (a document already
        puts one between its nodes)."""
        self.parent = parent
        self.spaced = spaced
        # [key, kind, title node or None, body node, last (title, payload)]
        self._shown: list[list[Any]] = []
        # field node id → its name, for the change events of "fields" sections
        self.fields: dict[int, str] = {}
        self._field_nodes: dict[str, Node] = {}

    def trees(self) -> set[int]:
        return {s[3].id for s in self._shown if s[1] == "tree"}

    def field_node(self, name: str) -> Node | None:
        return self._field_nodes.get(name)

    def show(self, sections: list[Section]) -> None:
        layout = [(key, kind, title is None) for key, title, kind, _ in sections]
        if layout != [(s[0], s[1], s[2] is None) for s in self._shown]:
            self._rebuild(sections)
            return
        for i, (shown, (_, title, kind, payload)) in enumerate(
            zip(self._shown, sections)
        ):
            if (title, payload) == shown[4]:
                continue
            if shown[2] is not None:
                shown[2].update(value=self._title(title or "", i))
            if kind == "fields":
                for f in payload:
                    node = self._field_nodes.get(f["name"])
                    if node is not None:
                        node.update(value=f["value"])
            elif kind == "spec":
                if payload[0] != shown[4][1][0]:
                    shown[3].remove()
                    shown[3] = self.parent.add(payload[1])
            else:
                _update(shown[3], kind, payload)
            shown[4] = (title, payload)

    def clear(self) -> None:
        self._rebuild([])

    def _title(self, title: str, index: int) -> str:
        return f"\n{title}" if self.spaced and index > 0 else title

    def _rebuild(self, sections: list[Section]) -> None:
        for shown in self._shown:
            if shown[2] is not None:
                shown[2].remove()
            shown[3].remove()
        self._shown = []
        self.fields.clear()
        self._field_nodes.clear()
        for i, (key, title, kind, payload) in enumerate(sections):
            header = None
            if title is not None:
                header = self.parent.add(Text(self._title(title, i), bold=True))
            if kind == "fields":
                body = self.parent.add(Stack())
                for f in payload:
                    spec = Field(
                        control="select",
                        label=f["label"],
                        options=f["options"],
                        value=f["value"],
                    )
                    node = body.add(spec)
                    self.fields[node.id] = f["name"]
                    self._field_nodes[f["name"]] = node
            else:
                body = self.parent.add(_spec(kind, payload))
            self._shown.append([key, kind, header, body, (title, payload)])


class Side:
    """The Chat page's right panel."""

    def __init__(self, shell: "KtuiShell", drives: data.Drives):
        self.shell = shell
        self.drives = drives
        self.sections: Sections | None = None
        self.pane: Node | None = None
        self.detail: SideDetail | None = None
        # nothing to show: the pane is hidden (the chrome asks before showing it again);
        # None until the first refresh decides
        self.empty: bool | None = None

    def build(self, pane: Node) -> None:
        self.pane = pane
        self.sections = Sections(pane)
        self.refresh()

    def open(self, kind: str, *ids: str) -> None:
        """Shows a detail (``trace`` / ``job`` with creature and job id, ``drive`` with its id)."""
        self.close_detail()
        self.detail = SideDetail(self.shell, kind, *ids)
        self.refresh()

    def close_detail(self) -> None:
        if self.detail is not None:
            self.detail.close()
            self.detail = None

    def act(self, what: str) -> None:
        """``side:<what>``: back to the lists, or the open detail's action."""
        if what == "back":
            self.close_detail()
        elif self.detail is not None:
            self.detail.act(what)
        self.refresh()

    def lists(self) -> set[int]:
        """Ids of the section lists (their select events are the panel's)."""
        return self.sections.trees() if self.sections else set()

    def _content(self) -> list[Section]:
        shell = self.shell
        out: list[Section] = []
        channel = shell.chrome.channel
        if channel is not None:
            who = data.members(shell, channel.name)
            title = f"#{channel.name} · {len(who)} members"
            if who:
                out.append(("members", title, "tree", who))
            else:
                out.append(
                    ("members", title, "text", "nobody listens or sends here yet")
                )
        jobs = data.jobs(shell)
        if jobs:
            out.append(("jobs", f"Jobs · {len(jobs)}", "tree", jobs))
        agents = data.subagents(shell)
        if agents:
            running = data.running_subagents(shell)
            count = f"{running} running" if running else str(len(agents))
            out.append(("agents", f"Sub-agents · {count}", "tree", agents))
        self.drives.poll(self.refresh)
        drives = self.drives.items()
        if drives:
            drives = [
                *drives,
                {"label": "+ new goal", "value": "cfg:goal", "tone": "info"},
            ]
            out.append(("drives", f"Drives · {len(drives) - 1}", "tree", drives))
        art = self._canvas()
        if art:
            out.append(("canvas", f"Canvas · {len(art)}", "tree", art))
        widgets = data.widgets(shell)
        if widgets:
            out.append(("widgets", f"Widgets · {len(widgets)}", "tree", widgets))
        pad = data.scratchpad(shell)
        if pad:
            out.append(("pad", f"Scratchpad · {len(pad)}", "kv", pad))
        return out

    def _canvas(self) -> list[dict]:
        """The open conversation's pictures, videos and sounds, newest first."""
        entry = self.shell.focused
        icons = {"image": "🖼", "video": "▶", "audio": "♪"}
        art = entry.view.artifacts
        return [
            {
                "label": name or url.rsplit("/", 1)[-1][:40] or kind,
                "icon": icons.get(kind, "·"),
                "value": f"art:{entry.creature.creature_id}:{i}",
            }
            for i, (kind, url, name) in reversed(list(enumerate(art)))
        ][:12]

    def refresh(self) -> None:
        if self.sections is None:
            return
        try:
            content = self.detail.sections() if self.detail else self._content()
        except Exception:  # the panel must never take the UI down
            content = []
        empty = not content
        self.sections.show(content or [("empty", None, "text", EMPTY)])
        if empty != self.empty:
            self.empty = empty
            if self.pane is not None and self.shell.chrome.page == "chat":
                self.pane.update(hidden=empty)
