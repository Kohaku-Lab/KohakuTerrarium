"""Creating and editing configuration from ``/config`` and the Settings page, through the
same functions as ``kt config``, kt-cli's settings and the web settings page:

* **Providers**: add (name, backend type, base URL, key variable), edit, delete (built-in
  ones are read only); ``/login <provider>`` signs in (Codex OAuth, or the API key).
* **Models**: add or edit a preset (name, provider, model id, context and output sizes,
  temperature, reasoning effort), delete one of yours, make one the default.
* **MCP servers**: add, edit, delete.
* **Goals**: create a goal drive for the focused creature.

Each entry opens a menu of its actions, then a form (``forms.py``).
"""

import asyncio
from typing import TYPE_CHECKING

from kohakutui.ui import Confirm, Picker

from kohakuterrarium.builtins.ktui.format import one_line
from kohakuterrarium.cli.auth import login_cli
from kohakuterrarium.llm.profiles.backends import _BUILTIN_PROVIDER_NAMES
from kohakuterrarium.llm.profiles.resolve import (
    delete_backend,
    delete_profile,
    load_backends,
    load_presets,
    save_backend,
    save_profile,
)
from kohakuterrarium.llm.profiles.types import LLMBackend, LLMPreset
from kohakuterrarium.studio.identity.llm.default import resolve_and_set_default
from kohakuterrarium.studio.identity.mcp.servers import load_servers, save_servers
from kohakuterrarium.terrarium.drive.goal import build_goal_spec
from kohakuterrarium.terrarium.drive.models import ActorRef
from kohakuterrarium.terrarium.drive.requests import CreateDriveRequest
from kohakuterrarium.terrarium.service.service import LocalTerrariumService
from kohakuterrarium.utils.logging import get_logger

if TYPE_CHECKING:
    from kohakuterrarium.builtins.ktui.shell import KtuiShell

logger = get_logger(__name__)

BACKEND_TYPES = ["openai", "codex", "anthropic", "gemini"]
TRANSPORTS = ["stdio", "http", "streamable_http"]
EFFORTS = ["", "minimal", "low", "medium", "high", "xhigh"]


def provider_rows() -> list[dict]:
    rows = []
    for name, b in sorted(load_backends().items()):
        built_in = name in _BUILTIN_PROVIDER_NAMES
        rows.append(
            {
                "label": name,
                "detail": " · ".join(
                    p
                    for p in (
                        b.backend_type,
                        b.base_url or "",
                        "built in" if built_in else "",
                    )
                    if p
                ),
                "value": f"cfg:provider:{name}",
            }
        )
    rows.append({"label": "+ add a provider", "value": "cfg:provider:", "tone": "info"})
    return rows


def model_rows() -> list[dict]:
    rows = [
        {
            "label": f"{provider}/{name}" if provider else name,
            "detail": one_line(preset.model, 40),
            "value": f"cfg:model:{provider}/{name}",
        }
        for (provider, name), preset in sorted(load_presets().items())
    ]
    rows.append({"label": "+ add a model", "value": "cfg:model:", "tone": "info"})
    return rows


def mcp_rows() -> list[dict]:
    rows = [
        {
            "label": s.get("name", "?"),
            "detail": " · ".join(
                str(p)
                for p in (s.get("transport", "stdio"), s.get("command") or s.get("url"))
                if p
            ),
            "value": f"cfg:mcp:{s.get('name', '')}",
        }
        for s in load_servers()
    ]
    rows.append({"label": "+ add an MCP server", "value": "cfg:mcp:", "tone": "info"})
    return rows


