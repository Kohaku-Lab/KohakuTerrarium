"""Module picker overlay — interactive runtime config for plugins +
native tools rendered inside the Rich CLI live region.

Pattern mirrors :class:`SettingsOverlay`:

* Tabs across the top (one per module type).
* List mode with a row-per-module cursor; ``t`` toggles enable/
  disable on plugin rows; ``Enter`` opens the edit form.
* Form mode with schema-driven fields. ``Tab`` / ``↑↓`` cycles
  fields, ``←→`` cycles enum options, printable chars edit string
  / numeric fields, ``Enter`` submits.
* No confirm sub-mode — option edits just save; toggle is reversible.

Pushed by:

* The ``/module`` (no-args) slash-command intercept in
  :class:`RichCLIApp._handle_slash` (analogous to ``/model``).
* The ``/module edit <name>`` intercept which opens straight into
  the form for the named module, skipping ``$EDITOR``.

Lives inside the live region the same way ``ModelPicker`` and
``SettingsOverlay`` do; the app routes ``_status_text`` through
``self.module_picker.render(width)`` while ``visible`` is True.
"""

import json
from typing import Any, Callable

from kohakuterrarium.builtins.cli_rich.dialogs.module_picker_model import (
    DEFAULT_TAB_ORDER,
    ModuleEntry,
    ModuleFormField,
    ModuleFormState,
    module_key,
)
from kohakuterrarium.builtins.cli_rich.dialogs.module_picker_render import (
    render_overlay,
)
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


