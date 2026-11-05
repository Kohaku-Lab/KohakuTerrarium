"""The right side panel of the Chat page, and :class:`Sections`, the titled lists both the
panel and the pages draw with.

The panel shows only what has something in it, one section under another: the open
channel's members, running tools (Jobs), sub-agents, drives, open tool widgets and the
scratchpad. Enter or a click on an entry opens it (a job's Stop / Background card, a
sub-agent's trace, a member's chat). Nothing going on: one quiet line.
"""

from typing import TYPE_CHECKING, Any

from kohakutui.ui import KV, Field, Node, Stack, Table, Text, Tree

import kohakuterrarium.builtins.ktui.chrome_data as data

if TYPE_CHECKING:
    from kohakuterrarium.builtins.ktui.shell import KtuiShell

EMPTY = "Nothing running."

# (key, title or None, kind: tree | kv | table | text | fields, payload); fields: a list of
# {"name", "label", "options", "value"} select controls
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

    def build(self, pane: Node) -> None:
        self.sections = Sections(pane)
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
            out.append(("drives", f"Drives · {len(drives)}", "tree", drives))
        widgets = data.widgets(shell)
        if widgets:
            out.append(("widgets", f"Widgets · {len(widgets)}", "tree", widgets))
        pad = data.scratchpad(shell)
        if pad:
            out.append(("pad", f"Scratchpad · {len(pad)}", "kv", pad))
        return out or [("empty", None, "text", EMPTY)]

    def refresh(self) -> None:
        if self.sections is None:
            return
        try:
            content = self._content()
        except Exception:  # the panel must never take the UI down
            content = [("empty", None, "text", EMPTY)]
        self.sections.show(content)
