"""Expose per-creature chat, editing, history, and branch operations.

Service routing sends remote creature operations to their home workers.
"""

from fastapi import APIRouter, Depends, HTTPException

from kohakuterrarium.api.deps import get_service
from kohakuterrarium.api.routes.sessions_v2._helpers import resolve_creature_id
from kohakuterrarium.api.schemas import AgentChat, MessageEdit, RegenerateRequest
from kohakuterrarium.studio._runtime import host_engine_or_none
from kohakuterrarium.studio.sessions.creature_chat import channel_history
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


@router.post("/{session_id}/creatures/{creature_id}/regenerate")
async def regenerate_creature(
    session_id: str,
    creature_id: str,
    req: RegenerateRequest | None = None,
    service: TerrariumService = Depends(get_service),
):
    cid = await resolve_creature_id(service, creature_id, session_id)
    turn_index = req.turn_index if req is not None else None
    branch_view = req.branch_view if req is not None else None
    try:
        result = await service.regenerate(
            cid, turn_index=turn_index, branch_view=branch_view
        )
    except KeyError:
        raise HTTPException(404, f"creature {creature_id!r} not found")
    # Preserve branch metadata so clients can update navigation before resync.
    if isinstance(result, dict):
        return result
    return {"status": "regenerating", "turn_index": turn_index}


@router.post("/{session_id}/creatures/{creature_id}/messages/{msg_idx}/edit")
async def edit_creature_message(
    session_id: str,
    creature_id: str,
    msg_idx: int,
    req: MessageEdit,
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
    try:
        edited = await service.edit_message(
            cid,
            msg_idx,
            content,
            turn_index=req.turn_index,
            user_position=req.user_position,
            branch_view=req.branch_view,
        )
    except KeyError:
        raise HTTPException(404, f"creature {creature_id!r} not found")
    if not edited:
        raise HTTPException(400, "Invalid edit target; expected a user message")
    # Dict results carry new branch metadata; boolean results require a
    # compatibility response based on the request.
    if isinstance(edited, dict):
        return {
            "user_position": req.user_position,
            **edited,
        }
    return {
        "status": "edited",
        "turn_index": req.turn_index,
        "user_position": req.user_position,
    }


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
    except KeyError:
        raise HTTPException(404, f"creature {creature_id!r} not found")


@router.get("/{session_id}/creatures/{creature_id}/history")
async def creature_history(
    session_id: str,
    creature_id: str,
    service: TerrariumService = Depends(get_service),
):
    # Channel tabs share this endpoint through the ``ch:`` prefix. Prefer the
    # host store when populated, then use cluster-aware worker history.
    if creature_id.startswith("ch:"):
        channel_name = creature_id[3:]
        engine = host_engine_or_none(service)
        if engine is not None:
            payload = channel_history(engine, session_id, channel_name)
            if payload.get("events"):
                return payload
        # Normalize service messages as events expected by channel-tab replay.
        try:
            messages = await service.channel_history(session_id, channel_name)
        except (KeyError, AttributeError):
            messages = []
        except Exception:
            messages = []
        events: list[dict] = []
        for m in messages or []:
            events.append(
                {
                    "type": "channel_message",
                    "channel": channel_name,
                    "sender": m.get("sender", ""),
                    "content": m.get("content", ""),
                    "ts": m.get("ts", 0) or m.get("timestamp", 0),
                }
            )
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
