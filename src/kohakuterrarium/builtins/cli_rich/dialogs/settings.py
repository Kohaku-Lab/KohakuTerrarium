"""Settings overlay — interactive config editor rendered inside the live region.

Mirrors the ModelPicker pattern: the RichCLIApp holds a single instance,
``open()`` flips ``visible`` True, and the app routes the status area
through ``render(width)`` until the overlay is closed. Key events arrive
via ``handle_key(name)`` for named keys and ``handle_text(char)`` for
printable chars (plugged into prompt_toolkit's ``Keys.Any`` with a
filter that only fires while the overlay is capturing input).

Scope covers the four non-trivial config surfaces today:

    Keys       — API keys per built-in provider (masked list + edit).
    Providers  — built-in (read-only) + user-defined backends (CRUD).
    Models     — presets (user + built-in). Set default + delete user.
    MCP        — MCP servers (name/transport/command/url CRUD).

Advanced fields that don't fit a single-line text box — variation_groups
on presets, MCP ``args``/``env`` JSON — are still editable via
``kt config`` / direct YAML and are intentionally skipped here. Everything
else persists through the same functions the web frontend calls, so the
CLI overlay, ``kt config``, and the settings page stay in lock-step.
"""

from dataclasses import dataclass, field
from typing import Any

from kohakuterrarium.api.routes.settings import _load_mcp_config, _save_mcp_config
from kohakuterrarium.builtins.cli_rich.dialogs.settings_render import render_overlay
from kohakuterrarium.llm.api_keys import PROVIDER_KEY_MAP, get_api_key, save_api_key
from kohakuterrarium.llm.profile_types import LLMBackend
from kohakuterrarium.llm.profiles import (
    delete_backend,
    delete_profile,
    get_default_model,
    list_all as list_all_presets,
    load_backends,
    load_presets,
    save_backend,
    set_default_model,
)
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

TABS: list[str] = ["Keys", "Providers", "Models", "MCP"]

_BUILTIN_PROVIDERS = {"codex", "openai", "openrouter", "anthropic", "gemini", "mimo"}
_BACKEND_TYPES = ["openai", "codex"]
_TRANSPORTS = ["stdio", "http"]


@dataclass
class FormField:
    """Single editable row inside a form."""

    label: str
    key: str
    value: str = ""
    # When non-empty, the field is an enum; left/right cycles options.
    options: list[str] | None = None
    # When True, the field accepts free text (password-style mask in render).
    secret: bool = False
    # Optional help text shown dim below the field label.
    hint: str = ""


@dataclass
class FormState:
    title: str
    action: str  # one of: set_key, add_provider, edit_provider, add_mcp, edit_mcp
    fields: list[FormField]
    cursor: int = 0
    message: str = ""  # transient error/warning shown while the form is open
    # Context passed back to the action handler (e.g. provider name for set_key).
    context: dict[str, Any] = field(default_factory=dict)


@dataclass
class ConfirmState:
    message: str
    action: str
    context: dict[str, Any] = field(default_factory=dict)