class ModulePicker:
    """Tabbed module picker with list + edit-form modes.

    The :class:`RichCLIApp` holds one instance and forwards key /
    text events through :meth:`handle_key` / :meth:`handle_text`.
    """

    def __init__(self, get_agent: Callable[[], Any]) -> None:
        self._get_agent = get_agent
        self.visible: bool = False
        self.mode: str = "list"  # list | form
        self.active_type: str = "plugin"
        self._entries_by_type: dict[str, list[ModuleEntry]] = {}
        self._cursor: dict[str, int] = {}
        self._form: ModuleFormState | None = None
        self._flash: str = ""

    # ── Lifecycle ───────────────────────────────────────────────

    def open(self, *, edit_target: str = "") -> None:
        """Show the overlay.

        ``edit_target`` (when set) is a ``name`` or ``type/name``
        reference that opens straight into the edit form for that
        module — used by ``/module edit <name>``. If the reference
        doesn't resolve, falls through to list mode.
        """
        self.mode = "list"
        self._form = None
        self._flash = ""
        self._reload()
        if edit_target.strip():
            entry = self._resolve(edit_target.strip())
            if entry is not None:
                self.active_type = entry.type
                self._open_form_for(entry)
        self.visible = True

    def close(self) -> None:
        self.visible = False
        self.mode = "list"
        self._form = None

    def is_capturing_text(self) -> bool:
        """True while a form is open — Composer routes printable
        characters here instead of into the chat textarea."""
        return self.visible and self.mode == "form" and self._form is not None

    # ── Data load ───────────────────────────────────────────────

    def _reload(self) -> None:
        agent = self._get_agent()
        if agent is None:
            self._entries_by_type = {}
            return
        plugins: list[ModuleEntry] = []
        natives: list[ModuleEntry] = []
        mgr = getattr(agent, "plugins", None)
        if mgr:
            for entry in mgr.list_plugins_with_options():
                plugins.append(
                    ModuleEntry(
                        type="plugin",
                        name=entry["name"],
                        description=entry.get("description", "") or "",
                        schema=entry.get("schema", {}) or {},
                        options=entry.get("options", {}) or {},
                        enabled=entry.get("enabled", True),
                        priority=entry.get("priority"),
                    )
                )
        registry = getattr(agent, "registry", None)
        helper = getattr(agent, "native_tool_options", None)
        if registry is not None:
            for name in sorted(registry.list_tools()):
                tool = registry.get_tool(name)
                if tool is None or not getattr(tool, "is_provider_native", False):
                    continue
                schema_fn = getattr(type(tool), "provider_native_option_schema", None)
                try:
                    schema = schema_fn() if callable(schema_fn) else {}
                except Exception:
                    schema = {}
                if not schema:
                    continue
                values = helper.get(name) if helper else {}
                natives.append(
                    ModuleEntry(
                        type="native_tool",
                        name=name,
                        description=getattr(tool, "description", "") or "",
                        schema=schema or {},
                        options=values or {},
                        enabled=None,
                        priority=None,
                    )
                )
        # Sort plugins enabled-on-top, priority ASC then name ASC.
        plugins.sort(
            key=lambda m: (
                0 if m.enabled else 1,
                50 if m.priority is None else int(m.priority),
                m.name,
            )
        )
        natives.sort(key=lambda m: m.name)
        self._entries_by_type = {
            "plugin": plugins,
            "native_tool": natives,
        }
        # Clamp / initialise cursors. New modules append at end so
        # this only matters when the active tab shrinks.
        for tid, entries in self._entries_by_type.items():
            cur = self._cursor.get(tid, 0)
            self._cursor[tid] = max(0, min(cur, max(0, len(entries) - 1)))
        # If the active tab is empty but another has content, jump.
        if not self._entries_by_type.get(self.active_type):
            for tid in DEFAULT_TAB_ORDER:
                if self._entries_by_type.get(tid):
                    self.active_type = tid
                    break

    def _resolve(self, ref: str) -> ModuleEntry | None:
        flat = [m for entries in self._entries_by_type.values() for m in entries]
        if "/" in ref:
            t, _, n = ref.partition("/")
            for m in flat:
                if m.type == t and m.name == n:
                    return m
            return None
        matches = [m for m in flat if m.name == ref]
        if len(matches) == 1:
            return matches[0]
        return None

    # ── Keyboard ────────────────────────────────────────────────

    def handle_key(self, key: str) -> bool:
        if not self.visible:
            return False
        if self.mode == "form":
            return self._form_key(key)
        return self._list_key(key)

    def handle_text(self, char: str) -> bool:
        if not self.visible or not char:
            return False
        if self.mode == "form":
            return self._form_text(char)
        # List mode: ``t`` toggles, otherwise swallow so stray chars
        # don't leak into the composer textarea.
        if char in ("t", "T"):
            self._toggle_current()
            return True
        return True

    # ── List mode ───────────────────────────────────────────────

    def _list_key(self, key: str) -> bool:
        if key == "escape":
            self.close()
            return True
        if key in ("up", "c-p"):
            self._move(-1)
            return True
        if key in ("down", "c-n"):
            self._move(1)
            return True
        if key == "pageup":
            self._move(-5)
            return True
        if key == "pagedown":
            self._move(5)
            return True
        if key == "tab":
            self._cycle_tab(1)
            return True
        if key in ("s-tab", "backtab"):
            self._cycle_tab(-1)
            return True
        if key == "enter":
            entry = self._current_entry()
            if entry is not None:
                self._open_form_for(entry)
            return True
        return True  # modal — swallow other named keys

    def _move(self, delta: int) -> None:
        entries = self._entries_by_type.get(self.active_type) or []
        if not entries:
            return
        cur = self._cursor.get(self.active_type, 0)
        self._cursor[self.active_type] = max(0, min(len(entries) - 1, cur + delta))
        self._flash = ""

    def _cycle_tab(self, delta: int) -> None:
        order = list(DEFAULT_TAB_ORDER)
        # Fall back to whatever types are present if the defaults
        # don't cover them. Stable order so navigation is predictable.
        for t in self._entries_by_type:
            if t not in order:
                order.append(t)
        if not order:
            return
        try:
            idx = order.index(self.active_type)
        except ValueError:
            idx = 0
        idx = (idx + delta) % len(order)
        self.active_type = order[idx]
        self._flash = ""

    def _current_entry(self) -> ModuleEntry | None:
        entries = self._entries_by_type.get(self.active_type) or []
        if not entries:
            return None
        cur = self._cursor.get(self.active_type, 0)
        return entries[max(0, min(cur, len(entries) - 1))]

    def _toggle_current(self) -> None:
        entry = self._current_entry()
        if entry is None or entry.enabled is None:
            return
        agent = self._get_agent()
        mgr = getattr(agent, "plugins", None) if agent else None
        if mgr is None:
            return
        try:
            if mgr.is_enabled(entry.name):
                mgr.disable(entry.name)
                self._flash = f"Plugin {entry.name!r} disabled"
            elif mgr.enable(entry.name):
                # ``load_pending`` is async; the toggle's effects on
                # ``list_plugins_with_options`` land before next reload
                # because enable() is synchronous. We don't await
                # load_pending here (would need an async key handler);
                # the next slash command or external trigger flushes
                # pending loads.
                self._flash = f"Plugin {entry.name!r} enabled"
            else:
                self._flash = f"Plugin {entry.name!r} not found"
        except Exception as e:
            self._flash = f"Toggle failed: {e}"
        self._reload()

    # ── Form mode ───────────────────────────────────────────────

    def _open_form_for(self, entry: ModuleEntry) -> None:
        if not entry.schema:
            self._flash = f"{entry.type}/{entry.name}: no editable options"
            return
        fields: list[ModuleFormField] = []
        for key, spec in entry.schema.items():
            spec = spec or {}
            kind = spec.get("type", "string")
            current = entry.options.get(key, spec.get("default"))
            value_text = _stringify(current, kind)
            options = (
                [str(v) for v in (spec.get("values") or [])] if kind == "enum" else None
            )
            fields.append(
                ModuleFormField(
                    label=key,
                    key=key,
                    kind=kind,
                    value=value_text,
                    options=options,
                    doc=spec.get("doc", "") or "",
                    minimum=spec.get("min"),
                    maximum=spec.get("max"),
                )
            )
        self._form = ModuleFormState(
            module_key=module_key(entry),
            title=f"{entry.type}/{entry.name}",
            fields=fields,
        )
        self.mode = "form"
        self._flash = ""

    def _form_current(self) -> ModuleFormField | None:
        if self._form is None or not self._form.fields:
            return None
        return self._form.fields[self._form.cursor]

    def _form_key(self, key: str) -> bool:
        if self._form is None:
            return False
        if key == "escape":
            self._form = None
            self.mode = "list"
            self._reload()
            return True
        if key in ("tab", "down"):
            self._form.cursor = (self._form.cursor + 1) % len(self._form.fields)
            return True
        if key in ("s-tab", "backtab", "up"):
            self._form.cursor = (self._form.cursor - 1) % len(self._form.fields)
            return True
        if key == "left":
            fld = self._form_current()
            if fld is not None:
                self._cycle_field(fld, -1)
            return True
        if key == "right":
            fld = self._form_current()
            if fld is not None:
                self._cycle_field(fld, +1)
            return True
        if key in ("backspace", "c-h"):
            fld = self._form_current()
            if fld is not None and not fld.options and fld.value:
                fld.value = fld.value[:-1]
                fld.error = ""
            return True
        if key == "enter":
            # Last field → submit; otherwise advance.
            if self._form.cursor < len(self._form.fields) - 1:
                self._form.cursor += 1
                return True
            self._form_submit()
            return True
        return True

    def _form_text(self, char: str) -> bool:
        if self._form is None:
            return False
        fld = self._form_current()
        if fld is None:
            return True
        if fld.options:
            # Enum: typed letter jumps to first option starting with it.
            for opt in fld.options:
                if opt.lower().startswith(char.lower()):
                    fld.value = opt
                    fld.error = ""
                    return True
            return True
        if fld.kind == "bool":
            # Bool: ``y`` / ``t`` / ``1`` → true; ``n`` / ``f`` / ``0`` → false.
            lc = char.lower()
            if lc in ("y", "t", "1"):
                fld.value = "true"
                fld.error = ""
            elif lc in ("n", "f", "0"):
                fld.value = "false"
                fld.error = ""
            return True
        if fld.kind in ("int", "float"):
            # Allow only digits, sign, decimal point, and ``e`` for
            # scientific notation (float).
            allowed = set("0123456789-+")
            if fld.kind == "float":
                allowed |= set(".eE")
            if char in allowed:
                fld.value += char
                fld.error = ""
            return True
        # string / list / dict — accept any printable.
        fld.value += char
        fld.error = ""
        return True

    def _cycle_field(self, fld: ModuleFormField, delta: int) -> None:
        if not fld.options:
            return
        if fld.value in fld.options:
            i = fld.options.index(fld.value)
        else:
            i = 0
        fld.value = fld.options[(i + delta) % len(fld.options)]
        fld.error = ""

    def _form_submit(self) -> None:
        if self._form is None:
            return
        agent = self._get_agent()
        if agent is None:
            self._form.message = "No agent in context"
            return
        # Find the underlying module record (may have shifted across
        # reloads — resolve by composite key).
        target: ModuleEntry | None = None
        for entries in self._entries_by_type.values():
            for m in entries:
                if module_key(m) == self._form.module_key:
                    target = m
                    break
            if target is not None:
                break
        if target is None:
            self._form.message = "Module disappeared"
            return
        # Coerce + validate field values.
        payload: dict[str, Any] = {}
        for fld in self._form.fields:
            try:
                payload[fld.key] = _coerce(fld)
            except ValueError as exc:
                fld.error = str(exc)
                self._form.message = f"{fld.label}: {exc}"
                return
        # Diff against current options — only send changed keys.
        diff = {
            k: v
            for k, v in payload.items()
            if json.dumps(v, sort_keys=True, default=str)
            != json.dumps(target.options.get(k), sort_keys=True, default=str)
        }
        if not diff:
            self._form = None
            self.mode = "list"
            self._flash = "No changes"
            self._reload()
            return
        try:
            if target.type == "plugin":
                helper = getattr(agent, "plugin_options", None)
                if helper is None:
                    raise RuntimeError("agent has no plugin_options helper")
                helper.set(target.name, diff)
            elif target.type == "native_tool":
                helper = getattr(agent, "native_tool_options", None)
                if helper is None:
                    raise RuntimeError("agent has no native_tool_options helper")
                merged = dict(helper.get(target.name))
                merged.update(diff)
                helper.set(target.name, merged)
            else:
                raise ValueError(f"Unsupported module type: {target.type!r}")
        except Exception as e:
            self._form.message = f"Save failed: {e}"
            return
        self._form = None
        self.mode = "list"
        self._flash = f"Saved {target.type}/{target.name}"
        self._reload()

    # ── Rendering ───────────────────────────────────────────────

    def render(self, width: int) -> str:
        return render_overlay(self, width)


