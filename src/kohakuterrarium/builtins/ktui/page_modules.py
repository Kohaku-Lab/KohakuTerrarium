"""The Settings page's Modules section, as the web's: every configurable module of the target
creature (plugins, native tools, tools). Enter on one offers what it supports: switching a
plugin on or off, editing its options in a form (from the module's option schema, as the web's
module settings). Works on a local engine and on a session in a server alike.
"""

import json
from typing import TYPE_CHECKING, Any

from kohakutui.ui import Picker

from kohakuterrarium.builtins.ktui.format import one_line
from kohakuterrarium.terrarium.service.service import LocalTerrariumService
from kohakuterrarium.utils.logging import get_logger

if TYPE_CHECKING:
    from kohakuterrarium.builtins.ktui.shell import Entry, KtuiShell

logger = get_logger(__name__)

TYPE_LABEL = {"plugin": "plugins", "native_tool": "native tools", "tool": "tools"}


def module_service(shell: "KtuiShell") -> Any:
    """The four module calls (list, options, set options, toggle) for the shell's engine."""
    remote = getattr(shell.engine, "remote_service", None)
    return remote if remote is not None else LocalTerrariumService(shell.engine)


def _field(key: str, spec: dict, value: Any) -> dict:
    kind = str(spec.get("type", "string"))
    hint = one_line(str(spec.get("doc") or spec.get("description") or ""), 70)
    if kind == "enum":
        options = ["", *[str(v) for v in spec.get("values") or []]]
        return {
            "name": key,
            "label": key,
            "control": "select",
            "options": options,
            "value": "" if value is None else str(value),
            "hint": hint,
        }
    if kind == "bool":
        shown = "" if value is None else ("on" if value else "off")
        return {
            "name": key,
            "label": key,
            "control": "select",
            "options": ["", "on", "off"],
            "value": shown,
            "hint": hint,
        }
    if kind in ("list", "dict"):
        shown = "" if value is None else json.dumps(value, ensure_ascii=False)
        return {"name": key, "label": f"{key} (json)", "value": shown, "hint": hint}
    return {
        "name": key,
        "label": key,
        "value": "" if value is None else str(value),
        "hint": hint,
    }


def _parse(spec: dict, text: str) -> Any:
    """A form answer as the option's type; empty is None (the default)."""
    kind = str(spec.get("type", "string"))
    text = text.strip()
    if not text:
        return None
    match kind:
        case "int":
            return int(text)
        case "float":
            return float(text)
        case "bool":
            return text == "on"
        case "list" | "dict":
            return json.loads(text)
    return text


class Modules:
    def __init__(self, shell: "KtuiShell"):
        self.shell = shell
        self.loaded: dict[str, list[dict]] = {}
        self._loading: set[str] = set()

    def items(self, entry: "Entry") -> list[dict]:
        """The section's entries (loaded in the background the first time)."""
        cid = entry.creature.creature_id
        modules = self.loaded.get(cid)
        if modules is None:
            if cid not in self._loading:
                self._loading.add(cid)
                self.shell.spawn(self.load(cid))
            return [{"label": "loading…", "value": "set:none"}]
        groups: dict[str, list[dict]] = {}
        for m in modules:
            enabled = m.get("enabled")
            icon = "✓" if enabled else ("○" if enabled is False else "·")
            groups.setdefault(m["type"], []).append(
                {
                    "label": m["name"],
                    "icon": icon,
                    "tone": "success" if enabled else None,
                    "detail": one_line(m.get("description") or "", 50) or None,
                    "value": f"set:module:{m['type']}:{m['name']}",
                }
            )
        return [
            {
                "label": f"{TYPE_LABEL.get(kind, kind)} · {len(rows)}",
                "value": f"set:none:{kind}",
                "open": kind == "plugin",
                "children": rows,
            }
            for kind, rows in groups.items()
        ]

    async def load(self, cid: str) -> None:
        try:
            self.loaded[cid] = list(await module_service(self.shell).list_modules(cid))
        except Exception as exc:
            logger.debug("modules not listed", error=str(exc))
            self.loaded[cid] = []
        finally:
            self._loading.discard(cid)
        pages = self.shell.chrome.pages
        if pages.shown == "settings":
            pages.refresh(force=True)

    async def act(self, entry: "Entry", what: str) -> None:
        """``<type>:<name>``: the module's menu, then the chosen action."""
        shell = self.shell
        kind, _, name = what.partition(":")
        cid = entry.creature.creature_id
        module = next(
            (
                m
                for m in self.loaded.get(cid, [])
                if m["type"] == kind and m["name"] == name
            ),
            None,
        )
        if module is None:
            return
        actions = []
        if kind == "plugin":
            verb = "switch off" if module.get("enabled") else "switch on"
            actions.append({"label": verb, "value": "toggle"})
        if module.get("schema"):
            actions.append({"label": "edit options…", "value": "options"})
        if not actions:
            shell.app.notify(f"{name} has nothing to set", "info")
            return
        choice = await shell.panel(Picker(items=actions, title=f"{kind} · {name}"))
        service = module_service(shell)
        try:
            if choice == "toggle":
                result = await service.toggle_module(cid, kind, name)
                state = "on" if (result or {}).get("enabled") else "off"
                shell.app.notify(f"{name}: {state}", "success")
            elif choice == "options":
                await self._options(service, cid, kind, name)
        except Exception as exc:
            shell.app.notify(f"{name}: {one_line(str(exc), 90)}", "error")
        await self.load(cid)

    async def _options(self, service: Any, cid: str, kind: str, name: str) -> None:
        shell = self.shell
        current = await service.get_module_options(cid, kind, name)
        schema = dict(current.get("schema") or {})
        values = dict(current.get("options") or {})
        fields = [_field(key, spec, values.get(key)) for key, spec in schema.items()]
        answers = await shell.forms.ask(f"Options · {name}", fields)
        if answers is None:
            return
        # only what was changed is sent: an untouched field keeps its configured or
        # default value; a plugin option emptied is left alone (None would replace it)
        parsed: dict[str, Any] = {}
        try:
            for f in fields:
                key = f["name"]
                answer = str(answers.get(key, ""))
                if answer == f["value"]:
                    continue
                value = _parse(schema[key], answer)
                if value is None and kind == "plugin":
                    continue
                parsed[key] = value
        except (ValueError, json.JSONDecodeError) as exc:
            shell.app.notify(f"not saved: {exc}", "error")
            return
        if not parsed:
            shell.app.notify(f"{name}: nothing changed", "info")
            return
        await service.set_module_options(cid, kind, name, parsed)
        shell.app.notify(f"{name}: options saved", "success")
