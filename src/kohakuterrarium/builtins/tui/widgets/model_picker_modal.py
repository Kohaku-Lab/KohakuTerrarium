"""Textual model-picker modal with variation-group support.

Mirrors the Vue ``ModelSwitcher.vue`` UX: provider tabs at the top,
a list of models per provider, and dropdowns for any variation groups
the highlighted model exposes (``effort=high``, ``tier=large``, …).
Apply assembles the canonical ``provider/name[@var1=opt1,var2=opt2]``
identifier and calls :meth:`Agent.switch_model`.

Pushed by:

* ``AgentTUI.action_open_model_picker`` (F3 keybinding).
* The ``/model`` (no-args) slash-command intercept in
  :class:`TUIInput`.
"""

from typing import Any

from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import (
    Button,
    DataTable,
    Label,
    Select,
    Static,
    TabbedContent,
    TabPane,
)

from kohakuterrarium.llm.profiles import list_all
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


# ── Helpers ──────────────────────────────────────────────────────


def _identifier(entry: dict[str, Any]) -> str:
    """Return ``provider/name`` (or just name when no provider)."""
    provider = entry.get("provider") or entry.get("login_provider") or ""
    return f"{provider}/{entry['name']}" if provider else entry["name"]


def _strip_variation(ident: str) -> str:
    return ident.split("@", 1)[0]


def _parse_variation_suffix(ident: str) -> dict[str, str]:
    """Extract ``{group: option, …}`` from a ``…@a=x,b=y`` identifier."""
    if "@" not in ident:
        return {}
    _, _, suffix = ident.partition("@")
    out: dict[str, str] = {}
    for part in suffix.split(","):
        part = part.strip()
        if "=" in part:
            k, _, v = part.partition("=")
            if k and v:
                out[k.strip()] = v.strip()
    return out


def _format_identifier(base: str, variations: dict[str, str]) -> str:
    if not variations:
        return base
    suffix = ",".join(f"{k}={v}" for k, v in variations.items() if v)
    return f"{base}@{suffix}" if suffix else base


# ── Picker modal ─────────────────────────────────────────────────


