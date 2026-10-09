"""The boot restore's state: what came back, what failed, Retry and Dismiss.

``GET /api/sessions/restore-state`` lists the live-session rows with their
last outcome; ``POST .../retry`` resumes one failed row now;
``POST .../dismiss`` drops a row so it is not restored again.
"""

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from kohakuterrarium.session import run_state
from kohakuterrarium.api.boot_restore import auto_resume_enabled, service_resolver
from kohakuterrarium.studio.sessions.live import live_sessions, path_key
from kohakuterrarium.studio.sessions.live.restore import restore_row

router = APIRouter()


class RestoreTarget(BaseModel):
    path: str


def _row_view(row: dict) -> dict:
    return {
        "path": row.get("path"),
        "session_id": row.get("session_id"),
        "failed": row.get("failed"),
        "restored_this_boot": row.get("claimed_by") == run_state.BOOT_ID
        and not row.get("failed"),
        "added_at": row.get("added_at"),
    }


@router.get("/restore-state")
async def restore_state(request: Request) -> dict:
    task = getattr(request.app.state, "restore_task", None)
    return {
        "enabled": auto_resume_enabled(),
        "running": bool(task is not None and not task.done()),
        "outcomes": list(getattr(request.app.state, "restore_outcomes", []) or []),
        "rows": [_row_view(row) for row in live_sessions().rows()],
    }


@router.post("/restore-state/retry")
async def retry_restore(target: RestoreTarget, request: Request) -> dict:
    registry = live_sessions()
    row = registry.get(target.path)
    if row is None:
        raise HTTPException(status_code=404, detail="no live-session row for that path")
    service = service_resolver(request.app)(row.get("session_dir") or "")
    if service is None:
        raise HTTPException(
            status_code=409, detail="this server does not host that session"
        )
    outcome = await restore_row(service, row)
    outcomes = [
        o
        for o in getattr(request.app.state, "restore_outcomes", []) or []
        if path_key(o.get("path") or "") != path_key(target.path)
    ]
    request.app.state.restore_outcomes = [*outcomes, outcome]
    return outcome


@router.post("/restore-state/dismiss")
async def dismiss_restore(target: RestoreTarget) -> dict:
    return {"removed": live_sessions().remove(target.path)}
