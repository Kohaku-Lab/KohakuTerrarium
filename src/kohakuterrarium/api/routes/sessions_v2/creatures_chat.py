"""Expose per-creature chat, editing, history, and branch operations.

Service routing sends remote creature operations to their home workers.
"""

from typing import Any

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
from kohakuterrarium.session.raw_history import UserMessageSelector
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
    target = (
        UserMessageSelector(**req.target.model_dump())
        if req is not None and req.target is not None
        else None
    )
    try:
        kwargs = {
            "turn_index": turn_index,
            "branch_view": branch_view,
            "request_id": request_id,
        }
        if target is not None:
            kwargs["target"] = target
        return await service.regenerate(cid, **kwargs)
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
        target = UserMessageSelector(**req.target.model_dump()) if req.target else None
        kwargs = {
            "turn_index": req.turn_index,
            "user_position": req.user_position,
            "branch_view": req.branch_view,
            "request_id": request_id,
        }
        if target is not None:
            kwargs["target"] = target
        edited = await service.edit_message(cid, msg_idx, content, **kwargs)
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


def _history_max_event_id(events: list) -> int:
    out = 0
    for evt in events:
        eid = evt.get("event_id") if isinstance(evt, dict) else None
        if isinstance(eid, int) and eid > out:
            out = eid
    return out


@router.get("/{session_id}/creatures/{creature_id}/history")
async def creature_history(
    session_id: str,
    creature_id: str,
    since_event_id: int | None = None,
    paged: bool = False,
    stream: str = "events",
    limit: int = 400,
    before: str | None = None,
    after: str | None = None,
    history_id: str | None = None,
    service: TerrariumService = Depends(get_service),
):
    """History payload with an optional event cursor.

    Legacy behaviour (``paged`` omitted / ``False``) is unchanged:
    ``since_event_id`` trims ``events`` and ``max_event_id`` reports the
    newest event. With ``paged=true`` the endpoint returns a bounded,
    cursor-driven page via ``history_page``; the numeric cursor filter is
    rejected in paged mode; use the opaque before/after cursors instead.
    """
    if paged:
        if since_event_id is not None:
            raise HTTPException(400, "paged history uses after, not since_event_id")
        if limit <= 0:
            raise HTTPException(400, "limit must be a positive integer")
        return await _paged_history(
            service,
            session_id,
            creature_id,
            stream=stream,
            limit=limit,
            before=before,
            after=after,
            history_id=history_id,
        )
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
            # Channel events carry no event_id; report the contract field
            # explicitly so clients can read it unconditionally.
            "max_event_id": 0,
        }
    cid = await resolve_creature_id(service, creature_id, session_id)
    try:
        payload = await service.chat_history(cid)
    except KeyError:
        raise HTTPException(404, f"creature {creature_id!r} not found")
    events = payload.get("events") or []
    max_eid = _history_max_event_id(events)
    if since_event_id is not None:
        payload["events"] = [
            evt
            for evt in events
            if isinstance(evt, dict)
            and isinstance(evt.get("event_id"), int)
            and evt["event_id"] > since_event_id
        ]
        # Incremental payloads omit the conversation snapshot; it is only
        # valid for the full log and would mislead an appending client.
        payload.pop("messages", None)
    payload["max_event_id"] = max_eid
    return payload


async def _paged_history(
    service: TerrariumService,
    session_id: str,
    creature_id: str,
    *,
    stream: str,
    limit: int,
    before: str | None,
    after: str | None,
    history_id: str | None,
) -> dict[str, Any]:
    """Build a bounded paged history slice for the ``paged=true`` mode.

    Channel tabs route through the ``ch:`` prefix and are paged from the
    channel message log; channels are never given a numeric ``since_event_id``
    cursor. Event/snapshot streams forward to the service so remote and
    multi-node adapters page at the record's home node.
    """
    if creature_id.startswith("ch:"):
        if stream not in ("events", "channel"):
            raise HTTPException(400, "channel target requires channel stream")
        try:
            return await service.channel_history_page(
                session_id,
                creature_id[3:],
                limit=limit,
                before=before,
                after=after,
                history_id=history_id,
            )
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
    if stream not in ("events", "snapshot"):
        raise HTTPException(400, f"unsupported paged stream {stream!r}")
    cid = await resolve_creature_id(service, creature_id, session_id)
    try:
        return await service.chat_history_page(
            cid,
            stream=stream,
            limit=limit,
            before=before,
            after=after,
            history_id=history_id,
        )
    except KeyError:
        raise HTTPException(404, f"creature {creature_id!r} not found")
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/{session_id}/creatures/{creature_id}/history/detail")
async def creature_history_detail(
    session_id: str,
    creature_id: str,
    stream: str,
    ref: str,
    history_id: str,
    service: TerrariumService = Depends(get_service),
):
    """Retrieve a full history record identified by an opaque detail token."""
    try:
        if creature_id.startswith("ch:"):
            return await service.channel_history_detail(
                session_id,
                creature_id[3:],
                stream=stream,
                ref=ref,
                history_id=history_id,
            )
        cid = await resolve_creature_id(service, creature_id, session_id)
        return await service.chat_history_detail(
            cid, stream=stream, ref=ref, history_id=history_id
        )
    except ConflictError as exc:
        raise HTTPException(409, str(exc)) from exc
    except (NotFoundError, KeyError) as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/{session_id}/creatures/{creature_id}/events/{event_id}")
async def creature_event(
    session_id: str,
    creature_id: str,
    event_id: int,
    service: TerrariumService = Depends(get_service),
):
    """Lazy single-event fetch: full tool/subagent output on expand.

    History payloads carry bounded ``output_preview`` strings; the client
    calls this to load the full ``output``/``result`` of one event.
    """
    cid = await resolve_creature_id(service, creature_id, session_id)
    try:
        return await service.chat_event(cid, event_id)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc


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
