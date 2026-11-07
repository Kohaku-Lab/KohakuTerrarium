"""The Settings page's session sections, as the web's Settings tab: Model (each creature's,
enter switches), Modules (the target creature's plugins and tools: switch on / off, edit options), Workspace (its
folder, enter changes it), Env and Triggers (read only), Cost (tokens and an estimate per
creature) and Extensions (installed packages). Per-creature sections follow a target picked
at the top when the session has several creatures (the focused one at first).
"""

import os
from typing import TYPE_CHECKING, Any

from kohakutui.ui import Prompt

from kohakuterrarium.builtins.ktui.attach import working_dir
from kohakuterrarium.builtins.ktui.format import one_line, tokens
from kohakuterrarium.builtins.ktui.page_modules import Modules
from kohakuterrarium.studio.catalog.packages.scan import scan_catalog
from kohakuterrarium.terrarium.creature.ops import agent_env
from kohakuterrarium.utils.logging import get_logger

if TYPE_CHECKING:
    from kohakuterrarium.builtins.ktui.shell import Entry, KtuiShell

logger = get_logger(__name__)

# USD per 1M tokens (input / cached input / output), by model-name prefix (the web's table)
MODEL_PRICES = {
    "gpt-4o-mini": (0.15, 0.075, 0.6),
    "gpt-4o": (2.5, 1.25, 10),
    "claude-opus-4-6": (5, 0.5, 25),
    "claude-sonnet-4-6": (3, 0.3, 15),
    "claude-haiku-4-5": (1, 0.1, 5),
    "o1-mini": (3, 1.5, 12),
    "o1": (15, 7.5, 60),
}
# names that commonly carry secrets are not shown (as the web's Env section)
SECRET_PARTS = (
    "secret",
    "key",
    "token",
    "password",
    "pass",
    "private",
    "auth",
    "credential",
)
ENV_SHOWN = 60


def price_for(model: str) -> tuple[float, float, float] | None:
    name = model.split("@", 1)[0].rpartition("/")[2].lower().replace(".", "-")
    best = max((p for p in MODEL_PRICES if name.startswith(p)), key=len, default=None)
    return MODEL_PRICES[best] if best else None


def estimate(model: str, prompt: int, cached: int, completion: int) -> float | None:
    rate = price_for(model)
    if rate is None:
        return None
    cached = min(cached, prompt)
    return ((prompt - cached) * rate[0] + cached * rate[1] + completion * rate[2]) / 1e6


def env_fields(agent: Any) -> list[dict]:
    """The creature's environment, secrets hidden (a session in a server: the server's)."""
    remote = getattr(agent, "remote_env", None)
    env = remote if isinstance(remote, dict) else agent_env(agent).get("env", {})
    rows = [
        (k, v)
        for k, v in sorted(env.items())
        if not any(part in k.lower() for part in SECRET_PARTS)
    ]
    return [{"label": k, "value": one_line(str(v), 80)} for k, v in rows[:ENV_SHOWN]]