# ── Helpers ─────────────────────────────────────────────────────


def _stringify(value: Any, kind: str) -> str:
    """Render a stored option value as the editable text the form shows."""
    if value is None:
        return ""
    if kind == "bool":
        return "true" if bool(value) else "false"
    if kind == "list":
        if isinstance(value, list):
            return "\n".join(str(v) for v in value)
        return str(value)
    if kind == "dict":
        try:
            return json.dumps(value, indent=2) if value else ""
        except (TypeError, ValueError):
            return str(value)
    return str(value)


def _coerce(fld: ModuleFormField) -> Any:
    """Coerce a form field's text back to the right typed value.

    Raises ``ValueError`` on invalid input — the form catches and
    surfaces these as per-field errors so the user can correct
    without losing other edits.
    """
    text = (fld.value or "").strip()
    kind = fld.kind
    if kind == "enum":
        if not text:
            return None
        return text
    if kind == "string":
        return text or None
    if kind == "bool":
        if not text:
            return None
        if text.lower() in ("true", "yes", "y", "1", "on"):
            return True
        if text.lower() in ("false", "no", "n", "0", "off"):
            return False
        raise ValueError("expected true/false")
    if kind == "int":
        if not text:
            return None
        try:
            v = int(text)
        except ValueError as exc:
            raise ValueError("not an integer") from exc
        _check_range(v, fld)
        return v
    if kind == "float":
        if not text:
            return None
        try:
            v = float(text)
        except ValueError as exc:
            raise ValueError("not a number") from exc
        _check_range(v, fld)
        return v
    if kind == "list":
        if not text:
            return []
        return [s.strip() for s in text.split("\n") if s.strip()]
    if kind == "dict":
        if not text:
            return None
        try:
            return json.loads(text)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"invalid JSON ({exc})") from exc
    return text or None


def _check_range(v: float | int, fld: ModuleFormField) -> None:
    if fld.minimum is not None and v < fld.minimum:
        raise ValueError(f"must be ≥ {fld.minimum}")
    if fld.maximum is not None and v > fld.maximum:
        raise ValueError(f"must be ≤ {fld.maximum}")
