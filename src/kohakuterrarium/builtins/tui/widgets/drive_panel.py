"""Textual Drive record panel — live list/detail/actions over the service.

``DriveScreen`` is a modal screen reachable from the F4 key binding and the
command palette. It shows a Records tab (list + detail + capability-filtered
actions, live-refreshing from Drive engine events) and a Settings tab (runtime +
registration config). It talks only to a :class:`TerrariumService` built from the
engine — never engine private dicts — so local and future remote behaviour match
(design §12.4). Every action is filtered by the server ``allowed_actions`` and
re-authorised server-side, so a hidden action stays safe if reached.

The data-loading + projection helpers (:func:`load_drive_rows`,
:func:`detail_lines`) are pure/async module functions so they are unit-testable
against a scripted service without mounting the Textual app.
"""

from functools import partial
from typing import Any

from rich.markup import escape
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import (
    Button,
    DataTable,
    Input,
    Label,
    Static,
    TabbedContent,
    TabPane,
)

from kohakuterrarium.builtins.cli_rich.dialogs.drive_format import (
    TERMINAL_STATUSES,
    enabled_actions,
    next_wake,
    owner_assignee,
    project_view,
    status_label,
    warning_badges,
)
from kohakuterrarium.builtins.tui.widgets.drive_settings_pane import DriveSettingsPane
from kohakuterrarium.builtins.tui.widgets.modals import ConfirmModal
from kohakuterrarium.terrarium.drive.errors import DriveError
from kohakuterrarium.terrarium.drive.models import ActorRef, DriveStatus
from kohakuterrarium.terrarium.events import EventFilter, EventKind
from kohakuterrarium.utils.logging import get_logger

_DRIVE_EVENT_KINDS = frozenset(
    {
        EventKind.DRIVE_CREATED,
        EventKind.DRIVE_UPDATED,
        EventKind.DRIVE_ASSIGNED,
        EventKind.DRIVE_UNASSIGNED,
        EventKind.DRIVE_STATUS_CHANGED,
        EventKind.DRIVE_DELIVERY_ADMITTED,
        EventKind.DRIVE_DELIVERY_ACKNOWLEDGED,
        EventKind.DRIVE_DELIVERY_RETRYING,
        EventKind.DRIVE_DELIVERY_DEAD_LETTERED,
        EventKind.DRIVE_ORPHANED,
        EventKind.DRIVE_RETIRED,
    }
)

logger = get_logger(__name__)


SCOPES: list[str] = ["mine", "graph"]
STATUS_FILTERS: list[tuple[str, frozenset[str] | None]] = [
    ("all", None),
    ("active", frozenset({"active", "waiting"})),
    ("attention", frozenset({"blocked", "paused"})),
    ("terminal", frozenset(TERMINAL_STATUSES)),
]


async def load_drive_rows(
    service: Any,
    actor: ActorRef,
    creature_id: str,
    *,
    scope: str,
    statuses: frozenset[str] | None,
    is_operator: bool,
) -> tuple[list[Any], list[dict[str, Any]]]:
    """Load Drive views for the scope/filter, returning (views, projected rows).

    ``mine`` scopes to the focused creature's assignments; ``graph`` lists the
    whole graph. Ordering is non-terminal first, higher priority, then id.
    """
    graph_id = None
    assignee = None
    if scope == "graph":
        info = await service.get_creature_info(creature_id) if creature_id else None
        graph_id = getattr(info, "graph_id", None) if info is not None else None
    else:
        assignee = creature_id or None
    drive_statuses = frozenset(DriveStatus(s) for s in statuses) if statuses else None
    views = await service.list_drives(
        actor=actor,
        graph_id=graph_id,
        assignee_creature_id=assignee,
        statuses=drive_statuses,
        include_terminal=True,
        is_privileged=is_operator,
    )
    ordered = sorted(views, key=_sort_key)
    return ordered, [project_view(v) for v in ordered]