class SessionSettings:
    """Builds the session sections and acts on their entries (``set:<what>[:<arg>]``)."""

    def __init__(self, shell: "KtuiShell"):
        self.shell = shell
        self.target = ""
        self._packages: list[Any] | None = None
        self.modules = Modules(shell)

    def entry(self) -> "Entry":
        shell = self.shell
        return shell.entries.get(self.target) or shell.focused

    def sections(self) -> list[tuple[str, str | None, str, Any]]:
        shell = self.shell
        entry = self.entry()
        out: list = []
        if shell.multi:
            names = [e.view.name for e in shell.entries.values()]
            target = {
                "name": "target",
                "label": "Settings for",
                "options": names,
                "value": entry.view.name,
            }
            out.append(("target", None, "fields", [target]))
        models = [
            {
                "label": e.view.name,
                "detail": e.view.usage.model or "?",
                "value": f"set:model:{cid}",
            }
            for cid, e in shell.entries.items()
        ]
        out.append(("s-model", "Model · enter switches", "tree", models))
        modules = self.modules.items(entry)
        out.append(("s-modules", f"Modules · {entry.view.name}", "tree", modules))
        folder = [
            {
                "label": str(working_dir(entry.agent)),
                "detail": "enter changes it",
                "value": "set:cwd",
            }
        ]
        out.append(("s-workspace", "Workspace", "tree", folder))
        triggers = self._triggers(entry)
        if triggers:
            out.append(("s-triggers", "Triggers", "tree", triggers))
        out.append(("s-cost", "Cost · estimated", "kv", self._cost()))
        packages = self._extensions()
        if packages:
            out.append(("s-ext", "Extensions", "tree", packages))
        out.append(
            (
                "s-env",
                f"Env · first {ENV_SHOWN}, secrets hidden",
                "kv",
                env_fields(entry.agent),
            )
        )
        return out

    def _triggers(self, entry: "Entry") -> list[dict]:
        manager = getattr(entry.agent, "trigger_manager", None)
        try:
            infos = manager.list() if manager is not None else []
        except Exception:
            return []
        return [
            {
                "label": info.trigger_type,
                "icon": "●" if info.running else "○",
                "detail": f"{info.trigger_id} · {info.created_at:%m-%d %H:%M}",
                "value": "set:none",
            }
            for info in infos
        ]

    def _cost(self) -> list[dict]:
        rows = []
        total = 0.0
        priced = False
        for entry in self.shell.entries.values():
            u = entry.view.usage
            if not (u.tokens_in or u.tokens_out):
                continue
            cost = estimate(u.model, u.tokens_in, u.cached, u.tokens_out)
            if cost is not None:
                total, priced = total + cost, True
            money = f"${cost:.4f}" if cost is not None else "no price"
            rows.append(
                {
                    "label": entry.view.name,
                    "value": f"{money} · in {tokens(u.tokens_in)} (cache "
                    f"{tokens(u.cached)}) · out {tokens(u.tokens_out)} · {u.model or '?'}",
                }
            )
        head = {"label": "session", "value": f"${total:.4f}" if priced else "—"}
        return (
            [head, *rows] if rows else [{"label": "session", "value": "no tokens yet"}]
        )

    def _extensions(self) -> list[dict]:
        if self._packages is None:
            try:
                self._packages = list(scan_catalog())
            except Exception as exc:
                logger.debug("packages not listed", error=str(exc))
                self._packages = []
        groups: dict[str, list[dict]] = {}
        for p in self._packages:
            groups.setdefault(str(p.type), []).append(
                {
                    "label": p.name,
                    "detail": one_line(p.description or "", 50) or None,
                    "value": "set:none",
                }
            )
        return [
            {
                "label": f"{kind} · {len(items)}",
                "value": f"set:none:{kind}",
                "children": items,
            }
            for kind, items in sorted(groups.items())
        ]

    # -- actions --------------------------------------------------------

    def choose_target(self, name: str) -> None:
        for cid, entry in self.shell.entries.items():
            if entry.view.name == name:
                self.target = cid

    async def act(self, what: str) -> None:
        shell = self.shell
        kind, _, arg = what.partition(":")
        entry = self.entry()
        match kind:
            case "model":
                target = shell.entries.get(arg)
                if target is not None:
                    await shell.settings.pick_model(target)
            case "module":
                await self.modules.act(entry, arg)
            case "cwd":
                answer = await shell.panel(
                    Prompt(
                        question=f"Working folder for {entry.view.name}",
                        placeholder=str(working_dir(entry.agent)),
                    )
                )
                if answer and str(answer).strip():
                    await self._set_cwd(entry, str(answer).strip())

    async def _set_cwd(self, entry: "Entry", path: str) -> None:
        shell = self.shell
        if shell.is_busy(entry):
            shell.app.notify(
                "wait until the creature is idle to switch folders", "warning"
            )
            return
        ws = getattr(entry.agent, "workspace", None)
        if ws is None:
            shell.app.notify("this creature has no workspace to switch", "warning")
            return
        try:
            applied = ws.set(os.path.expanduser(path))
        except Exception as exc:
            shell.app.notify(f"folder not changed: {exc}", "error")
            return
        shell.app.notify(f"working folder: {applied}", "success")