class SettingsOverlay:
    """Tabbed settings editor with list/form/confirm modes."""

    def __init__(self) -> None:
        self.visible = False
        self.mode = "list"  # list | form | confirm
        self.tab = TABS[0]
        self._cursor: dict[str, int] = {t: 0 for t in TABS}
        self._entries: dict[str, list[dict[str, Any]]] = {t: [] for t in TABS}
        self._form: FormState | None = None
        self._confirm: ConfirmState | None = None
        self._flash: str = ""  # transient status line under the tab row

    # ── Lifecycle ───────────────────────────────────────────────

    def open(self) -> None:
        self.mode = "list"
        self.tab = TABS[0]
        self._form = None
        self._confirm = None
        self._flash = ""
        self._refresh_all()
        self.visible = True

    def close(self) -> None:
        self.visible = False
        self.mode = "list"
        self._form = None
        self._confirm = None

    def is_capturing_text(self) -> bool:
        """True when printable characters should flow into a form field."""
        return self.visible and self.mode == "form" and self._form is not None

    # ── Data refresh ────────────────────────────────────────────

    def _refresh_all(self) -> None:
        for tab in TABS:
            self._refresh_tab(tab)

    def _refresh_tab(self, tab: str) -> None:
        try:
            if tab == "Keys":
                self._entries[tab] = self._load_keys()
            elif tab == "Providers":
                self._entries[tab] = self._load_providers()
            elif tab == "Models":
                self._entries[tab] = self._load_models()
            elif tab == "MCP":
                self._entries[tab] = self._load_mcp()
        except Exception as e:
            logger.warning("settings: refresh failed", tab=tab, error=str(e))
            self._entries[tab] = []
        # Clamp cursor to new list size.
        total = len(self._entries[tab])
        if total == 0:
            self._cursor[tab] = 0
        else:
            self._cursor[tab] = min(self._cursor.get(tab, 0), total - 1)

    def _load_keys(self) -> list[dict[str, Any]]:
        backends = load_backends()
        rows: list[dict[str, Any]] = []
        for name, backend in sorted(backends.items()):
            if backend.backend_type == "codex":
                # Codex uses OAuth, not an API key — surface that fact but
                # don't treat it as editable here (``kt login codex`` owns
                # the OAuth flow).
                rows.append(
                    {
                        "provider": name,
                        "masked": "(OAuth — use /login codex)",
                        "has_key": False,
                        "env": "",
                        "readonly": True,
                    }
                )
                continue
            key = get_api_key(name)
            masked = _mask_key(key) if key else ""
            rows.append(
                {
                    "provider": name,
                    "masked": masked or "(not set)",
                    "has_key": bool(key),
                    "env": backend.api_key_env or PROVIDER_KEY_MAP.get(name, ""),
                    "readonly": False,
                }
            )
        return rows

    def _load_providers(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for name, backend in sorted(load_backends().items()):
            rows.append(
                {
                    "name": name,
                    "backend_type": backend.backend_type,
                    "base_url": backend.base_url or "",
                    "api_key_env": backend.api_key_env or "",
                    "built_in": name in _BUILTIN_PROVIDERS,
                }
            )
        rows.append({"name": "+ Add new provider…", "is_add_row": True})
        return rows

    def _load_models(self) -> list[dict[str, Any]]:
        entries = list_all_presets()
        user_names = set(load_presets().keys())
        default = get_default_model()
        rows: list[dict[str, Any]] = []
        for e in entries:
            rows.append(
                {
                    "name": e["name"],
                    "model": e["model"],
                    "provider": e.get("provider") or "",
                    "available": e.get("available", True),
                    "is_default": e["name"] == default or e["model"] == default,
                    "user_defined": e["name"] in user_names,
                }
            )
        return rows

    def _load_mcp(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for s in _load_mcp_config():
            rows.append(
                {
                    "name": s.get("name", ""),
                    "transport": s.get("transport", "stdio"),
                    "command": s.get("command", ""),
                    "url": s.get("url", ""),
                    "args": s.get("args", []),
                    "env": s.get("env", {}),
                    "raw": s,
                }
            )
        rows.append({"name": "+ Add new MCP server…", "is_add_row": True})
        return rows

    # ── Keyboard ────────────────────────────────────────────────

    def handle_key(self, key: str) -> bool:
        """Route named keys (up/down/enter/escape/tab/backspace/…)."""
        if not self.visible:
            return False
        if self.mode == "confirm":
            return self._confirm_key(key)
        if self.mode == "form":
            return self._form_key(key)
        return self._list_key(key)

    def handle_text(self, char: str) -> bool:
        """Route a printable char (single-character ``event.data``)."""
        if not self.visible or not char:
            return False
        if self.mode == "confirm":
            if char in ("y", "Y"):
                self._confirm_apply(True)
                return True
            if char in ("n", "N"):
                self._confirm_apply(False)
                return True
            return True  # swallow stray chars in confirm mode
        if self.mode == "form":
            field_ = self._form_current_field()
            if field_ is None:
                return True
            # Enum fields: type letter jumps to first option starting with it
            # (bash-readline style). Keeps UX consistent with left/right.
            if field_.options:
                for opt in field_.options:
                    if opt.lower().startswith(char.lower()):
                        field_.value = opt
                        return True
                return True
            field_.value += char
            return True
        # list mode — actions on letter keys
        return self._list_letter(char)

    # ── List mode ───────────────────────────────────────────────

    def _list_key(self, key: str) -> bool:
        if key in ("escape",):
            self.close()
            return True
        if key in ("up", "c-p"):
            self._move(-1)
            return True
        if key in ("down", "c-n"):
            self._move(1)
            return True
        if key in ("pageup",):
            self._move(-5)
            return True
        if key in ("pagedown",):
            self._move(5)
            return True
        if key in ("tab",):
            self._cycle_tab(1)
            return True
        if key in ("s-tab", "backtab"):
            self._cycle_tab(-1)
            return True
        if key in ("enter",):
            self._list_activate()
            return True
        return False

    def _list_letter(self, char: str) -> bool:
        if char == "d":
            self._list_delete()
            return True
        # Consume other printable chars silently so they don't leak into
        # the composer's textarea. This matters because the overlay is
        # modal — anything the user types with it open should stay here.
        return True

    def _current_row(self) -> dict[str, Any] | None:
        rows = self._entries.get(self.tab) or []
        if not rows:
            return None
        idx = max(0, min(self._cursor[self.tab], len(rows) - 1))
        return rows[idx]

    def _move(self, delta: int) -> None:
        rows = self._entries.get(self.tab) or []
        if not rows:
            return
        self._cursor[self.tab] = max(
            0, min(len(rows) - 1, self._cursor[self.tab] + delta)
        )
        self._flash = ""

    def _cycle_tab(self, delta: int) -> None:
        idx = TABS.index(self.tab)
        idx = (idx + delta) % len(TABS)
        self.tab = TABS[idx]
        self._flash = ""

    def _list_activate(self) -> None:
        row = self._current_row()
        if row is None:
            return
        if self.tab == "Keys":
            if row.get("readonly"):
                self._flash = f"{row['provider']}: OAuth — run /login codex"
                return
            self._form = FormState(
                title=f"Set API key · {row['provider']}",
                action="set_key",
                fields=[
                    FormField(
                        label="API Key",
                        key="key",
                        value="",
                        secret=True,
                        hint=(f"env fallback: {row['env']}" if row["env"] else ""),
                    ),
                ],
                context={"provider": row["provider"]},
            )
            self.mode = "form"
            return
        if self.tab == "Providers":
            if row.get("is_add_row"):
                self._form = self._build_provider_form(None)
                self.mode = "form"
                return
            if row.get("built_in"):
                self._flash = f"{row['name']} is built-in (read-only)"
                return
            self._form = self._build_provider_form(row)
            self.mode = "form"
            return
        if self.tab == "Models":
            if not row.get("available"):
                self._flash = f"{row['name']}: provider has no key configured"
                return
            try:
                set_default_model(row["name"])
                self._flash = f"Default model set: {row['name']}"
                self._refresh_tab("Models")
            except Exception as e:
                self._flash = f"Error: {e}"
            return
        if self.tab == "MCP":
            if row.get("is_add_row"):
                self._form = self._build_mcp_form(None)
                self.mode = "form"
                return
            self._form = self._build_mcp_form(row)
            self.mode = "form"
            return

    def _list_delete(self) -> None:
        row = self._current_row()
        if row is None or row.get("is_add_row"):
            return
        if self.tab == "Keys":
            if row.get("readonly") or not row.get("has_key"):
                return
            self._confirm = ConfirmState(
                message=f"Clear API key for {row['provider']}?",
                action="delete_key",
                context={"provider": row["provider"]},
            )
            self.mode = "confirm"
            return
        if self.tab == "Providers":
            if row.get("built_in"):
                self._flash = f"{row['name']}: built-in, cannot delete"
                return
            self._confirm = ConfirmState(
                message=f"Delete provider {row['name']}?",
                action="delete_provider",
                context={"name": row["name"]},
            )
            self.mode = "confirm"
            return
        if self.tab == "Models":
            if not row.get("user_defined"):
                self._flash = f"{row['name']}: built-in preset, cannot delete"
                return
            self._confirm = ConfirmState(
                message=f"Delete preset {row['name']}?",
                action="delete_preset",
                context={"name": row["name"]},
            )
            self.mode = "confirm"
            return
        if self.tab == "MCP":
            self._confirm = ConfirmState(
                message=f"Delete MCP server {row['name']}?",
                action="delete_mcp",
                context={"name": row["name"]},
            )
            self.mode = "confirm"
            return

    # ── Form mode ───────────────────────────────────────────────

    def _build_provider_form(self, row: dict[str, Any] | None) -> FormState:
        editing = row is not None
        return FormState(
            title=("Edit provider" if editing else "Add provider"),
            action=("edit_provider" if editing else "add_provider"),
            fields=[
                FormField(
                    label="Name",
                    key="name",
                    value=(row["name"] if row else ""),
                    hint="unique id (no spaces)",
                ),
                FormField(
                    label="Backend type",
                    key="backend_type",
                    value=(row["backend_type"] if row else "openai"),
                    options=list(_BACKEND_TYPES),
                    hint="← → to cycle",
                ),
                FormField(
                    label="Base URL",
                    key="base_url",
                    value=(row.get("base_url", "") if row else ""),
                    hint="e.g. https://api.example.com/v1",
                ),
                FormField(
                    label="API key env",
                    key="api_key_env",
                    value=(row.get("api_key_env", "") if row else ""),
                    hint="optional env fallback name",
                ),
            ],
            context={"original_name": row["name"] if row else ""},
        )

    def _build_mcp_form(self, row: dict[str, Any] | None) -> FormState:
        editing = row is not None
        return FormState(
            title=("Edit MCP server" if editing else "Add MCP server"),
            action=("edit_mcp" if editing else "add_mcp"),
            fields=[
                FormField(
                    label="Name",
                    key="name",
                    value=(row["name"] if row else ""),
                    hint="unique id",
                ),
                FormField(
                    label="Transport",
                    key="transport",
                    value=(row["transport"] if row else "stdio"),
                    options=list(_TRANSPORTS),
                    hint="← → to cycle",
                ),
                FormField(
                    label="Command",
                    key="command",
                    value=(row.get("command", "") if row else ""),
                    hint="executable for stdio",
                ),
                FormField(
                    label="URL",
                    key="url",
                    value=(row.get("url", "") if row else ""),
                    hint="endpoint for http",
                ),
            ],
            context={
                "original_name": row["name"] if row else "",
                "preserved_args": row.get("args", []) if row else [],
                "preserved_env": row.get("env", {}) if row else {},
            },
        )

    def _form_current_field(self) -> FormField | None:
        if self._form is None or not self._form.fields:
            return None
        return self._form.fields[self._form.cursor]

    def _form_key(self, key: str) -> bool:
        if self._form is None:
            return False
        if key == "escape":
            self._form = None
            self.mode = "list"
            return True
        if key in ("tab", "down"):
            self._form.cursor = (self._form.cursor + 1) % len(self._form.fields)
            return True
        if key in ("s-tab", "backtab", "up"):
            self._form.cursor = (self._form.cursor - 1) % len(self._form.fields)
            return True
        if key == "left":
            field_ = self._form_current_field()
            if field_ and field_.options:
                self._cycle_field_option(field_, -1)
            return True
        if key == "right":
            field_ = self._form_current_field()
            if field_ and field_.options:
                self._cycle_field_option(field_, +1)
            return True
        if key in ("backspace", "c-h"):
            field_ = self._form_current_field()
            if field_ and not field_.options and field_.value:
                field_.value = field_.value[:-1]
            return True
        if key == "enter":
            # Last field → submit; otherwise advance.
            if self._form.cursor < len(self._form.fields) - 1:
                self._form.cursor += 1
                return True
            self._form_submit()
            return True
        return True  # swallow — the form is modal while open

    def _cycle_field_option(self, field_: FormField, delta: int) -> None:
        opts = field_.options or []
        if not opts:
            return
        if field_.value in opts:
            i = opts.index(field_.value)
        else:
            i = 0
        field_.value = opts[(i + delta) % len(opts)]

    def _form_submit(self) -> None:
        if self._form is None:
            return
        values = {f.key: f.value.strip() for f in self._form.fields}
        action = self._form.action
        ctx = self._form.context
        try:
            if action == "set_key":
                provider = ctx["provider"]
                if not values.get("key"):
                    self._form.message = "Key cannot be empty"
                    return
                save_api_key(provider, values["key"])
                self._flash = f"API key saved for {provider}"
                self._refresh_tab("Keys")
            elif action in ("add_provider", "edit_provider"):
                name = values.get("name", "")
                if not name:
                    self._form.message = "Name is required"
                    return
                if action == "add_provider" and name in load_backends():
                    self._form.message = f"Provider {name} already exists"
                    return
                # Rename not supported inline — delete + readd would lose
                # presets pinned to the old name. Block it explicitly.
                if (
                    action == "edit_provider"
                    and ctx.get("original_name")
                    and name != ctx["original_name"]
                ):
                    self._form.message = "Rename unsupported; delete + add"
                    return
                backend = LLMBackend(
                    name=name,
                    backend_type=values.get("backend_type", "openai"),
                    base_url=values.get("base_url", ""),
                    api_key_env=values.get("api_key_env", ""),
                )
                save_backend(backend)
                self._flash = f"Provider saved: {name}"
                self._refresh_tab("Providers")
                self._refresh_tab("Keys")
            elif action in ("add_mcp", "edit_mcp"):
                name = values.get("name", "")
                if not name:
                    self._form.message = "Name is required"
                    return
                servers = _load_mcp_config()
                # Rename allowed on edit (filter by original, replace).
                original = ctx.get("original_name", "")
                servers = [s for s in servers if s.get("name") not in {name, original}]
                servers.append(
                    {
                        "name": name,
                        "transport": values.get("transport", "stdio"),
                        "command": values.get("command", ""),
                        "url": values.get("url", ""),
                        "args": ctx.get("preserved_args", []),
                        "env": ctx.get("preserved_env", {}),
                    }
                )
                _save_mcp_config(servers)
                self._flash = f"MCP server saved: {name}"
                self._refresh_tab("MCP")
        except Exception as e:
            self._form.message = f"Error: {e}"
            return
        self._form = None
        self.mode = "list"

    # ── Confirm mode ────────────────────────────────────────────

    def _confirm_key(self, key: str) -> bool:
        if key == "escape":
            self._confirm = None
            self.mode = "list"
            return True
        if key == "enter":
            self._confirm_apply(True)
            return True
        return True  # modal — swallow other named keys

    def _confirm_apply(self, yes: bool) -> None:
        state = self._confirm
        self._confirm = None
        self.mode = "list"
        if not yes or state is None:
            return
        try:
            if state.action == "delete_key":
                save_api_key(state.context["provider"], "")
                self._flash = f"Cleared key: {state.context['provider']}"
                self._refresh_tab("Keys")
            elif state.action == "delete_provider":
                delete_backend(state.context["name"])
                self._flash = f"Deleted provider: {state.context['name']}"
                self._refresh_tab("Providers")
                self._refresh_tab("Keys")
            elif state.action == "delete_preset":
                delete_profile(state.context["name"])
                self._flash = f"Deleted preset: {state.context['name']}"
                self._refresh_tab("Models")
            elif state.action == "delete_mcp":
                servers = _load_mcp_config()
                servers = [s for s in servers if s.get("name") != state.context["name"]]
                _save_mcp_config(servers)
                self._flash = f"Deleted MCP server: {state.context['name']}"
                self._refresh_tab("MCP")
        except Exception as e:
            self._flash = f"Error: {e}"

    # ── Rendering ───────────────────────────────────────────────

    def render(self, width: int) -> str:
        return render_overlay(self, width)


def _mask_key(key: str) -> str:
    if len(key) <= 8:
        return "••••"
    return f"{key[:4]}…{key[-4:]}"
