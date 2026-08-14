"""Inspect and mutate live plugin and tool configuration.

The inventory normalizes both module kinds into one schema-driven UI; writes go
through the agent's option helpers so validation and prompt refresh stay centralized.
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

from kohakuterrarium.core.agent_tool_options import agent_tool_inventory
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


def _list_modules(agent: Any) -> list[dict[str, Any]]:
    """Normalize configurable modules for the UI."""
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
    for entry in agent_tool_inventory(agent):
        out.append(
            {
                "type": "tool",
                "name": entry["name"],
                "description": entry.get("description", ""),
                "schema": entry.get("option_schema", {}),
                "options": entry.get("values", {}),
                "enabled": None,
                "priority": None,
            }
        )
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
    if m["type"] == "tool":
        helper = getattr(agent, "tool_options", None)
        if helper is None:
            raise RuntimeError("agent has no tool_options helper")
        return helper.set(m["name"], values)
    raise ValueError(f"Unsupported module type: {m['type']!r}")


def _sort_key(m: dict[str, Any]) -> tuple[int, str]:
    p = m.get("priority")
    return (50 if p is None else int(p), m["name"])


class ModulesModal(ModalScreen[None]):
    """Browse live module inventory and toggle plugin enablement."""

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
        # Per-type row indexes keep highlighted-row lookup stable after filtering.
        self._row_index: dict[str, list[dict[str, Any]]] = {}
        self._search: str = ""

    def compose(self):
        with Vertical(id="modules-container"):
            with Horizontal(id="modules-search-row"):
                yield Input(placeholder="Filter modules…", id="modules-search")
            with TabbedContent(id="modules-tabs"):
                with TabPane("Plugins", id="tab-plugin"):
                    yield DataTable(id="table-plugin", cursor_type="row")
                with TabPane("Native tools", id="tab-native_tool"):
                    yield DataTable(id="table-native_tool", cursor_type="row")
                with TabPane("Tools", id="tab-tool"):
                    yield DataTable(id="table-tool", cursor_type="row")
            yield Static(
                "enter:edit  t:toggle  /:search  r:reload  esc:close",
                classes="hint",
            )

    def on_mount(self) -> None:
        self.query_one("#modules-container", Vertical).border_title = "Modules"
        for tid in ("plugin", "native_tool", "tool"):
            tbl = self.query_one(f"#table-{tid}", DataTable)
            tbl.add_columns("●", "name", "p", "opts")
        self.reload_modules()

    def reload_modules(self) -> None:
        try:
            self._modules = _list_modules(self._agent)
        except Exception as exc:  # pragma: no cover - defensive boundary
            logger.warning("reload_modules failed", error=str(exc))
            self._modules = []
        self._populate_tables()

    def _populate_tables(self) -> None:
        q = self._search.strip().lower()
        for tid in ("plugin", "native_tool", "tool"):
            tbl = self.query_one(f"#table-{tid}", DataTable)
            tbl.clear()
            self._row_index[tid] = []

        plugins = [m for m in self._modules if m["type"] == "plugin"]
        natives = [m for m in self._modules if m["type"] == "native_tool"]
        tools = [m for m in self._modules if m["type"] == "tool"]

        # Enabled plugins precede disabled plugins; priority orders each group.
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
        for m in sorted(tools, key=_sort_key):
            if not _matches(m, q):
                continue
            self._add_row("tool", m)

        tabs = self.query_one("#modules-tabs", TabbedContent)
        tabs.get_tab("tab-plugin").label = f"Plugins ({len(self._row_index['plugin'])})"
        tabs.get_tab("tab-native_tool").label = (
            f"Native tools ({len(self._row_index['native_tool'])})"
        )
        tabs.get_tab("tab-tool").label = f"Tools ({len(self._row_index['tool'])})"

    def _add_row(self, tid: str, m: dict[str, Any]) -> None:
        tbl = self.query_one(f"#table-{tid}", DataTable)
        glyph = "●" if m["enabled"] is True else "○" if m["enabled"] is False else "-"
        pr = m.get("priority")
        pr_text = f"p{pr}" if pr is not None else ""
        n_opts = len(m.get("schema") or {})
        opts_text = f"{n_opts}" if n_opts else ""
        tbl.add_row(glyph, m["name"], pr_text, opts_text)
        self._row_index[tid].append(m)

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
        # Reload after scheduling pending plugin initialization.
        self.reload_modules()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "modules-search":
            self._search = event.value
            self._populate_tables()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        # The active table's cursor is more portable than event.cursor_row.
        self.action_open_selected()

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


class ModuleEditModal(ModalScreen[bool]):
    """Edit schema-defined options through the agent's runtime option helper."""

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
        # The snapshot supports change-only writes and stable no-change detection.
        self._initial = dict(module.get("options") or {})
        # Widget references preserve schema keys when generated IDs are sanitized.
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
        """Compose the label and editor for one schema entry."""
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
            for value, reason in (spec.get("disabled_values") or {}).items():
                yield Label(f"[yellow]{value} unavailable:[/yellow] {reason}")
            widget = self._make_widget(key, kind, spec, current)
            self._widgets[key] = widget
            yield widget

    def _make_widget(self, key: str, kind: str, spec: dict[str, Any], current: Any):
        if kind == "bool":
            return Switch(value=bool(current), id=f"f-{_safe(key)}")
        if kind == "enum":
            disabled = set((spec.get("disabled_values") or {}).keys())
            values = [str(v) for v in (spec.get("values") or [])]
            opts = [(value, value) for value in values if value not in disabled]
            allowed = set(values) - disabled
            initial: Any
            if current is not None and str(current) in allowed:
                initial = str(current)
            else:
                # Select.NULL is the valid sentinel for no selection.
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
        return Input(
            value="" if current is None else str(current),
            id=f"f-{_safe(key)}",
            placeholder=str(spec.get("default") or ""),
        )

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
        # Persist only values that differ from the opening snapshot.
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
        # Dismissal lets the parent reload the updated module immediately.
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
        v = widget.value
        return v if v != "" else None

    def _set_status(self, text: str) -> None:
        self._status_text = text
        try:
            self.query_one("#edit-status", Static).update(text)
        except Exception:
            # Status updates may occur while the modal is being dismissed.
            pass


def _safe(key: str) -> str:
    """Convert a schema key to a Textual-safe widget ID."""
    return "".join(ch if ch.isalnum() else "_" for ch in key)
