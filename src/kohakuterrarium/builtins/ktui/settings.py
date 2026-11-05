"""``/config`` (``/settings``): ktui's and KT's settings in one categorised picker.

The same picker as ``/model``, docked at the input with a section column:

    display    layout, colours, tool-call detail: ←/→ changes, enter applies (remembered)
    models     the session's model (opens /model) and the default for new sessions
    keys       API keys per provider: enter asks for the key (typed hidden; empty removes)
    providers  backends, read only here
    mcp        MCP servers, read only here

Keys and the default model persist through the same functions as ``kt config`` and the web
settings page. Adding or editing providers and MCP servers is not in this picker yet; those rows
say where to do it.
"""

from typing import TYPE_CHECKING, Any

from kohakutui import THEMES
from kohakutui.ui import Picker, Prompt

from kohakuterrarium.builtins.ktui.config import LINKS, RENDERERS, VERBOSITY
from kohakuterrarium.builtins.ktui.models import model_id, model_items, selector_of
from kohakuterrarium.llm.profiles.api_keys import (
    PROVIDER_KEY_MAP,
    get_api_key,
    save_api_key,
)
from kohakuterrarium.llm.profiles.resolve import get_default_model, load_backends
from kohakuterrarium.llm.profiles.resolve import list_all as list_all_presets
from kohakuterrarium.llm.profiles.resolve import set_default_model
from kohakuterrarium.studio.identity.mcp.servers import load_servers
from kohakuterrarium.utils.logging import get_logger

if TYPE_CHECKING:
    from kohakuterrarium.builtins.ktui.shell import KtuiShell

logger = get_logger(__name__)

ELSEWHERE = "edit with `kt config` or the web settings page"


def mask(key: str) -> str:
    if not key:
        return "(not set)"
    return f"{key[:4]}…{key[-4:]}" if len(key) > 8 else "•" * len(key)


