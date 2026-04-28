"""WebSocket endpoint — single IO attach.

Mounts at ``/ws/sessions/{session_id}/creatures/{creature_id}/chat``.
Replaces the legacy ``/ws/agents/{id}/chat``,
``/ws/terrariums/{id}``, and ``/ws/creatures/{id}`` endpoints with one
URL shape per the Phase 2 plan.
"""

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from kohakuterrarium.api.deps import get_engine
from kohakuterrarium.studio.attach.io import attach_io
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

router = APIRouter()


@router.websocket("/ws/sessions/{session_id}/creatures/{creature_id}/chat")
async def session_creature_chat(
    websocket: WebSocket, session_id: str, creature_id: str
):
    """Bidirectional engine-backed chat for one creature."""
    await websocket.accept()
    engine = get_engine()

    try:
        await attach_io(websocket, engine, session_id, creature_id)
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
        logger.debug("IO WS error", error=str(e), exc_info=True)
        try:
            await websocket.send_json({"type": "error", "content": str(e)})
        except Exception:
            pass
        await websocket.close()
