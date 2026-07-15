"""WebSocket endpoint — single IO attach.

Mounts at ``/ws/sessions/{session_id}/creatures/{creature_id}/chat``.
Provides the unified replacement for the legacy agent, terrarium, and
creature chat endpoint shapes.
"""

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from kohakuterrarium.api.auth.ws_auth import accept_with_auth_echo
from kohakuterrarium.api.deps import get_service_legacy as get_service
from kohakuterrarium.studio.attach.io import attach_io
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

router = APIRouter()


@router.websocket("/ws/sessions/{session_id}/creatures/{creature_id}/chat")
async def session_creature_chat(
    websocket: WebSocket, session_id: str, creature_id: str
):
    """Bidirectional engine-backed chat for one creature."""
    await accept_with_auth_echo(websocket)
    # The global service preserves the existing chat session-id contract across
    # standalone and multi-user deployments; the local alias remains a test seam.
    service = get_service()

    try:
        await attach_io(websocket, service, session_id, creature_id)
    except KeyError:
        try:
            await websocket.send_json(
                {"type": "error", "content": f"creature {creature_id!r} not found"}
            )
        except Exception:
            pass
        await websocket.close()
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.warning("IO WS error", error=str(e), exc_info=True)
        try:
            await websocket.send_json({"type": "error", "content": str(e)})
        except Exception:
            pass
        await websocket.close()