def detail_lines(row: dict[str, Any], progress: list[Any]) -> list[str]:
    """Textual-markup lines for the detail pane of one Drive (pure).

    Every interpolated field is user/actor-controlled, so it is ``escape``-d;
    only the structural ``[b]`` tag is real markup. This keeps a title such as
    ``Fix [urgent] bug`` from raising a Textual markup error.
    """
    lines = [
        f"[b]{escape(str(row['title']))}[/b]",
        f"id: {escape(str(row['drive_id']))}",
        f"kind: {escape(str(row['kind']))}  rev {row['revision']}",
        f"status: {escape(status_label(row['status']))}",
    ]
    if row.get("status_reason"):
        lines.append(f"reason: {escape(str(row['status_reason']))}")
    lines.append(f"owner → assignee: {escape(owner_assignee(row))}")
    lines.append(
        f"scope: {escape(str(row.get('scope_type', '?')))}   "
        f"priority: {row.get('priority', 0)}"
    )
    lines.append(f"durability: {escape(str(row.get('durability', 'unknown')))}")
    badges = warning_badges(row)
    if badges:
        lines.append("warnings: " + ", ".join(escape(t) for t, _ in badges))
    wake = next_wake(row)
    if wake:
        lines.append(f"next wake: {escape(wake)}")
    if progress:
        lines.append("")
        lines.append("progress:")
        for p in progress[-4:]:
            lines.append(f"  • {escape(str(getattr(p, 'summary', ''))[:70])}")
    return lines


def _sort_key(view: Any) -> tuple[int, int, str]:
    record = view.record
    terminal = 1 if record.status.value in TERMINAL_STATUSES else 0
    return (terminal, -record.priority, record.drive_id)


class DriveProgressModal(ModalScreen[str | None]):
    """Small text prompt for a progress note."""

    DEFAULT_CSS = """
    DriveProgressModal { align: center middle; }
    #drive-progress-box {
        width: 70; height: auto; padding: 1 2;
        border: thick #5A4FCF 60%; background: $surface;
    }
    """

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def compose(self) -> ComposeResult:
        with Vertical(id="drive-progress-box"):
            yield Label("Progress note")
            yield Input(placeholder="what happened…", id="drive-progress-input")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        text = (event.value or "").strip()
        self.dismiss(text or None)

    def action_cancel(self) -> None:
        self.dismiss(None)


