"""WebSocket endpoint — service-backed channel observer.

Mounts at ``/ws/sessions/{sid}/observer``.

Service-driven: routes through ``service.subscribe(EventFilter)``
filtered to ``CHANNEL_MESSAGE`` events for the requested graph.  In
lab-host mode this fans out across nodes via
``MultiNodeTerrariumService.subscribe`` so a worker-hosted session's
channel traffic flows back to the controller's WS uninterrupted.

For a session that doesn't exist on any node we still send an
``error`` frame before closing — matches the pre-migration shape.
"""

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect

from kohakuterrarium.api.auth.ws_auth import accept_with_auth_echo
from kohakuterrarium.api.deps import get_service
from kohakuterrarium.terrarium.events import EventFilter, EventKind
from kohakuterrarium.terrarium.service import TerrariumService
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

router = APIRouter()


@router.websocket("/ws/sessions/{session_id}/observer")
async def session_channel_observer(
    websocket: WebSocket,
    session_id: str,
    service: TerrariumService = Depends(get_service),
):
    """Stream every shared-channel message from a session in real time."""
    await accept_with_auth_echo(websocket)

    # Confirm the session exists somewhere in the cluster before
    # subscribing — surfaces "not found" as an explicit error frame
    # rather than an empty stream that never yields anything.
    graph = await service.get_graph(session_id)
    if graph is None:
        try:
            await websocket.send_json(
                {"type": "error", "content": f"session {session_id!r} not found"}
            )
        except Exception:
            pass
        await websocket.close()
        return

    flt = EventFilter(
        kinds={EventKind.CHANNEL_MESSAGE},
        graph_ids={session_id},
    )
    try:
        async for event in service.subscribe(flt):
            payload = event.payload or {}
            await websocket.send_json(
                {
                    "type": "channel_message",
                    "channel": event.channel or payload.get("channel", ""),
                    "sender": payload.get("sender", ""),
                    # ``sender_id`` is the stable creature handle — keep
                    # both so the frontend can disambiguate two creatures
                    # that share a display name.
                    "sender_id": payload.get("sender_id"),
                    "content": payload.get("content", ""),
                    "message_id": payload.get("message_id", ""),
                    "timestamp": payload.get("timestamp", str(event.ts)),
                }
            )
    except WebSocketDisconnect:
        pass
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("Observer WS error", error=str(exc), exc_info=True)
        try:
            await websocket.send_json({"type": "error", "content": str(exc)})
        except Exception:
            pass
        try:
            await websocket.close()
        except Exception:
            pass
