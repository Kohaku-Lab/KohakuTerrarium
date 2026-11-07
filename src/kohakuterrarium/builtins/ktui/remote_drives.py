"""Drives of a session in a KT server, through its drive routes (``/api/sessions/{sid}/drives``,
as the web uses them), shaped like the local drive service's results so the side panel, the
Status page and goal creation work the same attached. :func:`drive_service` picks this or the
local service for the shell's engine.
"""

from datetime import datetime
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

from kohakuterrarium.terrarium.drive.goal import build_goal_spec
from kohakuterrarium.terrarium.service.service import LocalTerrariumService

if TYPE_CHECKING:
    from kohakuterrarium.builtins.ktui.remote import RemoteSession


def drive_service(engine: Any) -> Any:
    """The drive calls for ``engine``: a server's routes when attached, else the local ones."""
    remote = getattr(engine, "remote_drives", None)
    return remote if remote is not None else LocalTerrariumService(engine)


def _when(text: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(str(text)) if text else None
    except ValueError:
        return None


def _owner(text: str) -> SimpleNamespace:
    return SimpleNamespace(format=lambda: text)


def view_of(row: dict) -> SimpleNamespace:
    """A drive row from the server as a view (``record`` plus the assignment summary)."""
    record = SimpleNamespace(
        drive_id=str(row.get("drive_id", "")),
        kind=str(row.get("kind", "")),
        revision=int(row.get("revision", 0) or 0),
        title=str(row.get("title", "")),
        status=SimpleNamespace(value=str(row.get("status", ""))),
        status_reason=row.get("status_reason"),
        priority=int(row.get("priority", 0) or 0),
        scope_type=str(row.get("scope_type", "")),
        scope_id=str(row.get("scope_id", "") or ""),
        owner=_owner(str(row.get("owner", ""))),
        not_before=_when(row.get("not_before")),
        spec=row.get("spec") or {},
    )
    return SimpleNamespace(
        record=record,
        assignee_creature_id=row.get("assignee_creature_id"),
        assignment_state=row.get("assignment_state"),
        availability=str(row.get("availability", "")),
        durability=str(row.get("durability", "")),
        allowed_actions=tuple(row.get("allowed_actions") or ()),
    )


def _progress(row: dict) -> SimpleNamespace:
    return SimpleNamespace(
        summary=str(row.get("summary", "")),
        created_at=_when(row.get("created_at")) or datetime.now(),
    )


def _delivery(row: dict) -> SimpleNamespace:
    return SimpleNamespace(
        delivery_id=str(row.get("delivery_id", "")),
        state=str(row.get("state", "")),
        reason=str(row.get("reason", "")),
        attempt=int(row.get("attempt", 0) or 0),
        created_at=_when(row.get("created_at")) or datetime.now(),
        last_error=row.get("last_error"),
    )


class RemoteDrives:
    """The drive calls ktui makes, as REST calls (actor and privilege are the server's)."""

    def __init__(self, session: "RemoteSession"):
        self.session = session

    def _path(self, tail: str = "") -> str:
        base = f"/api/sessions/{self.session.session_id}/drives"
        return f"{base}/{tail}" if tail else base

    async def list_drives(self, **_: Any) -> list[SimpleNamespace]:
        body = await self.session.get(self._path(), include_terminal="true")
        return [view_of(r) for r in (body or {}).get("drives") or []]

    async def get_drive(self, drive_id: str, **_: Any) -> SimpleNamespace | None:
        body = await self.session.get(self._path(drive_id))
        return view_of(body) if body else None

    async def list_drive_progress(self, drive_id: str, **_: Any) -> list:
        body = await self.session.get(self._path(f"{drive_id}/progress"))
        return [_progress(r) for r in (body or {}).get("progress") or []]

    async def list_drive_deliveries(self, drive_id: str, **_: Any) -> list:
        body = await self.session.get(self._path(f"{drive_id}/deliveries"))
        return [_delivery(r) for r in (body or {}).get("deliveries") or []]

    async def transition_drive(
        self, drive_id: str, status: Any, *, expected_revision: int, **_: Any
    ) -> Any:
        target = getattr(status, "value", status)
        body = {"target_status": target, "expected_revision": expected_revision}
        return await self.session.post(self._path(f"{drive_id}/transition"), body)

    async def wake_drive(
        self, drive_id: str, *, expected_revision: int, **_: Any
    ) -> Any:
        body = {"expected_revision": expected_revision}
        return await self.session.post(self._path(f"{drive_id}/wake"), body)

    async def report_drive_progress(
        self, drive_id: str, *, summary: str, evidence: Any = None, **_: Any
    ) -> Any:
        body = {"summary": summary, "evidence": evidence or None}
        return await self.session.post(self._path(f"{drive_id}/progress"), body)

    async def assign_drive(
        self,
        drive_id: str,
        assignee_creature_id: str,
        assignee_graph_id: str,
        *,
        expected_revision: int,
        **_: Any,
    ) -> Any:
        body = {
            "assignee_creature_id": assignee_creature_id,
            "assignee_graph_id": assignee_graph_id,
            "expected_revision": expected_revision,
        }
        return await self.session.post(self._path(f"{drive_id}/assign"), body)

    async def unassign_drive(
        self, drive_id: str, *, expected_revision: int, **_: Any
    ) -> Any:
        body = {"expected_revision": expected_revision}
        return await self.session.post(self._path(f"{drive_id}/unassign"), body)

    async def replay_drive_delivery(self, delivery_id: str, **_: Any) -> Any:
        return await self.session.post(self._path(f"deliveries/{delivery_id}/replay"))

    async def create_goal(self, objective: str, assignee: str) -> SimpleNamespace:
        """A goal drive for ``assignee`` (what ``/config`` → new goal does locally)."""
        body = {
            "kind": "goal",
            "title": objective[:120],
            "spec": build_goal_spec(objective),
            "assignee_creature_id": assignee,
        }
        created = await self.session.post(self._path(), body)
        return view_of(created or {})