class Settings:
    """Builds ``/config``'s items and applies the chosen one."""

    def __init__(self, shell: "KtuiShell"):
        self.shell = shell

    def items(self) -> list[dict]:
        config = self.shell.config
        items: list[dict] = [
            _choice(
                "Layout",
                "renderer",
                RENDERERS,
                config.renderer,
                "inline · hybrid (inline, panels full screen) · fullscreen",
            ),
            _choice("Colours", "theme", list(THEMES), config.theme, ""),
            _choice(
                "Tool calls",
                "verbosity",
                VERBOSITY,
                config.verbosity,
                "high lists every call open, without batching (new sessions)",
            ),
            _choice(
                "Links",
                "links",
                LINKS,
                config.links,
                "osc8 clickable · text writes name (url) · applies on next start",
            ),
        ]
        items += self._model_items()
        items += _safe(self._key_items, "keys")
        items += _safe(self._provider_items, "providers")
        items += _safe(self._mcp_items, "mcp")
        return items

    def display_fields(self) -> list[dict]:
        """The display choices as select fields (the Settings page)."""
        return [
            {
                "name": item["value"].removeprefix("set:"),
                "label": item["label"],
                "options": item["choices"][0][1],
                "value": next(iter(item["chosen"].values())),
            }
            for item in self.items()
            if item.get("group") == "display"
        ]

    def sections(self) -> list[tuple[str, list[dict]]]:
        """The rest of the settings by section, as list entries (the Settings page)."""
        out: list[tuple[str, list[dict]]] = []
        for title, group in (
            ("Models", "models"),
            ("API keys", "keys"),
            ("Providers", "providers"),
            ("MCP servers", "mcp"),
        ):
            rows = [
                {"label": i["label"], "detail": i.get("detail"), "value": i["value"]}
                for i in self.items()
                if i.get("group") == group
            ]
            if rows:
                out.append((title, rows))
        return out

    def _model_items(self) -> list[dict]:
        entry = self.shell.focused
        try:
            default = get_default_model()
        except Exception:
            default = ""
        return [
            {
                "label": "Session model",
                "detail": entry.view.usage.model or "?",
                "value": "model:session",
                "group": "models",
            },
            {
                "label": "Default model",
                "detail": f"{default or '(none)'} · for new sessions",
                "value": "model:default",
                "group": "models",
            },
        ]

    def _key_items(self) -> list[dict]:
        items = []
        backends = load_backends()
        names = sorted(set(backends) | set(PROVIDER_KEY_MAP))
        for name in names:
            backend = backends.get(name)
            if backend is not None and backend.backend_type == "codex":
                detail, value = "OAuth · use /login codex", "info:oauth"
            else:
                env = (backend.api_key_env if backend else "") or PROVIDER_KEY_MAP.get(
                    name, ""
                )
                detail = mask(get_api_key(name)) + (f" · {env}" if env else "")
                value = f"key:{name}"
            items.append(
                {"label": name, "detail": detail, "value": value, "group": "keys"}
            )
        return items

    def _provider_items(self) -> list[dict]:
        return [
            {
                "label": name,
                "detail": " · ".join(
                    p for p in (b.backend_type, b.base_url or "") if p
                ),
                "value": "info:providers",
                "group": "providers",
            }
            for name, b in sorted(load_backends().items())
        ]

    def _mcp_items(self) -> list[dict]:
        rows = [
            {
                "label": s.get("name", "?"),
                "detail": " · ".join(
                    str(p)
                    for p in (
                        s.get("transport", "stdio"),
                        s.get("command") or s.get("url"),
                    )
                    if p
                ),
                "value": "info:mcp",
                "group": "mcp",
            }
            for s in load_servers()
        ]
        return rows or [
            {
                "label": "no MCP servers",
                "detail": ELSEWHERE,
                "value": "info:mcp",
                "group": "mcp",
            }
        ]

    async def open(self) -> None:
        shell = self.shell
        spec = Picker(
            items=self.items(),
            title="Settings · ←→ change · enter apply",
            placeholder="type to filter settings",
            sidebar=True,
            max_visible=10,
        )
        answer = await shell.panel(spec)
        if not answer:
            return
        value = answer.get("value") if isinstance(answer, dict) else answer
        choices = answer.get("choices", {}) if isinstance(answer, dict) else {}
        kind, _, rest = str(value).partition(":")
        if kind == "set":
            await self.apply(rest, str(choices.get(rest, "")))
        else:
            await self.act(str(value))

    async def act(self, value: str) -> None:
        """Acts on a settings entry that is not a choice (the picker and the Settings page)."""
        shell = self.shell
        kind, _, rest = value.partition(":")
        match kind:
            case "model" if rest == "session":
                await self.pick_model(shell.focused)
            case "model":
                await self._default_model()
            case "key":
                await self._set_key(rest)
            case "info" if rest == "oauth":
                shell.app.notify("codex signs in with /login codex", "info")
            case "info":
                shell.app.notify(f"{rest}: {ELSEWHERE}", "info")

    async def apply(self, name: str, option: str) -> None:
        """Sets a display choice (``renderer``, ``theme``, ``verbosity``, ``links``)."""
        shell = self.shell
        match name:
            case "renderer":
                self.set_renderer(option)
            case "theme":
                await self.choose_theme(option)
            case "verbosity" if option in VERBOSITY:
                shell.config.verbosity = option
                shell.config.save()
                shell.app.notify(
                    f"tool calls: {option} · applies to new sessions", "success"
                )
            case "links" if option in LINKS:
                shell.config.links = option
                shell.config.save()
                shell.app.notify(f"links: {option} · applies on next start", "success")

    async def _default_model(self) -> None:
        shell = self.shell
        try:
            presets = list_all_presets()
            current = get_default_model()
        except Exception as exc:
            shell.app.notify(f"could not list models: {exc}", "error")
            return
        items = model_items(presets, current)
        for item in items:
            item["choices"], item["chosen"] = [], {}
        spec = Picker(
            items=items,
            title=f"Default model · now {current or '(none)'}",
            placeholder="type to filter by provider or model",
            sidebar=True,
            max_visible=10,
        )
        selector = selector_of(await shell.panel(spec))
        if not selector:
            return
        try:
            set_default_model(selector)
        except Exception as exc:
            shell.app.notify(f"default model not saved: {exc}", "error")
            return
        shell.app.notify(f"default model: {selector}", "success")

    async def _set_key(self, provider: str) -> None:
        shell = self.shell
        spec = Prompt(
            question=f"API key for {provider} · empty removes it",
            placeholder="paste the key",
            secret=True,
        )
        key = await shell.panel(spec)
        if key is None:
            return
        try:
            save_api_key(provider, key.strip())
        except Exception as exc:
            shell.app.notify(f"key not saved: {exc}", "error")
            return
        if key.strip():
            shell.app.notify(f"{provider}: key saved", "success")
        elif get_api_key(provider):
            # the stored key is gone, but the environment still provides one
            shell.app.notify(
                f"{provider}: stored key removed · still set from the environment",
                "warning",
            )
        else:
            shell.app.notify(f"{provider}: key removed", "success")

    # -- /model, /theme, /renderer --------------------------------------

    async def pick_model(self, entry: Any) -> None:
        """kt-cli's model picker: filter, ←/→ variations, enter applies through /model."""
        try:
            presets = list_all_presets()
        except Exception as exc:
            entry.view.render("error", f"could not list models: {exc}")
            return
        items = model_items(presets, model_id(entry.agent))
        if not items:
            self.shell.app.notify("no models configured", "warning")
            return
        title = f"Model · {entry.view.name} · now {model_id(entry.agent) or '?'}"
        spec = Picker(
            items=items,
            title=title,
            placeholder="type to filter by provider or model",
            max_visible=10,
            sidebar=True,
        )
        answer = await self.shell.panel(spec)
        selector = selector_of(answer)
        if selector:
            await self.shell.command(f"/model {selector}")

    async def choose_theme(self, name: str) -> None:
        if not name:
            current = self.shell.config.theme
            items = [{"label": t, "value": t, "current": t == current} for t in THEMES]
            name = await self.shell.panel(Picker(items=items, title="Theme")) or ""
        if name not in THEMES:
            if name:
                self.shell.app.notify(f"unknown theme {name!r}", "error")
            return
        self.shell.app.set_theme(name)
        self.shell.config.theme = name
        self.shell.config.save()
        self.shell.app.notify(f"theme: {name}", "success")

    def set_renderer(self, name: str) -> None:
        if name not in RENDERERS:
            self.shell.app.notify(
                f"renderer: {self.shell.config.renderer} · choose {' | '.join(RENDERERS)}",
                "info",
            )
            return
        self.shell.config.renderer = name
        self.shell.config.save()
        self.shell.app.set_mode("fullscreen" if name == "fullscreen" else "inline")
        self.shell.app.notify(f"renderer: {name} (remembered)", "success")


def _choice(label: str, name: str, options: Any, current: str, detail: str) -> dict:
    return {
        "label": label,
        "detail": detail or None,
        "value": f"set:{name}",
        "group": "display",
        "choices": [[name, list(options)]],
        "chosen": {name: current},
    }


def _safe(build: Any, section: str) -> list[dict]:
    """A section that fails to load is left out (logged), never breaks the picker."""
    try:
        return build()
    except Exception as exc:
        logger.warning("ktui settings section failed", section=section, error=str(exc))
        return []
