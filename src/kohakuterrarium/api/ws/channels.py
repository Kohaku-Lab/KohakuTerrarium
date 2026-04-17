"""WebSocket endpoint for streaming terrarium channel events."""

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from kohakuterrarium.api.deps import get_manager
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

router = APIRouter()


@router.websocket("/ws/terrariums/{terrarium_id}/channels")
async def channel_stream(websocket: WebSocket, terrarium_id: str):
    """Stream all channel messages from a terrarium in real-time."""
    await websocket.accept()
    manager = get_manager()

    try:
        async for event in manager.terrarium_channel_stream(terrarium_id):
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
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.debug("WebSocket close error", error=str(e), exc_info=True)
        try:
            await websocket.send_json({"type": "error", "content": str(e)})
        except Exception:
            pass
        await websocket.close()
