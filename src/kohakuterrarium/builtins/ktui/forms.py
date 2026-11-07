"""Forms: several fields filled at once (add or edit a provider, a model, an MCP server, a
goal). Full screen draws a form as a page where the chat is: the fields one under another
(tab or ↓ moves on, ←/→ changes a choice), then Save / Cancel; esc leaves a field and esc
again cancels. Inline asks the
fields one at a time at the input (enter keeps the shown value, esc cancels).

A field is ``{"name", "label", "value", "control": "text" | "select", "options", "hint"}``.
"""

import asyncio
from typing import TYPE_CHECKING, Any

from kohakutui import Event
from kohakutui.ui import Button, Field, Node, Picker, Prompt, Text

if TYPE_CHECKING:
    from kohakuterrarium.builtins.ktui.shell import KtuiShell


class Forms:
    def __init__(self, shell: "KtuiShell"):
        self.shell = shell
        self._open: dict[str, Any] | None = None

    async def ask(self, title: str, fields: list[dict]) -> dict | None:
        """The filled values (name → text), or None when cancelled."""
        if self.shell.mode != "fullscreen":
            return await self._inline(title, fields)
        return await self._page(title, fields)

    # -- inline: one field at a time -------------------------------------

    async def _inline(self, title: str, fields: list[dict]) -> dict | None:
        values: dict[str, str] = {}
        for f in fields:
            label = f"{title} · {f['label']}"
            current = str(f.get("value", "") or "")
            if f.get("control") == "select":
                items = [
                    {"label": o, "value": o, "current": o == current}
                    for o in f.get("options") or []
                ]
                answer = await self.shell.panel(Picker(items=items, title=label))
            else:
                hint = f.get("hint") or ""
                answer = await self.shell.panel(
                    Prompt(
                        question=f"{label}" + (f" · {hint}" if hint else ""),
                        placeholder=current or None,
                    )
                )
                if answer is not None and not str(answer).strip():
                    answer = current
            if answer is None:
                return None
            values[f["name"]] = str(answer)
        return values

    # -- full screen: a page --------------------------------------------

    async def _page(self, title: str, fields: list[dict]) -> dict | None:
        shell = self.shell
        app = shell.app
        doc = app.add_document()
        doc.add(Text(title, bold=True))
        nodes: dict[int, str] = {}
        values = {f["name"]: str(f.get("value", "") or "") for f in fields}
        first: Node | None = None
        for f in fields:
            control = "select" if f.get("control") == "select" else "text"
            spec = Field(
                control=control,
                label=f["label"],
                value=values[f["name"]],
                options=f.get("options") if control == "select" else None,
                placeholder=f.get("hint") if control == "text" else None,
            )
            node = doc.add(spec)
            nodes[node.id] = f["name"]
            first = first or node
        buttons = doc.add(
            Button(
                segments=[
                    {"label": "Save", "value": "save"},
                    {"label": "Cancel", "value": "cancel"},
                ],
                focused_hint="  ←→ choose · enter",
            )
        )
        hint = "tab / ↓ next field · ←/→ change a choice · esc twice cancels"
        doc.add(Text(hint, tone="faint"))
        done: asyncio.Future = asyncio.get_running_loop().create_future()
        self._open = {
            "nodes": nodes,
            "values": values,
            "buttons": buttons.id,
            "done": done,
        }
        app.show_document(doc, top=True)
        if first is not None:
            first.focus()
        try:
            return await done
        finally:
            self._open = None
            shell.chrome.restore_document()
            doc.remove()
            if shell.composer is not None:
                shell.composer.focus()

    def handle(self, ev: Event) -> bool:
        """The open form's events; True when taken."""
        form = self._open
        if form is None:
            return False
        done: asyncio.Future = form["done"]
        if ev.id in form["nodes"]:
            if ev.kind == "change" and ev.value is not None:
                form["values"][form["nodes"][ev.id]] = str(ev.value)
            return True
        if ev.id == form["buttons"] and ev.kind == "select":
            if not done.done():
                done.set_result(dict(form["values"]) if ev.value == "save" else None)
            return True
        if ev.kind == "action" and ev.data.get("name") == "escape":
            if not done.done():
                done.set_result(None)
            return True
        return False