class DriveScreen(ModalScreen[None]):
    """Live Drive record panel (Records + Settings tabs)."""

    DEFAULT_CSS = """
    DriveScreen { align: center middle; }
    #drive-container {
        width: 96; height: 36;
        border: thick #5A4FCF 60%; border-title-color: #5A4FCF;
        border-title-align: left; background: $surface; padding: 1 1 0 1;
    }
    #drive-status-line { height: 1; color: $text-muted; }
    #drive-split { height: 1fr; }
    #drive-list { width: 2fr; }
    #drive-detail-pane { width: 3fr; padding: 0 1; }
    #drive-detail { height: 1fr; }
    #drive-actions { height: auto; }
    #drive-actions Button { margin: 0 1 0 0; }
    DriveScreen .drive-field { height: 3; }
    DriveScreen .drive-field Label { width: 30; content-align: left middle; }
    DriveScreen .drive-actions { height: 3; margin-top: 1; }
    DriveScreen .drive-error { color: $error; }
    .drive-hint { height: 1; color: $text-muted; text-align: center; }
    """

    BINDINGS = [
        Binding("escape", "close", "Close", show=True),
        Binding("f5", "reload", "Reload", show=True),
        Binding("f", "cycle_filter", "Filter", show=True),
        Binding("v", "cycle_scope", "Scope", show=True),
    ]

    def __init__(
        self,
        service: Any,
        engine: Any,
        *,
        creature_id: str,
        actor: ActorRef | None = None,
        is_operator: bool = True,
    ) -> None:
        super().__init__()
        # ``service`` (a TerrariumService) and ``engine`` are injected by the
        # runner (engine_cli) and consumed as duck-typed handles — the same
        # context-provided-service pattern the /goal command uses — so this
        # module never imports terrarium.service.
        self._engine = engine
        self._service = service
        self._creature_id = creature_id
        self._actor = actor or ActorRef("user", "local")
        self._is_operator = is_operator
        self._scope = "mine"
        self._filter_idx = 0
        self._views: list[Any] = []
        self._rows: list[dict[str, Any]] = []
        self._selected = 0

    def compose(self) -> ComposeResult:
        with Vertical(id="drive-container"):
            with TabbedContent(id="drive-tabs"):
                with TabPane("Records", id="drive-records"):
                    yield Static("", id="drive-status-line")
                    with Horizontal(id="drive-split"):
                        yield DataTable(id="drive-list", cursor_type="row")
                        with VerticalScroll(id="drive-detail-pane"):
                            yield Static("Select a drive.", id="drive-detail")
                            yield Horizontal(id="drive-actions")
                with TabPane("Settings", id="drive-settings"):
                    yield DriveSettingsPane(get_engine=lambda: self._engine)
            yield Static(
                "↑↓ select · f filter · v scope · f5 reload · esc close",
                classes="drive-hint",
            )

    def on_mount(self) -> None:
        self.query_one("#drive-container", Vertical).border_title = "Drives"
        table = self.query_one("#drive-list", DataTable)
        table.add_columns("st", "title", "owner→assignee")
        if self._service is None:
            self.query_one("#drive-status-line", Static).update(
                "[yellow]Drive runtime is not available in this context.[/yellow]"
            )
            return
        self.run_worker(self._reload, exclusive=True, group="drive-reload")
        self.run_worker(self._watch_events, exclusive=True, group="drive-watch")

    # ── Data ────────────────────────────────────────────────────

    async def _reload(self) -> None:
        if self._service is None:
            return
        _, statuses = STATUS_FILTERS[self._filter_idx]
        try:
            views, rows = await load_drive_rows(
                self._service,
                self._actor,
                self._creature_id,
                scope=self._scope,
                statuses=statuses,
                is_operator=self._is_operator,
            )
        except DriveError as exc:
            self.query_one("#drive-status-line", Static).update(
                f"[red]{escape(str(exc))}[/red]"
            )
            return
        self._views, self._rows = views, rows
        await self._populate_table()

    async def _populate_table(self) -> None:
        table = self.query_one("#drive-list", DataTable)
        table.clear()
        for row in self._rows:
            badges = "".join(f" ({t})" for t, _ in warning_badges(row))
            # Text() cells so user-controlled titles/owners are never parsed as
            # Textual markup (a title with '[' would otherwise raise).
            table.add_row(
                Text(status_label(row["status"])),
                Text(row["title"][:28] + badges),
                Text(owner_assignee(row)),
            )
        self._update_status_line()
        if self._rows:
            self._selected = min(self._selected, len(self._rows) - 1)
            # Single exclusive worker serializes detail loads (and the action
            # re-mount) so rapid reloads / highlight events never overlap.
            self._show_detail(self._selected)
        else:
            self.query_one("#drive-detail", Static).update("(no drives)")
            await self._clear_actions()

    def _update_status_line(self) -> None:
        active = sum(1 for r in self._rows if r["status"] in ("active", "waiting"))
        blocked = sum(1 for r in self._rows if r["status"] == "blocked")
        label = STATUS_FILTERS[self._filter_idx][0]
        text = (
            f"scope: {'assigned to me' if self._scope == 'mine' else 'whole graph'}"
            f"   filter: {label}   {len(self._rows)} shown · {active} active · {blocked} blocked"
        )
        self.query_one("#drive-status-line", Static).update(text)

    def _show_detail(self, index: int) -> None:
        if not (0 <= index < len(self._views)):
            return
        self._selected = index
        row = self._rows[index]
        self.run_worker(
            partial(self._load_detail, row["drive_id"]),
            exclusive=True,
            group="drive-detail",
        )

    async def _load_detail(self, drive_id: str) -> None:
        if self._service is None:
            return
        try:
            view = await self._service.get_drive(
                drive_id, actor=self._actor, is_privileged=self._is_operator
            )
            progress = list(await self._service.list_drive_progress(drive_id))
        except DriveError as exc:
            self.query_one("#drive-detail", Static).update(
                f"[red]{escape(str(exc))}[/red]"
            )
            return
        if view is None:
            self.query_one("#drive-detail", Static).update("(drive no longer exists)")
            await self._clear_actions()
            return
        row = project_view(view)
        self._views[self._selected] = view
        self._rows[self._selected] = row
        self.query_one("#drive-detail", Static).update(
            "\n".join(detail_lines(row, progress))
        )
        await self._render_actions(row)

    # ── Actions ─────────────────────────────────────────────────

    async def _clear_actions(self) -> None:
        # ``remove_children`` is async in Textual; awaiting it before the next
        # mount is what prevents a DuplicateIds race across rapid reloads.
        await self.query_one("#drive-actions", Horizontal).remove_children()

    async def _render_actions(self, row: dict[str, Any]) -> None:
        actions = self.query_one("#drive-actions", Horizontal)
        await actions.remove_children()
        for action in enabled_actions(row):
            # ``name`` (not ``id``): action buttons are rebuilt on every detail
            # reload, and ids must be unique — name carries the action instead.
            await actions.mount(
                Button(action["label"], name=action["id"], variant="default")
            )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        action_id = event.button.name or ""
        if action_id:
            self._trigger_action(action_id)

    def _trigger_action(self, action_id: str) -> None:
        if not (0 <= self._selected < len(self._views)):
            return
        record = self._views[self._selected].record
        if action_id == "progress":
            self.app.push_screen(DriveProgressModal(), self._on_progress_submitted)
            return

        def _on_confirm(ok: bool) -> None:
            if ok:
                self.run_worker(
                    self._apply_action(action_id, record.drive_id, record.revision),
                    exclusive=False,
                )

        self.app.push_screen(
            ConfirmModal(f"{action_id} drive {record.drive_id}?"), _on_confirm
        )

    def _on_progress_submitted(self, text: str | None) -> None:
        if not text or not (0 <= self._selected < len(self._views)):
            return
        record = self._views[self._selected].record
        self.run_worker(self._report_progress(record.drive_id, text), exclusive=False)

    async def _apply_action(self, action_id: str, drive_id: str, revision: int) -> None:
        if self._service is None:
            return
        targets = {
            "pause": DriveStatus.PAUSED,
            "resume": DriveStatus.ACTIVE,
            "cancel": DriveStatus.CANCELLED,
        }
        try:
            if action_id == "wake":
                await self._service.wake_drive(
                    drive_id,
                    actor=self._actor,
                    expected_revision=revision,
                    is_privileged=self._is_operator,
                )
            else:
                await self._service.transition_drive(
                    drive_id,
                    targets[action_id],
                    expected_revision=revision,
                    actor=self._actor,
                    is_privileged=self._is_operator,
                )
            await self._reload()
        except DriveError as exc:
            self.query_one("#drive-status-line", Static).update(
                f"[red]{escape(str(exc))}[/red]"
            )

    async def _report_progress(self, drive_id: str, summary: str) -> None:
        if self._service is None:
            return
        try:
            await self._service.report_drive_progress(
                drive_id,
                summary=summary,
                evidence=None,
                actor=self._actor,
                is_privileged=self._is_operator,
            )
            await self._reload()
        except DriveError as exc:
            self.query_one("#drive-status-line", Static).update(
                f"[red]{escape(str(exc))}[/red]"
            )

    # ── Live refresh ────────────────────────────────────────────

    async def _watch_events(self) -> None:
        try:
            async for _ev in self._engine.subscribe(
                EventFilter(kinds=set(_DRIVE_EVENT_KINDS))
            ):
                await self._reload()
        except Exception as exc:  # pragma: no cover — cancelled on close
            logger.debug("drive panel watch ended", error=str(exc))

    # ── Events + bindings ───────────────────────────────────────

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        self._show_detail(event.cursor_row)

    def action_close(self) -> None:
        self.dismiss(None)

    def action_reload(self) -> None:
        if self._service is not None:
            self.run_worker(self._reload, exclusive=True, group="drive-reload")

    def action_cycle_filter(self) -> None:
        self._filter_idx = (self._filter_idx + 1) % len(STATUS_FILTERS)
        self._selected = 0
        if self._service is not None:
            self.run_worker(self._reload, exclusive=True, group="drive-reload")

    def action_cycle_scope(self) -> None:
        idx = SCOPES.index(self._scope) if self._scope in SCOPES else 0
        self._scope = SCOPES[(idx + 1) % len(SCOPES)]
        self._selected = 0
        if self._service is not None:
            self.run_worker(self._reload, exclusive=True, group="drive-reload")


__all__ = ["DriveScreen", "DriveProgressModal", "detail_lines", "load_drive_rows"]
