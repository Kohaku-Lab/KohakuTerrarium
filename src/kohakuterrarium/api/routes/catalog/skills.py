"""Discover procedural skills and persist their enabled state.

Discovery spans workspace, user, and installed-package locations, so filesystem
work runs outside the event loop.
"""

import asyncio
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

from kohakuterrarium.api.routes.catalog._deps import get_workspace_optional
from kohakuterrarium.skills import discover_skills
from kohakuterrarium.studio.editors.skills_state import (
    load_state,
    save_state,
    serialize,
)
from kohakuterrarium.studio.editors.workspace_manifest import Workspace

router = APIRouter()


def _discover_with_state(cwd: Path) -> list[dict]:
    skills = discover_skills(cwd=cwd)
    state = load_state()
    return [serialize(s, state) for s in skills]


@router.get("")
async def list_skills(
    ws: Workspace | None = Depends(get_workspace_optional),
) -> list[dict]:
    """List skills discoverable from the workspace context with persisted state."""
    cwd = Path(ws.root) if ws is not None else Path.cwd()
    try:
        return await asyncio.to_thread(_discover_with_state, cwd)
    except Exception as exc:
        raise HTTPException(
            500, detail={"code": "discovery_failed", "message": str(exc)}
        ) from exc


def _toggle_skill_sync(cwd: Path, name: str) -> dict:
    skills = discover_skills(cwd=cwd)
    matching = next((s for s in skills if s.name == name), None)
    if matching is None:
        raise FileNotFoundError(name)
    state = load_state()
    current = state.get(name, matching.enabled)
    state[name] = not current
    save_state(state)
    return {"name": name, "enabled": state[name]}


@router.post("/{name}/toggle")
async def toggle_skill(
    name: str,
    ws: Workspace | None = Depends(get_workspace_optional),
) -> dict:
    """Flip and persist the enabled state of a discoverable skill."""
    cwd = Path(ws.root) if ws is not None else Path.cwd()
    try:
        return await asyncio.to_thread(_toggle_skill_sync, cwd, name)
    except FileNotFoundError:
        raise HTTPException(
            404,
            detail={
                "code": "skill_not_found",
                "message": f"Skill not found: {name!r}",
            },
        )
    except Exception as exc:
        raise HTTPException(
            500, detail={"code": "discovery_failed", "message": str(exc)}
        ) from exc
