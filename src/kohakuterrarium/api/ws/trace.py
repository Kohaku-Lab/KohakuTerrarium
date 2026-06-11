"""Live trace WS shell — registers ``/ws/sessions/{name}/events``.

Thin wrapper around :mod:`kohakuterrarium.studio.attach.trace`. The
URL is preserved exactly to keep the existing frontend
``useSessionEventStream`` composable working without changes.
"""

from fastapi import APIRouter, Depends, WebSocket

from kohakuterrarium.api.deps import get_service
from kohakuterrarium.studio.attach.trace import run_trace_attach
from kohakuterrarium.studio.sessions.registry import stores_for
from kohakuterrarium.terrarium.service import TerrariumService

router = APIRouter()


@router.websocket("/ws/sessions/{session_name}/events")
async def session_events_stream(
    websocket: WebSocket,
    session_name: str,
    agent: str | None = None,
    service: TerrariumService = Depends(get_service),
):
    """Live event stream for a running session.

    See ``studio/attach/trace.py`` for the full mechanics.  The live
    store registry is instance-scoped, so this shell resolves it from
    the request's service and hands the iterable to the attach helper.
    """
    await run_trace_attach(websocket, session_name, agent, stores=stores_for(service))
