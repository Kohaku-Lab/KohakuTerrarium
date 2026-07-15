"""Read-only Drive records for a saved or live session.

The saved-session viewer reads persisted Drive records directly from
``<name>.kohakutr.drives`` without resuming the session or constructing a
``DriveManager``. Rows are read-only, expose no allowed actions, and retain the
standard list-row redaction of specifications and evidence.

The router mounts at
``/api/persistence/viewer/{session_name}/drives``.
"""

import asyncio
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from kohakuterrarium.api.deps import get_service, resolve_request_session_dir
from kohakuterrarium.api.routes.persistence.live_paths import live_store_path
from kohakuterrarium.errors import SessionError
from kohakuterrarium.studio.persistence.store import resolve_session_path_in
from kohakuterrarium.terrarium.drive.errors import DriveError
from kohakuterrarium.terrarium.drive.requests import DriveQuery
from kohakuterrarium.terrarium.drive.saved_snapshot import DriveSidecarMissingError
from kohakuterrarium.terrarium.drive.store import open_drive_repository_readonly
from kohakuterrarium.terrarium.drive.store_migration import drive_sidecar_path
from kohakuterrarium.terrarium.service import TerrariumService

router = APIRouter()

_REDACTED = ("spec", "presentation", "metadata", "terminal_evidence")


def _saved_row(record: Any, assignment: Any) -> dict[str, Any]:
    """Build a redacted persisted-Drive row without live authorization data."""
    r = record
    return {
        "drive_id": r.drive_id,
        "kind": r.kind,
        "schema_version": r.schema_version,
        "revision": r.revision,
        "title": r.title,
        "status": r.status.value,
        "status_reason": r.status_reason,
        "scope_type": r.scope_type,
        "scope_id": r.scope_id,
        "priority": r.priority,
        "owner": r.owner.format(),
        "owner_scope": r.owner_scope,
        "created_by": r.created_by.format(),
        "created_at": r.created_at.isoformat(),
        "updated_at": r.updated_at.isoformat(),
        "lifecycle_epoch": r.lifecycle_epoch,
        "dependency_ids": list(r.dependency_ids),
        "assignee_creature_id": (
            assignment.assignee_creature_id if assignment is not None else None
        ),
        "assignment_state": (
            assignment.assignment_state if assignment is not None else None
        ),
        "availability": None,
        "durability": "persistent",
        "allowed_actions": [],
    }


async def _read_saved_drives(path: str | Path) -> list[dict[str, Any]]:
    """Read persisted Drive rows without opening or modifying the parent store.

    Opening a writable ``SessionStore`` would initialize a missing parent merely
    by viewing it. The sidecar path therefore uses the same expanded-path
    normalization directly. A missing sidecar means no drives and remains absent;
    an existing sidecar is opened read-only and closed by this function. Its single
    repository read keeps committed WAL rows consistent.
    """
    sidecar = drive_sidecar_path(Path(path).expanduser())
    repo = open_drive_repository_readonly(sidecar, session_path=path)
    try:
        pairs = await repo.list_saved_rows(DriveQuery(include_terminal=True))
        return [_saved_row(record, assignment) for record, assignment in pairs]
    except DriveSidecarMissingError:
        return []
    finally:
        await asyncio.to_thread(repo.close_blocking)


@router.get("/{session_name}/drives")
async def saved_session_drives(
    session_name: str,
    session_dir: Path = Depends(resolve_request_session_dir),
    service: TerrariumService = Depends(get_service),
):
    """Return persisted Drive records for a saved or live session.

    Live graph IDs resolve through their attached store because files are named
    by creature ID. If the active writer prevents an offline sidecar read, this
    viewer endpoint returns an empty list; the live Drive endpoint remains the
    authoritative source.

    Saved sessions resolve only within the request's session namespace, without
    global fallback. Missing sessions return 404, while locked or corrupt saved
    sidecars return 409.
    """
    live = live_store_path(service, session_name)
    if live is not None:
        try:
            return {"drives": await _read_saved_drives(live)}
        except (SessionError, DriveError):
            return {"drives": []}

    path = await asyncio.to_thread(resolve_session_path_in, session_name, session_dir)
    if path is None:
        raise HTTPException(404, f"Session not found: {session_name}")
    try:
        rows = await _read_saved_drives(path)
    except (SessionError, DriveError) as exc:
        raise HTTPException(409, f"session is not readable offline: {exc}")
    return {"drives": rows}
