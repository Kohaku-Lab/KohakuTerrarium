"""Expose per-creature chat, editing, history, and branch operations.

Service routing sends remote creature operations to their home workers.
"""

from fastapi import APIRouter, Depends, Header, HTTPException

from kohakuterrarium.api.deps import get_service
from kohakuterrarium.api.routes.sessions_v2._helpers import resolve_creature_id
from kohakuterrarium.api.schemas import (
    AgentChat,
    BranchMutationResponse,
    MessageEdit,
    RegenerateRequest,
)
from kohakuterrarium.errors import ConflictError, NotFoundError
from kohakuterrarium.terrarium.service import TerrariumService

router = APIRouter()


@router.post("/{session_id}/creatures/{creature_id}/chat")
async def chat_creature(
    session_id: str,
    creature_id: str,
    req: AgentChat,
    service: TerrariumService = Depends(get_service),
):
    """Non-streaming HTTP chat fallback — collects the streaming chunks."""
    cid = await resolve_creature_id(service, creature_id, session_id)
    content = req.content if req.content is not None else (req.message or "")
    try:
        chunks: list[str] = []
        async for chunk in service.chat(cid, content):
            chunks.append(chunk)
        return {"response": "".join(chunks)}
    except KeyError:
        raise HTTPException(404, f"creature {creature_id!r} not found")


@router.post(
    "/{session_id}/creatures/{creature_id}/regenerate",
    response_model=BranchMutationResponse,
)
async def regenerate_creature(
    session_id: str,
    creature_id: str,
    req: RegenerateRequest | None = None,
    request_id: str | None = Header(default=None, alias="X-Request-ID"),
    service: TerrariumService = Depends(get_service),
):
    cid = await resolve_creature_id(service, creature_id, session_id)
    turn_index = req.turn_index if req is not None else None
    branch_view = req.branch_view if req is not None else None
    request_id = request_id or (req.request_id if req is not None else None)
    try:
        return await service.regenerate(
            cid,
            turn_index=turn_index,
            branch_view=branch_view,
            request_id=request_id,
        )
    except (NotFoundError, KeyError) as exc:
        raise HTTPException(404, str(exc)) from exc
    except (ConflictError, ValueError) as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post(
    "/{session_id}/creatures/{creature_id}/messages/{msg_idx}/edit",
    response_model=BranchMutationResponse,
)
async def edit_creature_message(
    session_id: str,
    creature_id: str,
    msg_idx: int,
    req: MessageEdit,
    request_id: str | None = Header(default=None, alias="X-Request-ID"),
    service: TerrariumService = Depends(get_service),
):
    if isinstance(req.content, list):
        content: str | list[dict] = [
            part.model_dump() if hasattr(part, "model_dump") else part
            for part in req.content
        ]
    else:
        content = req.content
    cid = await resolve_creature_id(service, creature_id, session_id)
    request_id = request_id or req.request_id
    try:
        edited = await service.edit_message(
            cid,
            msg_idx,
            content,
            turn_index=req.turn_index,
            user_position=req.user_position,
            branch_view=req.branch_view,
            request_id=request_id,
        )
        return edited
    except (NotFoundError, KeyError) as exc:
        raise HTTPException(404, str(exc)) from exc
    except (ConflictError, ValueError) as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/{session_id}/creatures/{creature_id}/messages/{msg_idx}/rewind")
async def rewind_creature(
    session_id: str,
    creature_id: str,
    msg_idx: int,
    service: TerrariumService = Depends(get_service),
):
    cid = await resolve_creature_id(service, creature_id, session_id)
    try:
        await service.rewind(cid, msg_idx)
        return {"status": "rewound"}
    except (NotFoundError, KeyError) as exc:
        raise HTTPException(404, str(exc)) from exc
    except (ConflictError, ValueError) as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("/{session_id}/creatures/{creature_id}/history")
async def creature_history(
    session_id: str,
    creature_id: str,
    service: TerrariumService = Depends(get_service),
):
    # Channel tabs share this endpoint through the ``ch:`` prefix.
    if creature_id.startswith("ch:"):
        channel_name = creature_id[3:]
        try:
            messages = await service.channel_history(session_id, channel_name)
        except KeyError:
            messages = []
        events = [
            {
                "type": "channel_message",
                "channel": channel_name,
                "sender": message.get("sender", ""),
                "content": message.get("content", ""),
                "ts": message.get("timestamp", message.get("ts", 0)),
            }
            for message in messages
        ]
        return {
            "creature_id": creature_id,
            "session_id": session_id,
            "messages": [],
            "events": events,
            "is_processing": False,
        }
    cid = await resolve_creature_id(service, creature_id, session_id)
    try:
        return await service.chat_history(cid)
    except KeyError:
        raise HTTPException(404, f"creature {creature_id!r} not found")


@router.get("/{session_id}/creatures/{creature_id}/branches")
async def creature_branches(
    session_id: str,
    creature_id: str,
    service: TerrariumService = Depends(get_service),
):
    cid = await resolve_creature_id(service, creature_id, session_id)
    try:
        return await service.chat_branches(cid)
    except KeyError:
        raise HTTPException(404, f"creature {creature_id!r} not found")
