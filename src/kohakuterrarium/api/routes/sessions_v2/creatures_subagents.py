"""Per-creature sub-agent routes — read a sub-agent's inner conversation
and send a live user message to a running sub-agent.

Mounted at ``/api/sessions``; URLs land at
``/api/sessions/{session_id}/creatures/{creature_id}/subagents/...``.

Service-driven resolution (``Depends(get_service)`` +
``resolve_creature_id``) mirrors ``creatures_ctl.py`` / ``creatures_chat.py``.
Sub-agent internals are read off the host engine's live ``Agent`` — the
same single-host reach ``creatures_chat.history`` uses.
"""

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
    # Precise live target: the ``job_id`` the frontend block carries.
    # Prefer it so concurrent same-name runs aren't ambiguous; the path
    # ``{name}`` is the fallback target.
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
    """Read a sub-agent run's conversation.

    ``?job_id=`` reads a live task sub-agent, falling back to the latest
    persisted run for that name when the job is no longer live (e.g. a
    resumed session, whose event log carries only the job id). ``?name=``
    alone reads a live interactive child, else the latest persisted run for
    that name; ``?name=&run=`` reads a specific persisted snapshot.
    ``can_receive`` is true for any live, still-running
    instance (one-shot included) — the flag the frontend gates its chat box
    on; completed / persisted runs are read-only.
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
    """Send a live user message to a RUNNING sub-agent.

    ``job_id`` in the body targets one exact live run (preferred); the
    path ``{name}`` is the fallback target. Rejected with 409 when there
    is no live sub-agent to receive it — completed runs are read-only.
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