class ModelPickerModal(ModalScreen[bool]):
    """Pick a model + variation options. Returns ``True`` if applied."""

    DEFAULT_CSS = """
    ModelPickerModal {
        align: center middle;
    }
    #picker-container {
        width: 96;
        height: 36;
        border: thick #5A4FCF 60%;
        border-title-color: #5A4FCF;
        border-title-align: left;
        background: $surface;
        padding: 1 1 0 1;
    }
    #picker-tabs {
        height: 1fr;
    }
    ModelPickerModal DataTable {
        height: 1fr;
    }
    #variations-pane {
        height: auto;
        min-height: 3;
        max-height: 8;
        border-top: solid $surface-darken-1;
        padding: 1 0;
        margin-top: 0;
    }
    .variation-row {
        height: 3;
    }
    .variation-label {
        width: 16;
        color: $text-muted;
        padding: 1 0 0 1;
    }
    #picker-status {
        height: 1;
        color: $text-muted;
    }
    #picker-buttons {
        height: 3;
        align: right middle;
        margin-top: 0;
    }
    #picker-buttons Button {
        margin-left: 1;
    }
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=True),
        Binding("ctrl+s", "apply", "Apply", show=True),
    ]

    def __init__(self, agent: Any) -> None:
        super().__init__()
        self._agent = agent
        self._entries: list[dict[str, Any]] = []
        self._by_provider: dict[str, list[dict[str, Any]]] = {}
        # Per-tab row index → entry dict (for cursor-row → model lookup).
        self._row_index: dict[str, list[dict[str, Any]]] = {}
        # Tab-id ↔ provider name map. ``_safe_id`` is lossy (slashes,
        # spaces become ``_``) so we store the real provider string
        # keyed by the safe id used as the Textual TabPane id.
        self._tab_to_provider: dict[str, str] = {}
        self._selected_variations: dict[str, str] = {}
        self._current_identifier = ""

    def compose(self):
        with Vertical(id="picker-container"):
            with TabbedContent(id="picker-tabs"):
                # Tabs are populated dynamically in on_mount.
                yield TabPane("loading…", id="tab-_init")
            with VerticalScroll(id="variations-pane"):
                yield Static(
                    "[dim]Highlight a model above to see its variations.[/dim]",
                    id="variations-empty",
                )
            yield Static("", id="picker-status")
            with Horizontal(id="picker-buttons"):
                yield Button("Cancel", id="btn-cancel", variant="default")
                yield Button("Apply", id="btn-apply", variant="primary")

    async def on_mount(self) -> None:
        self.query_one("#picker-container", Vertical).border_title = "Model picker"
        self._current_identifier = self._read_current_identifier()
        self._selected_variations = _parse_variation_suffix(self._current_identifier)
        self._entries = self._load_entries()
        self._populate_tabs()

    # Data ──────────────────────────────────────────────────────────

    def _read_current_identifier(self) -> str:
        get_ident = getattr(self._agent, "llm_identifier", None)
        if callable(get_ident):
            try:
                return get_ident() or ""
            except Exception:
                return ""
        llm = getattr(self._agent, "llm", None)
        return getattr(llm, "model", "") if llm else ""

    def _load_entries(self) -> list[dict[str, Any]]:
        try:
            return [e for e in list_all() if e.get("available")]
        except Exception as exc:  # pragma: no cover — defensive
            logger.warning("model picker list_all failed", error=str(exc))
            return []

    def _populate_tabs(self) -> None:
        # Group by provider (login_provider preferred).
        groups: dict[str, list[dict[str, Any]]] = {}
        for e in self._entries:
            prov = e.get("login_provider") or e.get("provider") or "—"
            groups.setdefault(prov, []).append(e)
        self._by_provider = groups

        tabs = self.query_one("#picker-tabs", TabbedContent)
        # Remove the placeholder tab and add real ones.
        try:
            tabs.remove_pane("tab-_init")
        except Exception:
            pass

        # Stable order: provider of the current model first, then
        # alphabetical.
        current_base = _strip_variation(self._current_identifier)
        current_provider = ""
        if "/" in current_base:
            current_provider, _, _ = current_base.partition("/")
        order: list[str] = []
        if current_provider and current_provider in groups:
            order.append(current_provider)
        order.extend(sorted(p for p in groups if p != current_provider))

        active_pane = ""
        cursor_targets: list[tuple[Any, int]] = []
        for prov in order:
            safe = _safe_id(prov)
            pane_id = f"tab-{safe}"
            self._tab_to_provider[pane_id] = prov
            tbl = DataTable(id=f"table-{safe}", cursor_type="row")
            pane = TabPane(prov, tbl, id=pane_id)
            tabs.add_pane(pane)
            tbl.add_columns("●", "name", "context", "variations")
            self._row_index[prov] = []
            target_row = 0
            for i, e in enumerate(groups[prov]):
                ident = _identifier(e)
                marker = (
                    "●" if _strip_variation(self._current_identifier) == ident else ""
                )
                ctx = (
                    f"{(e.get('max_context') or 0) // 1000}k"
                    if e.get("max_context")
                    else ""
                )
                var_count = len(e.get("variation_groups") or {})
                var_text = f"{var_count} var" if var_count else ""
                tbl.add_row(marker, ident, ctx, var_text)
                self._row_index[prov].append(e)
                if marker:
                    target_row = i
                    active_pane = pane_id
            if target_row:
                cursor_targets.append((tbl, target_row))

        if active_pane:
            tabs.active = active_pane

        # Position cursors after the panes finish mounting; calling
        # ``move_cursor`` synchronously here can race with the
        # initial layout and end up no-op'd on some Textual versions.
        def _seat_cursors() -> None:
            for tbl, row in cursor_targets:
                try:
                    tbl.move_cursor(row=row)
                except Exception:
                    pass

        self.call_after_refresh(_seat_cursors)
        # Update variations pane for whatever's currently highlighted.
        self.call_after_refresh(self._refresh_variations)

    # Events ───────────────────────────────────────────────────────

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        # When the user moves the cursor between rows, refresh the
        # variation widgets to match that model's groups.
        self._refresh_variations()

    def on_tabbed_content_tab_activated(
        self, event: TabbedContent.TabActivated
    ) -> None:
        self._refresh_variations()

    def on_select_changed(self, event: Select.Changed) -> None:
        # Variation dropdown changed.
        wid = event.select.id or ""
        if wid.startswith("var-"):
            group = wid[len("var-") :]
            value = event.value
            if value is Select.NULL:
                self._selected_variations.pop(group, None)
            else:
                self._selected_variations[group] = str(value)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-cancel":
            self.dismiss(False)
        elif event.button.id == "btn-apply":
            self._apply()

    def action_cancel(self) -> None:
        self.dismiss(False)

    def action_apply(self) -> None:
        self._apply()

    # Variation widgets ────────────────────────────────────────────

    def _highlighted_entry(self) -> dict[str, Any] | None:
        tabs = self.query_one("#picker-tabs", TabbedContent)
        active = tabs.active
        if not active:
            return None
        prov = self._tab_to_provider.get(active, "")
        rows = self._row_index.get(prov, [])
        if not rows:
            return None
        try:
            tbl = self.query_one(f"#table-{_safe_id(prov)}", DataTable)
        except Exception:
            return None
        idx = tbl.cursor_row
        if 0 <= idx < len(rows):
            return rows[idx]
        return None

    def _refresh_variations(self) -> None:
        """Rebuild the variation-pane content for the highlighted model."""
        try:
            pane = self.query_one("#variations-pane", VerticalScroll)
        except Exception:
            return
        pane.remove_children()
        entry = self._highlighted_entry()
        if not entry:
            pane.mount(Static("[dim]Highlight a model above.[/dim]"))
            return
        groups = entry.get("variation_groups") or {}
        if not groups:
            pane.mount(Static("[dim]This model has no variation options.[/dim]"))
            return
        ident_base = _identifier(entry)
        # If the user just highlighted a different model, drop any
        # variation choices that don't apply to this model's groups.
        prev = self._selected_variations
        applicable: dict[str, str] = {}
        if _strip_variation(self._current_identifier) == ident_base:
            applicable = dict(prev)
        else:
            for k, v in prev.items():
                if k in groups and v in (groups.get(k) or {}):
                    applicable[k] = v
        self._selected_variations = applicable

        for group_name in sorted(groups):
            options_dict = groups[group_name] or {}
            options = sorted(options_dict.keys())
            current = applicable.get(group_name) or ""
            row = Horizontal(classes="variation-row")
            label = Label(group_name, classes="variation-label")
            select = Select(
                options=[(opt, opt) for opt in options],
                value=current if current in options else Select.NULL,
                allow_blank=True,
                id=f"var-{group_name}",
            )
            pane.mount(row)
            row.mount(label)
            row.mount(select)

    # Apply ────────────────────────────────────────────────────────

    def _apply(self) -> None:
        entry = self._highlighted_entry()
        if not entry:
            self._set_status("[red]No model selected[/red]")
            return
        ident = _format_identifier(_identifier(entry), self._selected_variations)
        switch = getattr(self._agent, "switch_model", None)
        if not callable(switch):
            self._set_status("[red]Agent does not support model switching[/red]")
            return
        try:
            applied = switch(ident)
        except ValueError as exc:
            self._set_status(f"[red]{exc}[/red]")
            return
        self._set_status(f"[green]Switched to {applied}[/green]")
        self.dismiss(True)

    def _set_status(self, text: str) -> None:
        try:
            self.query_one("#picker-status", Static).update(text)
        except Exception:
            pass


# ── Tab id helpers ──────────────────────────────────────────────


def _safe_id(name: str) -> str:
    """Map an arbitrary provider name to a Textual-safe widget id."""
    return "".join(ch if ch.isalnum() else "_" for ch in name) or "_"
