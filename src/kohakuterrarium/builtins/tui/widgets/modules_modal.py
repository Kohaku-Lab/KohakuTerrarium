"""Textual modal screens for the unified Modules surface (TUI).

Two screens:

- :class:`ModulesModal` — master view. Type tabs (Plugins / Native
  tools / future), each with a ``DataTable`` of modules. Bindings:
  ``Enter`` opens the edit modal; ``t`` toggles enable/disable on the
  highlighted plugin row; ``/`` focuses the search input; ``r``
  reloads; ``Esc`` closes.
- :class:`ModuleEditModal` — editor for a single module. Header shows
  description + toggle button (plugins only). Body is a schema-driven
  form: ``Switch`` for ``bool``, ``Select`` for ``enum``,
  ``Input`` for ``string`` / ``int`` / ``float``, ``TextArea`` for
  ``list`` (newline-separated) and ``dict`` (JSON). Footer has
  explicit ``Save`` and ``Cancel`` buttons; ``Ctrl+S`` saves and
  ``Esc`` cancels.

Both screens are pushed by ``AgentTUI.action_open_modules`` (bound to
``F2``). The agent reference is wired into :class:`TUISession` by
:meth:`TUIInput.set_user_commands` so screens can mutate options
through ``agent.plugin_options`` / ``agent.native_tool_options``
without round-tripping through the slash-command pipeline.
"""

import json
from typing import Any

from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import (
    Button,
    DataTable,
    Input,
    Label,
    Select,
    Static,
    Switch,
    TabbedContent,
    TabPane,
    TextArea,
)

from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


# ── Inventory + apply (talks to the live in-process agent) ───────


