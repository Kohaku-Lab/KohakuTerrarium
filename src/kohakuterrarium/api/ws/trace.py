"""Live trace WS shell — registers ``/ws/sessions/{name}/events``.

Thin wrapper around :mod:`kohakuterrarium.studio.attach.trace`. The
URL is preserved exactly to keep the existing frontend
``useSessionEventStream`` composable working without changes.
"""

from fastapi import APIRouter, WebSocket

from kohakuterrarium.studio.attach.trace import run_trace_attach

router = APIRouter()


@router.websocket("/ws/sessions/{session_name}/events")
async def session_events_stream(
    websocket: WebSocket, session_name: str, agent: str | None = None
):
    """Live event stream for a running session.

    See ``studio/attach/trace.py`` for the full mechanics.
    """
    await run_trace_attach(websocket, session_name, agent)
