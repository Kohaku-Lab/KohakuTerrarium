"""Read-only Drive records for a SAVED (non-live) session (design §12.3).

The saved-session viewer's Drives tab needs the persisted Drive records without
resuming the session. This reads them straight from the session's Drive sidecar
(``<name>.kohakutr.drives``) through the storage layer's public reader — no live
engine, no DriveManager, so rows carry no ``allowed_actions`` (the viewer is
read-only) and spec/evidence stay redacted like every list row.

Mounted under ``/api/persistence/viewer`` → ``/{session_name}/drives``.
"""

import asyncio
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from kohakuterrarium.api.deps import resolve_request_session_dir
from kohakuterrarium.errors import SessionError
from kohakuterrarium.studio.persistence.store import resolve_session_path_in
from kohakuterrarium.terrarium.drive.errors import DriveError
from kohakuterrarium.terrarium.drive.requests import DriveQuery
from kohakuterrarium.terrarium.drive.saved_snapshot import DriveSidecarMissingError
from kohakuterrarium.terrarium.drive.store import open_drive_repository_readonly
from kohakuterrarium.terrarium.drive.store_migration import drive_sidecar_path

router = APIRouter()

_REDACTED = ("spec", "presentation", "metadata", "terminal_evidence")


def _saved_row(record: Any, assignment: Any) -> dict[str, Any]:
    """A redacted, read-only row for a persisted Drive record (no live ACL)."""
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
    """List a saved session's persisted Drive records WITHOUT touching the parent.

    Strictly read-only, and it NEVER opens the parent ``.kohakutr``: constructing a
    writable ``SessionStore`` would initialise that database merely by viewing. The
    sidecar path is derived from the session path exactly as ``SessionStore`` would
    normalise it (``Path.expanduser``). A session whose ``.drives`` sidecar is
    absent reports zero drives and the sidecar stays absent (viewing never migrates
    or initialises Drive storage). An existing sidecar is opened ``mode=ro`` and
    read under ONE transaction so committed WAL rows are included consistently;
    THIS caller owns closing the read-only repo.
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
):
    """Read-only persisted Drive records for a saved session.

    Resolves the session strictly inside the caller's L4 session namespace
    (R1-01): an anonymous ``required``-mode request is rejected upstream by the
    dependency, and no user namespace ever falls back to the global directory.
    404 if the session file does not exist; a live/locked or corrupt sidecar is
    refused with 409 (typed) rather than a partial/stale read or a raw storage
    error (use the live ``/api/sessions/{sid}/drives`` surface for a running one).
    """
    path = await asyncio.to_thread(resolve_session_path_in, session_name, session_dir)
    if path is None:
        raise HTTPException(404, f"Session not found: {session_name}")
    try:
        rows = await _read_saved_drives(path)
    except (SessionError, DriveError) as exc:
        raise HTTPException(409, f"session is not readable offline: {exc}")
    return {"drives": rows}