def _list_modules(agent: Any) -> list[dict[str, Any]]:
    """Return every configurable module on the agent.

    Mirrors :func:`builtins.user_commands.module._inventory` — kept
    here as a small duplicate to avoid the TUI screens importing
    user_commands. The inputs are tiny (4–10 modules typical) so the
    duplication cost is real but small.
    """
    out: list[dict[str, Any]] = []
    mgr = getattr(agent, "plugins", None)
    if mgr:
        for entry in mgr.list_plugins_with_options():
            out.append(
                {
                    "type": "plugin",
                    "name": entry["name"],
                    "description": entry.get("description", "") or "",
                    "schema": entry.get("schema", {}) or {},
                    "options": entry.get("options", {}) or {},
                    "enabled": entry.get("enabled", True),
                    "priority": entry.get("priority"),
                }
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
            out.append(
                {
                    "type": "native_tool",
                    "name": name,
                    "description": getattr(tool, "description", "") or "",
                    "schema": schema or {},
                    "options": values or {},
                    "enabled": None,
                    "priority": None,
                }
            )
    return out


def _apply_options(
    agent: Any, m: dict[str, Any], values: dict[str, Any]
) -> dict[str, Any]:
    if m["type"] == "plugin":
        helper = getattr(agent, "plugin_options", None)
        if helper is None:
            raise RuntimeError("agent has no plugin_options helper")
        return helper.set(m["name"], values)
    if m["type"] == "native_tool":
        helper = getattr(agent, "native_tool_options", None)
        if helper is None:
            raise RuntimeError("agent has no native_tool_options helper")
        merged = dict(helper.get(m["name"]))
        merged.update(values)
        return helper.set(m["name"], merged)
    raise ValueError(f"Unsupported module type: {m['type']!r}")


def _sort_key(m: dict[str, Any]) -> tuple[int, str]:
    p = m.get("priority")
    return (50 if p is None else int(p), m["name"])


# ── Master screen: list with type tabs ────────────────────────────


class ModulesModal(ModalScreen[None]):
    """Browse + open + toggle modules. Pushed by F2 in the TUI app."""

    DEFAULT_CSS = """
    ModulesModal {
        align: center middle;
    }
    #modules-container {
        width: 84;
        height: 32;
        border: thick #5A4FCF 60%;
        border-title-color: #5A4FCF;
        border-title-align: left;
        background: $surface;
        padding: 1 1 0 1;
    }
    #modules-search-row {
        height: 3;
        margin-bottom: 1;
    }
    #modules-search {
        width: 1fr;
    }
    ModulesModal DataTable {
        height: 1fr;
    }
    ModulesModal .hint {
        height: 1;
        color: $text-muted;
        text-align: center;
    }
    ModulesModal .empty {
        height: 1fr;
        content-align: center middle;
        color: $text-muted;
    }
    """

    BINDINGS = [
        Binding("escape", "close", "Close", show=True),
        Binding("enter", "open_selected", "Edit", show=True),
        Binding("t", "toggle_selected", "Toggle", show=True),
        Binding("slash", "focus_search", "Search", show=True),
        Binding("r", "reload", "Reload", show=True),
    ]

    def __init__(self, agent: Any) -> None:
        super().__init__()
        self._agent = agent
        self._modules: list[dict[str, Any]] = []
        # Per-type row index → module dict. Rebuilt on every
        # populate_table() so toggle/edit can resolve the highlighted
        # row to a module without scanning the global list.
        self._row_index: dict[str, list[dict[str, Any]]] = {}
        self._search: str = ""

    # Composition ────────────────────────────────────────────────

    def compose(self):
        with Vertical(id="modules-container"):
            with Horizontal(id="modules-search-row"):
                yield Input(placeholder="Filter modules…", id="modules-search")
            with TabbedContent(id="modules-tabs"):
                with TabPane("Plugins", id="tab-plugin"):
                    yield DataTable(id="table-plugin", cursor_type="row")
                with TabPane("Native tools", id="tab-native_tool"):
                    yield DataTable(id="table-native_tool", cursor_type="row")
            yield Static(
                "enter:edit  t:toggle  /:search  r:reload  esc:close",
                classes="hint",
            )

    def on_mount(self) -> None:
        self.query_one("#modules-container", Vertical).border_title = "Modules"
        for tid in ("plugin", "native_tool"):
            tbl = self.query_one(f"#table-{tid}", DataTable)
            tbl.add_columns("●", "name", "p", "opts")
        self.reload_modules()

    # Data load ──────────────────────────────────────────────────

    def reload_modules(self) -> None:
        try:
            self._modules = _list_modules(self._agent)
        except Exception as exc:  # pragma: no cover — defensive
            logger.warning("reload_modules failed", error=str(exc))
            self._modules = []
        self._populate_tables()

    def _populate_tables(self) -> None:
        q = self._search.strip().lower()
        for tid in ("plugin", "native_tool"):
            tbl = self.query_one(f"#table-{tid}", DataTable)
            tbl.clear()
            self._row_index[tid] = []

        plugins = [m for m in self._modules if m["type"] == "plugin"]
        natives = [m for m in self._modules if m["type"] == "native_tool"]

        # Group plugins enabled-on-top, sort by priority ASC.
        plugin_rows: list[dict[str, Any]] = []
        for want in (True, False):
            group = sorted((m for m in plugins if m["enabled"] is want), key=_sort_key)
            plugin_rows.extend(group)

        for m in plugin_rows:
            if not _matches(m, q):
                continue
            self._add_row("plugin", m)
        for m in sorted(natives, key=_sort_key):
            if not _matches(m, q):
                continue
            self._add_row("native_tool", m)

        # Update tab labels with counts.
        tabs = self.query_one("#modules-tabs", TabbedContent)
        tabs.get_tab("tab-plugin").label = f"Plugins ({len(self._row_index['plugin'])})"
        tabs.get_tab("tab-native_tool").label = (
            f"Native tools ({len(self._row_index['native_tool'])})"
        )

    def _add_row(self, tid: str, m: dict[str, Any]) -> None:
        tbl = self.query_one(f"#table-{tid}", DataTable)
        glyph = "●" if m["enabled"] is True else "○" if m["enabled"] is False else "-"
        pr = m.get("priority")
        pr_text = f"p{pr}" if pr is not None else ""
        n_opts = len(m.get("schema") or {})
        opts_text = f"{n_opts}" if n_opts else ""
        tbl.add_row(glyph, m["name"], pr_text, opts_text)
        self._row_index[tid].append(m)

    # Bindings ───────────────────────────────────────────────────

    def action_close(self) -> None:
        self.dismiss(None)

    def action_focus_search(self) -> None:
        self.query_one("#modules-search", Input).focus()

    def action_reload(self) -> None:
        self.reload_modules()

    def action_open_selected(self) -> None:
        m = self._highlighted_module()
        if m is None:
            return

        def _on_dismissed(_result: Any) -> None:
            self.reload_modules()

        self.app.push_screen(ModuleEditModal(self._agent, m), _on_dismissed)

    def action_toggle_selected(self) -> None:
        m = self._highlighted_module()
        if m is None or m.get("type") != "plugin":
            return
        mgr = getattr(self._agent, "plugins", None)
        if mgr is None:
            return
        name = m["name"]
        if mgr.is_enabled(name):
            mgr.disable(name)
        else:
            mgr.enable(name)
            self.app.run_worker(mgr.load_pending(), exclusive=False)
        # Refresh after a microtask so load_pending has a chance to
        # finish on the next loop tick. ``reload_modules`` is cheap.
        self.reload_modules()

    # Events ─────────────────────────────────────────────────────

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "modules-search":
            self._search = event.value
            self._populate_tables()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        # Mouse-click / Enter selects a row → open edit modal.
        # event.cursor_row is unreliable across Textual versions; rely
        # on the active tab + its row index.
        self.action_open_selected()

    # Helpers ────────────────────────────────────────────────────

    def _active_tab_id(self) -> str:
        tabs = self.query_one("#modules-tabs", TabbedContent)
        active = tabs.active or "tab-plugin"
        return active.replace("tab-", "")

    def _highlighted_module(self) -> dict[str, Any] | None:
        tid = self._active_tab_id()
        try:
            tbl = self.query_one(f"#table-{tid}", DataTable)
        except Exception:
            return None
        row = tbl.cursor_row
        rows = self._row_index.get(tid, [])
        if 0 <= row < len(rows):
            return rows[row]
        return None


def _matches(m: dict[str, Any], q: str) -> bool:
    if not q:
        return True
    return q in m["name"].lower() or q in (m.get("description") or "").lower()


# ── Edit screen: schema-driven form ──────────────────────────────


class ModuleEditModal(ModalScreen[bool]):
    """Edit one module's options. Returns ``True`` if changes saved."""

    DEFAULT_CSS = """
    ModuleEditModal {
        align: center middle;
    }
    #edit-container {
        width: 84;
        height: 36;
        border: thick #5A4FCF 60%;
        border-title-color: #5A4FCF;
        border-title-align: left;
        background: $surface;
        padding: 1 2;
    }
    #edit-header {
        height: auto;
        margin-bottom: 1;
    }
    #edit-description {
        color: $text-muted;
    }
    #edit-form {
        height: 1fr;
        border: round $surface-darken-1;
        padding: 1;
    }
    .field {
        height: auto;
        margin-bottom: 1;
    }
    .field-label {
        color: $text-muted;
    }
    .field TextArea {
        height: 5;
    }
    #edit-status {
        height: 1;
        color: $text-muted;
    }
    #edit-buttons {
        height: 3;
        align: right middle;
        margin-top: 1;
    }
    #edit-buttons Button {
        margin-left: 1;
    }
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=True),
        Binding("ctrl+s", "save", "Save", show=True),
    ]

    def __init__(self, agent: Any, module: dict[str, Any]) -> None:
        super().__init__()
        self._agent = agent
        self._module = module
        self._initial = dict(module.get("options") or {})
        # Per-key widget reference so save() can read current values.
        self._widgets: dict[str, Any] = {}
        self._status_text = ""

    def compose(self):
        m = self._module
        schema = m.get("schema") or {}
        with Vertical(id="edit-container"):
            with Vertical(id="edit-header"):
                yield Static(
                    f"[b]{m['type']}/{m['name']}[/b]"
                    + (
                        f"  [dim]p{m['priority']}[/dim]"
                        if m.get("priority") is not None
                        else ""
                    ),
                )
                if m.get("description"):
                    yield Static(m["description"], id="edit-description")
            with VerticalScroll(id="edit-form"):
                if not schema:
                    yield Static("This module has no runtime-mutable options.")
                else:
                    for key, spec in schema.items():
                        yield from self._compose_field(key, spec or {})
            yield Static("", id="edit-status")
            with Horizontal(id="edit-buttons"):
                yield Button("Cancel", id="btn-cancel", variant="default")
                yield Button("Save", id="btn-save", variant="primary")

    def on_mount(self) -> None:
        self.query_one("#edit-container", Vertical).border_title = (
            f"Edit {self._module['type']}/{self._module['name']}"
        )

    def _compose_field(self, key: str, spec: dict[str, Any]):
        """Yield label + widget rows for one schema entry."""
        kind = spec.get("type", "string")
        current = self._initial.get(key, spec.get("default"))
        doc = spec.get("doc") or ""
        with Vertical(classes="field"):
            yield Label(
                f"[b]{key}[/b]  [dim]({kind})[/dim]",
                classes="field-label",
            )
            if doc:
                yield Label(f"[dim]{doc}[/dim]")
            widget = self._make_widget(key, kind, spec, current)
            self._widgets[key] = widget
            yield widget

    def _make_widget(self, key: str, kind: str, spec: dict[str, Any], current: Any):
        if kind == "bool":
            return Switch(value=bool(current), id=f"f-{_safe(key)}")
        if kind == "enum":
            opts = [(str(v), str(v)) for v in (spec.get("values") or [])]
            allowed = {str(v) for v in (spec.get("values") or [])}
            initial: Any
            if current is not None and str(current) in allowed:
                initial = str(current)
            else:
                # ``Select.NULL`` is the sentinel for "no selection".
                # (Older Textual versions exposed ``Select.BLANK`` as
                # the same thing; in this version that's a broken
                # alias that resolves to ``False`` and trips the
                # validator.)
                initial = Select.NULL
            return Select(
                options=opts,
                value=initial,
                id=f"f-{_safe(key)}",
            )
        if kind == "list":
            text = "\n".join(current) if isinstance(current, list) else ""
            return TextArea(text, id=f"f-{_safe(key)}")
        if kind == "dict":
            text = ""
            if current is not None:
                try:
                    text = json.dumps(current, indent=2)
                except (TypeError, ValueError):
                    text = str(current)
            return TextArea(text, id=f"f-{_safe(key)}", language="json")
        # int / float / string → Input
        return Input(
            value="" if current is None else str(current),
            id=f"f-{_safe(key)}",
            placeholder=str(spec.get("default") or ""),
        )

    # Bindings + buttons ─────────────────────────────────────────

    def action_cancel(self) -> None:
        self.dismiss(False)

    def action_save(self) -> None:
        self._save()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-cancel":
            self.dismiss(False)
        elif event.button.id == "btn-save":
            self._save()

    def _save(self) -> None:
        schema = self._module.get("schema") or {}
        try:
            payload = self._collect_payload(schema)
        except ValueError as exc:
            self._set_status(f"[red]{exc}[/red]")
            return
        # Send only keys that changed from the initial snapshot.
        diff: dict[str, Any] = {}
        for key in payload:
            if json.dumps(payload[key], sort_keys=True, default=str) != json.dumps(
                self._initial.get(key), sort_keys=True, default=str
            ):
                diff[key] = payload[key]
        if not diff:
            self._set_status("[dim]No changes[/dim]")
            return
        try:
            applied = _apply_options(self._agent, self._module, diff)
        except (KeyError, ValueError, RuntimeError) as exc:
            self._set_status(f"[red]{exc}[/red]")
            return
        self._initial = dict(applied)
        self._set_status(f"[green]Saved {len(diff)} key(s).[/green]")
        # Auto-close on save so the user returns to the list with the
        # change reflected. Status flash is brief (~500ms) — Textual
        # actions can't trivially defer; dismiss immediately and let
        # the parent reload.
        self.dismiss(True)

    def _collect_payload(self, schema: dict[str, Any]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for key, spec in schema.items():
            spec = spec or {}
            kind = spec.get("type", "string")
            widget = self._widgets.get(key)
            if widget is None:
                continue
            out[key] = self._read_widget(key, kind, spec, widget)
        return out

    def _read_widget(
        self, key: str, kind: str, spec: dict[str, Any], widget: Any
    ) -> Any:
        if kind == "bool":
            return bool(widget.value)
        if kind == "enum":
            v = widget.value
            return None if v is Select.NULL else v
        if kind == "list":
            text = widget.text or ""
            return [s.strip() for s in text.split("\n") if s.strip()]
        if kind == "dict":
            text = (widget.text or "").strip()
            if not text:
                return None
            try:
                return json.loads(text)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{key}: invalid JSON ({exc})") from exc
        if kind == "int":
            text = (widget.value or "").strip()
            if not text:
                return None
            try:
                return int(text)
            except ValueError as exc:
                raise ValueError(f"{key}: not an integer") from exc
        if kind == "float":
            text = (widget.value or "").strip()
            if not text:
                return None
            try:
                return float(text)
            except ValueError as exc:
                raise ValueError(f"{key}: not a number") from exc
        # default: string
        v = widget.value
        return v if v != "" else None

    def _set_status(self, text: str) -> None:
        self._status_text = text
        try:
            self.query_one("#edit-status", Static).update(text)
        except Exception:
            pass


def _safe(key: str) -> str:
    """Make a schema key safe for use as a Textual widget id."""
    return "".join(ch if ch.isalnum() else "_" for ch in key)
