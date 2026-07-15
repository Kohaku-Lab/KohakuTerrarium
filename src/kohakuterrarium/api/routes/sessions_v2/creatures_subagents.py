"""Read sub-agent conversations and message live sub-agent runs."""

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from kohakuterrarium.api.deps import get_service
from kohakuterrarium.api.routes.sessions_v2._helpers import resolve_creature_id
from kohakuterrarium.errors import ConflictError, InvalidRequestError, NotFoundError
from kohakuterrarium.studio.sessions.creature_subagents import (
    read_subagent_conversation,
    send_to_subagent,
)
from kohakuterrarium.terrarium.service import TerrariumService

router = APIRouter()


class SubAgentMessage(BaseModel):
    content: str = ""
    message: str | None = None
    # Job identifiers disambiguate concurrent runs that share a name.
    job_id: str | None = None


@router.get("/{session_id}/creatures/{creature_id}/subagents/conversation")
async def read_subagent_conversation_route(
    session_id: str,
    creature_id: str,
    job_id: str | None = Query(default=None),
    name: str | None = Query(default=None),
    run: int | None = Query(default=None),
    service: TerrariumService = Depends(get_service),
):
    """Return a live or persisted sub-agent conversation.

    A job identifier targets a live task and falls back to its latest persisted
    run. A name targets a live interactive child or its latest persisted run; a
    run number selects a specific snapshot. Only live, running instances report
    ``can_receive`` and accept messages.
    """
    cid = await resolve_creature_id(service, creature_id, session_id)
    try:
        return read_subagent_conversation(
            service, session_id, cid, job_id=job_id, name=name, run=run
        )
    except NotFoundError as exc:
        raise HTTPException(404, str(exc))
    except InvalidRequestError as exc:
        raise HTTPException(400, str(exc))
    except KeyError:
        raise HTTPException(404, f"creature {creature_id!r} not found")


@router.post("/{session_id}/creatures/{creature_id}/subagents/{name}/send")
async def send_subagent_route(
    session_id: str,
    creature_id: str,
    name: str,
    req: SubAgentMessage,
    service: TerrariumService = Depends(get_service),
):
    """Send a user message to a running sub-agent.

    A body ``job_id`` targets one exact run; the path name is the fallback.
    Completed and persisted runs are read-only.
    """
    cid = await resolve_creature_id(service, creature_id, session_id)
    content = req.content if req.content else (req.message or "")
    if not content.strip():
        raise HTTPException(400, "message content is required")
    try:
        return await send_to_subagent(
            service, session_id, cid, name, content, job_id=req.job_id
        )
    except ConflictError as exc:
        raise HTTPException(409, str(exc))
    except NotFoundError as exc:
        raise HTTPException(404, str(exc))
    except KeyError:
        raise HTTPException(404, f"creature {creature_id!r} not found")
