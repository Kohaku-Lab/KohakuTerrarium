"""WebSocket endpoint for streaming agent chat."""

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from kohakuterrarium.api.deps import get_manager
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

router = APIRouter()


@router.websocket("/ws/agents/{agent_id}/chat")
async def agent_chat(websocket: WebSocket, agent_id: str):
    """Bidirectional streaming chat with a standalone agent."""
    await websocket.accept()
    manager = get_manager()

    try:
        while True:
            # Receive message from client
            data = await websocket.receive_json()
            message = data.get("message", "")

            if not message:
                continue

            # Stream response back
            async for chunk in manager.agent_chat(agent_id, message):
                await websocket.send_json(
                    {
                        "type": "text",
                        "content": chunk,
                    }
                )

            # Signal end of response
            await websocket.send_json({"type": "done"})

    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.debug("WebSocket close error", error=str(e), exc_info=True)
        await websocket.close()