class ConfigEdit:
    def __init__(self, shell: "KtuiShell"):
        self.shell = shell

    async def act(self, what: str) -> None:
        """``<kind>:<name>`` from a settings row (an empty name adds one)."""
        kind, _, name = what.partition(":")
        try:
            match kind:
                case "provider":
                    await self.provider(name)
                case "model":
                    await self.model(name)
                case "mcp":
                    await self.mcp(name)
                case "goal":
                    await self.goal()
        except Exception as exc:
            logger.warning("ktui config edit failed", kind=kind, error=str(exc))
            self.shell.app.notify(f"{kind}: {one_line(str(exc), 90)}", "error")

    async def _menu(self, title: str, actions: list[tuple[str, str]]) -> str | None:
        items = [{"label": label, "value": value} for value, label in actions]
        answer = await self.shell.panel(Picker(items=items, title=title))
        return str(answer) if answer else None

    # -- providers ------------------------------------------------------

    async def provider(self, name: str) -> None:
        shell = self.shell
        backend = load_backends().get(name) if name else None
        if backend is not None:
            built_in = name in _BUILTIN_PROVIDER_NAMES
            actions = [("login", "sign in / set the key")]
            if not built_in:
                actions += [("edit", "edit"), ("delete", "delete")]
            choice = await self._menu(f"Provider {name}", actions)
            if choice == "login":
                await self.login(name)
                return
            if choice == "delete":
                if await shell.panel(Confirm(question=f"Delete provider {name}?")):
                    delete_backend(name)
                    shell.app.notify(f"provider {name} deleted", "success")
                return
            if choice != "edit":
                return
        values = await shell.forms.ask(
            "Edit provider" if backend else "Add provider",
            [
                {
                    "name": "name",
                    "label": "Name",
                    "value": name,
                    "hint": "unique id, no spaces",
                },
                {
                    "name": "backend_type",
                    "label": "Backend type",
                    "control": "select",
                    "options": BACKEND_TYPES,
                    "value": backend.backend_type if backend else "openai",
                },
                {
                    "name": "base_url",
                    "label": "Base URL",
                    "value": backend.base_url if backend else "",
                    "hint": "https://api.example.com/v1",
                },
                {
                    "name": "api_key_env",
                    "label": "API key variable",
                    "value": backend.api_key_env if backend else "",
                    "hint": "optional, e.g. MY_API_KEY",
                },
            ],
        )
        if not values:
            return
        new = values["name"].strip()
        if not new or " " in new:
            shell.app.notify("a provider needs a name without spaces", "error")
            return
        if backend is None and new in load_backends():
            shell.app.notify(f"provider {new} already exists", "error")
            return
        if backend is not None and new != name:
            shell.app.notify(
                "renaming is not supported: delete it and add a new one", "warning"
            )
            return
        save_backend(
            LLMBackend(
                name=new,
                backend_type=values["backend_type"],
                base_url=values["base_url"].strip(),
                api_key_env=values["api_key_env"].strip(),
            )
        )
        shell.app.notify(f"provider {new} saved", "success")

    async def login(self, provider: str) -> None:
        """Signs in to ``provider`` as ``kt login`` does, with the terminal given back for it."""
        shell = self.shell
        logs = shell.chrome.logs
        logs.release()
        try:
            code = await asyncio.to_thread(self._login_blocking, provider)
        finally:
            logs.capture()
        level = "success" if code == 0 else "warning"
        shell.app.notify(
            f"{provider}: {'signed in' if code == 0 else 'not signed in'}", level
        )

    def _login_blocking(self, provider: str) -> int:
        app = self.shell.app
        app.suspend()
        try:
            print(
                f"\n— sign in to {provider} (ktui comes back when done) —\n", flush=True
            )
            return login_cli(provider)
        except (EOFError, KeyboardInterrupt):
            return 1
        finally:
            app.resume()

    # -- models ---------------------------------------------------------

    async def model(self, key: str) -> None:
        shell = self.shell
        provider, _, name = key.partition("/")
        preset = load_presets().get((provider, name)) if key else None
        if preset is not None:
            choice = await self._menu(
                f"Model {key}",
                [
                    ("default", "make it the default"),
                    ("edit", "edit"),
                    ("delete", "delete"),
                ],
            )
            if choice == "default":
                identifier, error = resolve_and_set_default(key)
                shell.app.notify(
                    error or f"default model: {identifier}",
                    "error" if error else "success",
                )
                return
            if choice == "delete":
                if await shell.panel(Confirm(question=f"Delete model {key}?")):
                    delete_profile(name, provider)
                    shell.app.notify(f"model {key} deleted", "success")
                return
            if choice != "edit":
                return
        providers = sorted(load_backends())
        values = await shell.forms.ask(
            "Edit model" if preset else "Add model",
            [
                {"name": "name", "label": "Name", "value": name, "hint": "e.g. my-gpt"},
                {
                    "name": "provider",
                    "label": "Provider",
                    "control": "select",
                    "options": providers,
                    "value": provider or (providers[0] if providers else ""),
                },
                {
                    "name": "model",
                    "label": "Model id",
                    "value": preset.model if preset else "",
                    "hint": "what the provider calls it",
                },
                {
                    "name": "max_context",
                    "label": "Context window",
                    "value": str(preset.max_context if preset else 256000),
                },
                {
                    "name": "max_output",
                    "label": "Max output",
                    "value": str(preset.max_output if preset else 65536),
                },
                {
                    "name": "temperature",
                    "label": "Temperature",
                    "value": (
                        ""
                        if not preset or preset.temperature is None
                        else str(preset.temperature)
                    ),
                    "hint": "empty: the provider's",
                },
                {
                    "name": "reasoning_effort",
                    "label": "Reasoning effort",
                    "control": "select",
                    "options": EFFORTS,
                    "value": preset.reasoning_effort if preset else "",
                },
            ],
        )
        if not values:
            return
        if not values["name"].strip() or not values["model"].strip():
            shell.app.notify("a model needs a name and a model id", "error")
            return
        try:
            temperature = (
                float(values["temperature"]) if values["temperature"].strip() else None
            )
            max_context = int(values["max_context"])
            max_output = int(values["max_output"])
        except ValueError:
            shell.app.notify("sizes are whole numbers, temperature a number", "error")
            return
        updated = LLMPreset(
            name=values["name"].strip(),
            model=values["model"].strip(),
            provider=values["provider"],
            max_context=max_context,
            max_output=max_output,
            temperature=temperature,
            reasoning_effort=values["reasoning_effort"],
        )
        if preset is not None:
            # what the form does not show is kept
            updated.service_tier = preset.service_tier
            updated.extra_body = preset.extra_body
            updated.retry_policy = preset.retry_policy
            updated.provider_native_tools = preset.provider_native_tools
            updated.variation_groups = preset.variation_groups
        save_profile(updated)
        shell.app.notify(f"model {updated.provider}/{updated.name} saved", "success")

    # -- MCP ------------------------------------------------------------

    async def mcp(self, name: str) -> None:
        shell = self.shell
        servers = load_servers()
        server = (
            next((s for s in servers if s.get("name") == name), None) if name else None
        )
        if server is not None:
            choice = await self._menu(
                f"MCP server {name}", [("edit", "edit"), ("delete", "delete")]
            )
            if choice == "delete":
                if await shell.panel(Confirm(question=f"Delete MCP server {name}?")):
                    save_servers([s for s in servers if s.get("name") != name])
                    shell.app.notify(f"MCP server {name} deleted", "success")
                return
            if choice != "edit":
                return
        server = server or {}
        values = await shell.forms.ask(
            "Edit MCP server" if name else "Add MCP server",
            [
                {"name": "name", "label": "Name", "value": name},
                {
                    "name": "transport",
                    "label": "Transport",
                    "control": "select",
                    "options": TRANSPORTS,
                    "value": server.get("transport", "stdio"),
                },
                {
                    "name": "command",
                    "label": "Command",
                    "value": server.get("command", ""),
                    "hint": "stdio: the program to run",
                },
                {
                    "name": "url",
                    "label": "URL",
                    "value": server.get("url", ""),
                    "hint": "http: the endpoint",
                },
                {
                    "name": "connect_timeout",
                    "label": "Timeout (s)",
                    "value": str(server.get("connect_timeout") or ""),
                },
            ],
        )
        if not values or not values["name"].strip():
            return
        new = values["name"].strip()
        try:
            timeout = (
                float(values["connect_timeout"])
                if values["connect_timeout"].strip()
                else None
            )
        except ValueError:
            shell.app.notify("the timeout is a number of seconds", "error")
            return
        kept = [s for s in servers if s.get("name") not in {new, name}]
        kept.append(
            {
                "name": new,
                "transport": values["transport"],
                "command": values["command"].strip(),
                "url": values["url"].strip(),
                "connect_timeout": timeout,
                "args": server.get("args", []),
                "env": server.get("env", {}),
            }
        )
        save_servers(kept)
        shell.app.notify(f"MCP server {new} saved · new sessions use it", "success")

    # -- goals ----------------------------------------------------------

    async def goal(self) -> None:
        shell = self.shell
        if getattr(shell.engine, "drives", None) is None:
            shell.app.notify("this session has no drive runtime", "warning")
            return
        remote = getattr(shell.engine, "remote_drives", None)
        values = await shell.forms.ask(
            f"New goal for {shell.focused.view.name}",
            [
                {
                    "name": "objective",
                    "label": "Objective",
                    "hint": "what should be achieved",
                }
            ],
        )
        objective = (values or {}).get("objective", "").strip()
        if not objective:
            return
        if remote is not None:
            view = await remote.create_goal(objective, shell.focus_id)
            shell.app.notify(f"goal created: {view.record.drive_id}", "success")
            return
        actor = ActorRef("user", "local")
        graph_id = shell.focused.creature.graph_id
        request = CreateDriveRequest(
            kind="goal",
            title=objective[:120],
            scope_type="graph",
            scope_id=graph_id or "",
            owner=actor,
            owner_scope="actor",
            created_by=actor,
            spec=build_goal_spec(objective),
            assignee_creature_id=shell.focus_id,
        )
        view = await LocalTerrariumService(shell.engine).create_drive(
            request, graph_id=graph_id, actor=actor, operator=True
        )
        shell.app.notify(f"goal created: {view.record.drive_id}", "success")
