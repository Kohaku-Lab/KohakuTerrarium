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
    """Stream session events using the request service's live-store registry."""
    await run_trace_attach(websocket, session_name, agent, stores=stores_for(service))
