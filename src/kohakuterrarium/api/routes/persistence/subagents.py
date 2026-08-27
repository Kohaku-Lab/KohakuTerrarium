"""Read-only persisted sub-agent conversation endpoints."""

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from kohakuterrarium.api.deps import get_service
from kohakuterrarium.errors import ConflictError, InvalidRequestError, NotFoundError
from kohakuterrarium.studio.persistence.viewer.subagents import (
    build_subagent_conversation_payload,
    build_subagent_runs_payload,
)
from kohakuterrarium.terrarium.service import TerrariumService

from .viewer import (
    _build_single,
    _resolve_cluster_or_404,
    _run_per_member,
    _run_with_store,
)

router = APIRouter()


@router.get("/{session_name}/subagents")
async def get_session_subagents(
    session_name: str,
    parent: str | None = None,
    name: str | None = None,
    job_id: str | None = None,
    service: TerrariumService = Depends(get_service),
) -> dict[str, Any]:
    """List persisted sub-agent runs in one session or cluster."""
    members = await _resolve_cluster_or_404(session_name, service)

    def _build(store, canonical: str) -> dict[str, Any]:
        return build_subagent_runs_payload(
            store, canonical, parent=parent, name=name, job_id=job_id
        )

    if len(members) == 1:
        return await _build_single(service, members[0][0], members[0][1], _build)
    per_member = await asyncio.to_thread(_run_per_member, members, _build)
    runs = []
    for member_sid, payload in per_member:
        runs.extend({**row, "member_sid": member_sid} for row in payload["runs"])
    return {"session_name": session_name, "runs": runs}


@router.get("/{session_name}/subagents/conversation")
async def get_session_subagent_conversation(
    session_name: str,
    parent: str,
    job_id: str | None = None,
    name: str | None = None,
    run: int | None = None,
    service: TerrariumService = Depends(get_service),
) -> dict[str, Any]:
    """Read an exact persisted conversation with safe legacy fallback."""
    members = await _resolve_cluster_or_404(session_name, service)

    def _build(store, canonical: str) -> dict[str, Any]:
        return build_subagent_conversation_payload(
            store,
            canonical,
            parent=parent,
            job_id=job_id,
            name=name,
            run=run,
        )

    if len(members) == 1:
        try:
            return await _build_single(service, members[0][0], members[0][1], _build)
        except NotFoundError as exc:
            raise HTTPException(404, str(exc)) from exc
        except InvalidRequestError as exc:
            raise HTTPException(400, str(exc)) from exc
        except ConflictError as exc:
            raise HTTPException(409, str(exc)) from exc

    def _build_cluster() -> list[tuple[str, dict[str, Any]]]:
        resolved: list[tuple[str, dict[str, Any]]] = []
        conflicts: list[ConflictError] = []
        invalid: list[InvalidRequestError] = []
        for member_sid, path in members:
            try:
                resolved.append((member_sid, _run_with_store(path, _build)))
            except NotFoundError:
                continue
            except ConflictError as exc:
                conflicts.append(exc)
            except InvalidRequestError as exc:
                invalid.append(exc)
        exact = [
            entry for entry in resolved if entry[1].get("resolution") == "exact_job_id"
        ]
        if exact:
            duplicate_exact = [
                conflict
                for conflict in conflicts
                if "multiple persisted runs match job_id" in str(conflict)
            ]
            if duplicate_exact:
                raise duplicate_exact[0]
            return exact
        if conflicts:
            raise conflicts[0]
        if resolved:
            return resolved
        if invalid:
            raise invalid[0]
        return []

    try:
        per_member = await asyncio.to_thread(_build_cluster)
    except InvalidRequestError as exc:
        raise HTTPException(400, str(exc)) from exc
    except ConflictError as exc:
        raise HTTPException(409, str(exc)) from exc
    if not per_member:
        raise HTTPException(404, "sub-agent conversation not found")
    exact = [
        entry for entry in per_member if entry[1].get("resolution") == "exact_job_id"
    ]
    selected = exact if exact else per_member
    if len(selected) > 1:
        raise HTTPException(409, "sub-agent conversation is ambiguous across members")
    member_sid, payload = selected[0]
    return {**payload, "member_sid": member_sid}
