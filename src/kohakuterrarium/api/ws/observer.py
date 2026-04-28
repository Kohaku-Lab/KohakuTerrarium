"""WebSocket endpoint — engine-backed channel observer.

Mounts at ``/ws/sessions/{sid}/observer``.  Replaces the legacy
``/ws/terrariums/{tid}/channels`` path; the body lives in
:func:`kohakuterrarium.studio.attach.observer.stream_session_channels`.
"""

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from kohakuterrarium.api.deps import get_engine
from kohakuterrarium.studio.attach.observer import stream_session_channels
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

router = APIRouter()


@router.websocket("/ws/sessions/{session_id}/observer")
async def session_channel_observer(websocket: WebSocket, session_id: str):
    """Stream every shared-channel message from a session in real time."""
    await websocket.accept()
    engine = get_engine()

    try:
        async for event in stream_session_channels(engine, session_id):
            await websocket.send_json(
                {
                    "type": "channel_message",
                    "channel": event.channel,
                    "sender": event.sender,
                    "content": event.content,
                    "message_id": event.message_id,
                    "timestamp": event.timestamp.isoformat(),
                }
            )
    except KeyError as e:
        try:
            await websocket.send_json({"type": "error", "content": str(e)})
        except Exception:
            pass
        await websocket.close()
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.debug("Observer WS error", error=str(e), exc_info=True)
        try:
            await websocket.send_json({"type": "error", "content": str(e)})
        except Exception:
            pass
        await websocket.close()
