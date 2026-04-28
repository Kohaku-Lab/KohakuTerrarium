"""Per-creature chat routes — HTTP fallback chat / regen / edit /
rewind / history / branches.
"""

from fastapi import APIRouter, Depends, HTTPException

from kohakuterrarium.api.deps import get_engine
from kohakuterrarium.api.schemas import AgentChat, MessageEdit
from kohakuterrarium.studio.sessions import creature_chat

router = APIRouter()


@router.post("/{session_id}/creatures/{creature_id}/chat")
async def chat_creature(
    session_id: str,
    creature_id: str,
    req: AgentChat,
    engine=Depends(get_engine),
):
    """Non-streaming HTTP chat fallback."""
    content = req.content if req.content is not None else (req.message or "")
    try:
        chunks: list[str] = []
        async for chunk in creature_chat.chat(engine, session_id, creature_id, content):
            chunks.append(chunk)
        return {"response": "".join(chunks)}
    except KeyError:
        raise HTTPException(404, f"creature {creature_id!r} not found")


@router.post("/{session_id}/creatures/{creature_id}/regenerate")
async def regenerate_creature(
    session_id: str, creature_id: str, engine=Depends(get_engine)
):
    try:
        await creature_chat.regenerate(engine, session_id, creature_id)
        return {"status": "regenerating"}
    except KeyError:
        raise HTTPException(404, f"creature {creature_id!r} not found")


@router.post("/{session_id}/creatures/{creature_id}/messages/{msg_idx}/edit")
async def edit_creature_message(
    session_id: str,
    creature_id: str,
    msg_idx: int,
    req: MessageEdit,
    engine=Depends(get_engine),
):
    # Multimodal edit payloads arrive as a list of Pydantic content-part
    # models — flatten to plain dicts so downstream code can rely on
    # ``isinstance(item, dict)`` without needing to know about Pydantic.
    if isinstance(req.content, list):
        content: str | list[dict] = [
            part.model_dump() if hasattr(part, "model_dump") else part
            for part in req.content
        ]
    else:
        content = req.content
    try:
        edited = await creature_chat.edit_message(
            engine,
            session_id,
            creature_id,
            msg_idx,
            content,
            turn_index=req.turn_index,
            user_position=req.user_position,
        )
    except KeyError:
        raise HTTPException(404, f"creature {creature_id!r} not found")
    if not edited:
        raise HTTPException(400, "Invalid edit target; expected a user message")
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
    engine=Depends(get_engine),
):
    try:
        await creature_chat.rewind(engine, session_id, creature_id, msg_idx)
        return {"status": "rewound"}
    except KeyError:
        raise HTTPException(404, f"creature {creature_id!r} not found")


@router.get("/{session_id}/creatures/{creature_id}/history")
async def creature_history(
    session_id: str, creature_id: str, engine=Depends(get_engine)
):
    try:
        return creature_chat.history(engine, session_id, creature_id)
    except KeyError:
        raise HTTPException(404, f"creature {creature_id!r} not found")


@router.get("/{session_id}/creatures/{creature_id}/branches")
async def creature_branches(
    session_id: str, creature_id: str, engine=Depends(get_engine)
):
    try:
        return creature_chat.branches(engine, session_id, creature_id)
    except KeyError:
        raise HTTPException(404, f"creature {creature_id!r} not found")
