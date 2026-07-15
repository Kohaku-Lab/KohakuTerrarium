"""Drive record overlay — live list/detail/actions for the rich CLI.

Bare ``/drives`` opens this overlay (subcommands stay scriptable and route to
the generic ``/drives`` user command for text output). It mirrors the
``SettingsOverlay`` contract: the ``RichCLIApp`` owns one instance, ``open()``
flips ``visible`` True, the app routes the status area through ``render(width)``,
and key events arrive via ``handle_key`` (named keys) / ``handle_text``
(printable chars) while the overlay captures input.

The overlay talks only to a :class:`TerrariumService` (built from the engine by
the app), never to engine private dicts, so local and future remote behaviour
match (design §12.4/§12.5). Actions are filtered by the server-provided
``allowed_actions`` **and** re-authorized server-side, so a hidden action is
still safe if reached. Live refresh is driven by the app feeding Drive engine
events into :meth:`note_event`.
"""

from typing import Any, Awaitable, Callable

from kohakuterrarium.builtins.cli_rich.dialogs.drive_format import (
    ACTIONS,
    TERMINAL_STATUSES,
    action_enabled,
    enabled_actions,
    project_view,
)
from kohakuterrarium.builtins.cli_rich.dialogs.drive_overlay_render import (
    render_drive_overlay,
)
from kohakuterrarium.terrarium.drive.errors import (
    DriveConflictError,
    DriveError,
    DriveIdempotencyConflictError,
    DriveNotFoundError,
    DrivePermissionError,
)
from kohakuterrarium.terrarium.drive.models import ActorRef, DriveStatus
from kohakuterrarium.terrarium.service import LocalTerrariumService
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

SCOPES: list[tuple[str, str]] = [
    ("mine", "assigned to me"),
    ("graph", "whole graph"),
]

# A None status set includes every status.
_STATUS_FILTERS: list[tuple[str, frozenset[str] | None]] = [
    ("all", None),
    ("active", frozenset({"active", "waiting"})),
    ("attention", frozenset({"blocked", "paused"})),
    ("terminal", frozenset(TERMINAL_STATUSES)),
]


