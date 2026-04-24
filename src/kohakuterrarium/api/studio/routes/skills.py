"""Studio skills routes — discover + runtime toggle (Qa).

Two endpoints:

- ``GET  /api/studio/skills`` — list every procedural skill the
  workspace can see (project / user / packages), with ``enabled``,
  ``origin``, ``description``, and ``disable_model_invocation``.
- ``POST /api/studio/skills/{name}/toggle`` — flip the persisted
  enabled state.

Toggle state is kept in ``~/.kohakuterrarium/skill_state.json`` so the
Studio can manage it even when no agent is running. Running agents
apply on top of it via :class:`SkillRegistry` + their session
scratchpad; the studio-managed file is the global default.
"""

import json
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

from kohakuterrarium.api.studio.deps import get_workspace_optional
from kohakuterrarium.api.studio.workspace.base import Workspace
from kohakuterrarium.skills import Skill, discover_skills

router = APIRouter()


_STATE_FILE = Path.home() / ".kohakuterrarium" / "skill_state.json"


def _load_state() -> dict[str, bool]:
    if not _STATE_FILE.exists():
        return {}
    try:
        data = json.loads(_STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k): bool(v) for k, v in data.items()}


def _save_state(state: dict[str, bool]) -> None:
    _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    _STATE_FILE.write_text(json.dumps(state, indent=2, sort_keys=True), "utf-8")


@router.get("")
async def list_skills(
    ws: Workspace | None = Depends(get_workspace_optional),
) -> list[dict]:
    """List every procedural skill discoverable from the workspace cwd."""
    cwd = Path(ws.root) if ws is not None else Path.cwd()
    try:
        skills = discover_skills(cwd=cwd)
    except Exception as exc:
        raise HTTPException(
            500, detail={"code": "discovery_failed", "message": str(exc)}
        ) from exc
    state = _load_state()
    return [_serialize(s, state) for s in skills]


@router.post("/{name}/toggle")
async def toggle_skill(
    name: str,
    ws: Workspace | None = Depends(get_workspace_optional),
) -> dict:
    """Flip the persisted enabled state for ``name``.

    Returns the new ``{"name", "enabled"}`` tuple so the frontend
    doesn't need a follow-up GET.
    """
    cwd = Path(ws.root) if ws is not None else Path.cwd()
    try:
        skills = discover_skills(cwd=cwd)
    except Exception as exc:
        raise HTTPException(
            500, detail={"code": "discovery_failed", "message": str(exc)}
        ) from exc
    matching = next((s for s in skills if s.name == name), None)
    if matching is None:
        raise HTTPException(
            404,
            detail={
                "code": "skill_not_found",
                "message": f"Skill not found: {name!r}",
            },
        )
    state = _load_state()
    current = state.get(name, matching.enabled)
    state[name] = not current
    _save_state(state)
    return {"name": name, "enabled": state[name]}


def _serialize(skill: Skill, state: dict[str, bool]) -> dict:
    """Produce the JSON shape the frontend consumes."""
    enabled = state.get(skill.name, skill.enabled)
    return {
        "name": skill.name,
        "description": skill.description,
        "origin": skill.origin,
        "enabled": bool(enabled),
        "disable_model_invocation": skill.disable_model_invocation,
        "paths": list(skill.paths),
        "allowed_tools": list(skill.allowed_tools),
        "base_dir": str(skill.base_dir) if skill.base_dir else None,
    }
