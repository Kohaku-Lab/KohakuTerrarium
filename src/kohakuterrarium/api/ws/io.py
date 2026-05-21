"""WebSocket endpoint — single IO attach.

Mounts at ``/ws/sessions/{session_id}/creatures/{creature_id}/chat``.
Replaces the legacy ``/ws/agents/{id}/chat``,
``/ws/terrariums/{id}``, and ``/ws/creatures/{id}`` endpoints with one
URL shape per the Phase 2 plan.
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
    # Per-user routing for WS chat is a future enhancement; today the
    # WS uses the global legacy service so the standalone path and
    # multi-user path both remain consistent with the existing chat
    # session id contract.  Tests can still monkeypatch ``get_service``
    # on this module — the alias keeps that seam working.
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
        logger.debug("IO WS error", error=str(e), exc_info=True)
        try:
            await websocket.send_json({"type": "error", "content": str(e)})
        except Exception:
            pass
        await websocket.close()