class DriveOverlay:
    """Interactive list/detail/action overlay over the Drive service."""

    def __init__(
        self,
        *,
        get_engine: Callable[[], Any],
        get_creature_id: Callable[[], str],
        schedule: Callable[[Awaitable[Any]], Any],
        on_invalidate: Callable[[], None],
        get_principal: Callable[[], str | None] | None = None,
        is_operator: bool = True,
        on_close: Callable[[], None] | None = None,
    ) -> None:
        self._get_engine = get_engine
        self._get_creature_id = get_creature_id
        self._schedule = schedule
        self._on_invalidate = on_invalidate
        self._get_principal = get_principal or (lambda: None)
        self._is_operator = is_operator
        self._on_close = on_close or (lambda: None)

        self.visible = False
        self.mode = "list"  # list, detail, confirm, or progress
        self.scope = "mine"
        self._status_filter_idx = 0
        self._views: list[Any] = []
        self._rows: list[dict[str, Any]] = []
        self._cursor = 0
        self._detail_view: Any = None
        self._detail_row: dict[str, Any] | None = None
        self._detail_progress: list[Any] = []
        self._confirm: dict[str, Any] | None = None
        self._progress_text = ""
        self._flash = ""
        self._error = ""
        self._loading = False

    def open(self) -> None:
        self.visible = True
        self.mode = "list"
        self._cursor = 0
        self._detail_view = None
        self._detail_row = None
        self._confirm = None
        self._progress_text = ""
        self._flash = ""
        self._error = ""
        self._schedule(self.reload())

    def close(self) -> None:
        self.visible = False
        self.mode = "list"
        self._confirm = None
        self._on_close()

    def is_capturing_text(self) -> bool:
        """Return whether the visible modal owns printable input."""
        return self.visible

    def note_event(self) -> None:
        """Refresh an open overlay after a Drive engine event."""
        if not self.visible or self._loading:
            return
        self._schedule(self.reload())

    def _service(self) -> Any:
        engine = self._get_engine()
        if engine is None:
            return None
        return LocalTerrariumService(engine)

    def _actor(self) -> ActorRef:
        raw = self._get_principal()
        if isinstance(raw, ActorRef):
            return raw
        if isinstance(raw, str) and raw:
            try:
                return ActorRef.parse(raw)
            except DriveError:
                pass
        return ActorRef("user", "local")

    def _status_filter(self) -> frozenset[str] | None:
        return _STATUS_FILTERS[self._status_filter_idx][1]

    async def reload(self) -> None:
        service = self._service()
        if service is None:
            self._views = []
            self._rows = []
            self._error = "Drive runtime is not available in this context."
            self._on_invalidate()
            return
        self._loading = True
        try:
            actor = self._actor()
            graph_id = None
            assignee = None
            if self.scope == "graph":
                graph_id = await self._focused_graph(service)
            else:
                assignee = self._get_creature_id() or None
            statuses = self._status_filter()
            drive_statuses = (
                frozenset(DriveStatus(s) for s in statuses) if statuses else None
            )
            views = await service.list_drives(
                actor=actor,
                graph_id=graph_id,
                assignee_creature_id=assignee,
                statuses=drive_statuses,
                include_terminal=True,
                is_privileged=self._is_operator,
            )
            self._error = ""
            self._views = sorted(views, key=_sort_key)
            self._rows = [project_view(v) for v in self._views]
            if self._cursor >= len(self._rows):
                self._cursor = max(0, len(self._rows) - 1)
            if self.mode == "detail" and self._detail_view is not None:
                await self._refresh_detail(service, actor)
        except DriveError as exc:
            self._error = _error_text(exc)
        except Exception as exc:  # pragma: no cover - defensive service wiring
            logger.warning("drive overlay reload failed", error=str(exc))
            self._error = f"drive error: {exc}"
        finally:
            self._loading = False
            self._on_invalidate()

    async def _focused_graph(self, service: Any) -> str | None:
        cid = self._get_creature_id()
        if not cid:
            return None
        info = await service.get_creature_info(cid)
        return getattr(info, "graph_id", None) if info is not None else None

    async def load_detail(self, drive_id: str) -> None:
        service = self._service()
        if service is None:
            return
        actor = self._actor()
        try:
            await self._refresh_detail(service, actor, drive_id=drive_id)
        except DriveError as exc:
            self._error = _error_text(exc)
        self.mode = "detail" if self._detail_view is not None else "list"
        self._on_invalidate()

    async def _refresh_detail(
        self, service: Any, actor: ActorRef, *, drive_id: str | None = None
    ) -> None:
        if drive_id is None and self._detail_view is not None:
            drive_id = self._detail_view.record.drive_id
        if not drive_id:
            return
        view = await service.get_drive(
            drive_id, actor=actor, is_privileged=self._is_operator
        )
        if view is None:
            self._detail_view = None
            self._detail_row = None
            self._detail_progress = []
            self._error = "Drive no longer exists."
            return
        self._detail_view = view
        self._detail_row = project_view(view)
        try:
            self._detail_progress = list(
                await service.list_drive_progress(
                    view.record.drive_id, actor=actor, is_privileged=self._is_operator
                )
            )
        except DriveError:
            self._detail_progress = []

    def handle_key(self, key: str) -> bool:
        if not self.visible:
            return False
        if self.mode == "confirm":
            return self._confirm_key(key)
        if self.mode == "progress":
            return self._progress_key(key)
        if self.mode == "detail":
            return self._detail_key(key)
        return self._list_key(key)

    def handle_text(self, char: str) -> bool:
        if not self.visible or not char:
            return False
        if self.mode == "confirm":
            if char in ("y", "Y"):
                self._schedule(self._apply_confirmed())
                return True
            if char in ("n", "N"):
                self.mode = "detail" if self._detail_view else "list"
                self._confirm = None
                return True
            return True
        if self.mode == "progress":
            self._progress_text += char
            return True
        if self.mode == "detail":
            return self._detail_letter(char)
        return self._list_letter(char)

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
            self._cycle_scope()
            return True
        if key == "enter":
            row = self._current_row()
            if row is not None:
                self._schedule(self.load_detail(row["drive_id"]))
            return True
        return False

    def _list_letter(self, char: str) -> bool:
        if char == "s":
            self._cycle_status_filter()
        elif char == "r":
            self._schedule(self.reload())
        # Modal input must not leak into the composer behind the overlay.
        return True

    def _detail_key(self, key: str) -> bool:
        if key == "escape":
            self.mode = "list"
            self._detail_view = None
            self._detail_row = None
            self._error = ""
            self._on_invalidate()
            return True
        if key == "enter":
            return True
        return False

    def _detail_letter(self, char: str) -> bool:
        action = self._action_for_key(char)
        if action is None:
            return True
        if not self._action_enabled(action):
            self._flash = f"{action['label']}: not available for this Drive"
            return True
        if action["id"] == "progress":
            self.mode = "progress"
            self._progress_text = ""
            return True
        self._confirm = {"action": action}
        self.mode = "confirm"
        return True

    def _confirm_key(self, key: str) -> bool:
        if key == "escape":
            self.mode = "detail" if self._detail_view else "list"
            self._confirm = None
            return True
        if key == "enter":
            self._schedule(self._apply_confirmed())
            return True
        return True

    def _progress_key(self, key: str) -> bool:
        if key == "escape":
            self.mode = "detail"
            self._progress_text = ""
            return True
        if key in ("backspace", "c-h"):
            self._progress_text = self._progress_text[:-1]
            return True
        if key == "enter":
            if self._progress_text.strip():
                self._schedule(self._submit_progress())
            return True
        return True

    async def _apply_confirmed(self) -> None:
        confirm = self._confirm
        self._confirm = None
        self.mode = "detail" if self._detail_view else "list"
        if confirm is None or self._detail_view is None:
            return
        action = confirm["action"]
        service = self._service()
        if service is None:
            return
        actor = self._actor()
        record = self._detail_view.record
        try:
            if action["id"] == "wake":
                await service.wake_drive(
                    record.drive_id,
                    actor=actor,
                    expected_revision=record.revision,
                    is_privileged=self._is_operator,
                )
            else:
                await service.transition_drive(
                    record.drive_id,
                    DriveStatus(action["target"]),
                    expected_revision=record.revision,
                    actor=actor,
                    is_privileged=self._is_operator,
                )
            self._flash = f"{action['label']} applied to {record.drive_id}"
            await self.reload()
        except DriveError as exc:
            self._error = _error_text(exc)
            self._on_invalidate()

    async def _submit_progress(self) -> None:
        summary = self._progress_text.strip()
        self.mode = "detail"
        self._progress_text = ""
        if not summary or self._detail_view is None:
            return
        service = self._service()
        if service is None:
            return
        actor = self._actor()
        record = self._detail_view.record
        try:
            await service.report_drive_progress(
                record.drive_id,
                summary=summary,
                evidence=None,
                actor=actor,
                is_privileged=self._is_operator,
            )
            self._flash = "progress recorded"
            await self.reload()
        except DriveError as exc:
            self._error = _error_text(exc)
            self._on_invalidate()

    def _action_for_key(self, char: str) -> dict[str, Any] | None:
        for action in ACTIONS:
            if action["key"] == char:
                return action
        return None

    def _action_enabled(self, action: dict[str, Any]) -> bool:
        row = self._detail_row
        if row is None:
            return False
        return action_enabled(row, action)

    def enabled_actions(self) -> list[dict[str, Any]]:
        """Actions offered for the current detail Drive (capability-filtered)."""
        if self._detail_row is None:
            return []
        return enabled_actions(self._detail_row)

    def _current_row(self) -> dict[str, Any] | None:
        if not self._rows:
            return None
        idx = max(0, min(self._cursor, len(self._rows) - 1))
        return self._rows[idx]

    def _move(self, delta: int) -> None:
        if not self._rows:
            return
        self._cursor = max(0, min(len(self._rows) - 1, self._cursor + delta))
        self._flash = ""

    def _cycle_scope(self) -> None:
        names = [s[0] for s in SCOPES]
        idx = names.index(self.scope) if self.scope in names else 0
        self.scope = names[(idx + 1) % len(names)]
        self._flash = ""
        self._cursor = 0
        self._schedule(self.reload())

    def _cycle_status_filter(self) -> None:
        self._status_filter_idx = (self._status_filter_idx + 1) % len(_STATUS_FILTERS)
        self._cursor = 0
        self._schedule(self.reload())

    @property
    def status_filter_label(self) -> str:
        return _STATUS_FILTERS[self._status_filter_idx][0]

    def render(self, width: int) -> str:
        return render_drive_overlay(self, width)


def _sort_key(view: Any) -> tuple[int, int, str]:
    """Sort active Drives first, then by priority and stable identifier."""
    record = view.record
    terminal = 1 if record.status.value in TERMINAL_STATUSES else 0
    return (terminal, -record.priority, record.drive_id)


def _error_text(exc: DriveError) -> str:
    if isinstance(exc, (DriveConflictError, DriveIdempotencyConflictError)):
        return f"conflict: {exc}; refreshing"
    if isinstance(exc, DrivePermissionError):
        return f"permission denied: {exc}"
    if isinstance(exc, DriveNotFoundError):
        return f"not found: {exc}"
    return f"drive error: {exc}"


__all__ = ["DriveOverlay", "SCOPES"]
