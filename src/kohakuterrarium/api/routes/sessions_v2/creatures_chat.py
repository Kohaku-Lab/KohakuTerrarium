"""Per-creature chat routes — HTTP fallback chat / regen / edit /
rewind / history / branches.

Service-driven: ``Depends(get_service)`` so multi-node lab-host
deployments route by ``_home`` automatically.  ``service.chat`` and
``service.chat_history`` already cross the lab transport for remote
creatures.
"""

from fastapi import APIRouter, Depends, HTTPException

from kohakuterrarium.api.deps import get_service
from kohakuterrarium.api.routes.sessions_v2._helpers import resolve_creature_id
from kohakuterrarium.api.schemas import AgentChat, MessageEdit, RegenerateRequest
from kohakuterrarium.studio._runtime import host_engine_or_none
from kohakuterrarium.studio.sessions.creature_chat import _channel_history
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
    # Pass through ``turn_index`` / ``branch_id`` from the service so
    # the frontend can promote the <N/M> navigator the instant the API
    # call returns, instead of waiting for the post-turn resync.
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
    # Newer service implementations return a dict carrying the just-
    # opened branch_id / turn_index so the frontend's navigator can
    # promote immediately. Older ones still return ``True`` — fall
    # back to echoing the request fields then.
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
    # The frontend uses the same endpoint for per-creature chat tabs and
    # per-channel tabs (``ch:<name>``).  In lab-host mode the host engine
    # has no attached session store, but the service's cluster-aware
    # ``channel_history`` already unions messages across every cluster
    # member's worker store (CF-4). CF-9: delegate to it so the channel
    # tab is non-empty even when ``_channel_history``'s studio-attached
    # store walk finds nothing.
    if creature_id.startswith("ch:"):
        channel_name = creature_id[3:]
        engine = host_engine_or_none(service)
        if engine is not None:
            payload = _channel_history(engine, session_id, channel_name)
            if payload.get("events"):
                return payload
        # Fall back to (or default to in lab-host) the service-routed
        # cluster fan-out. Shape the returned list of channel-message
        # dicts as ``channel_message`` events so the frontend's chat
        # replay can render them in the channel tab.
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
