"""WebSocket endpoint for watching file changes in a creature's working directory.

Thin shell over
:func:`kohakuterrarium.studio.attach.workspace_watch.watch_directory`.
Resolves the creature's working directory through the engine and hands
the live ``watchfiles`` loop off to the studio attach module.

Wire format (server → client):

    { "type": "ready",  "root": "/path/to/cwd" }
    { "type": "change", "changes": [{"path": "...", "action": "..."}] }
    { "type": "error",  "text": "..." }
"""

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from kohakuterrarium.api.deps import get_engine
from kohakuterrarium.studio.attach.workspace_watch import watch_directory
from kohakuterrarium.studio.sessions.lifecycle import find_creature
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

router = APIRouter()


@router.websocket("/ws/files/{agent_id}")
async def watch_files(websocket: WebSocket, agent_id: str):
    """Watch file changes in a creature's working directory."""
    await websocket.accept()

    engine = get_engine()
    try:
        creature = find_creature(engine, "_", agent_id)
    except KeyError:
        await websocket.send_json(
            {"type": "error", "text": f"Agent not found: {agent_id}"}
        )
        await websocket.close()
        return

    agent = creature.agent
    root = getattr(agent, "_working_dir", None)
    if not root:
        # Fall back to executor working dir.
        root = getattr(getattr(agent, "executor", None), "_working_dir", None)
    if not root:
        await websocket.send_json(
            {"type": "error", "text": "Agent has no working directory"}
        )
        await websocket.close()
        return

    logger.info("File watcher starting", root=str(root), agent_id=agent_id)
    try:
        await watch_directory(str(root), websocket)
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.error(
            "file watch WS crashed", error=str(e), root=str(root), exc_info=True
        )
        try:
            await websocket.send_json({"type": "error", "text": str(e)})
            await websocket.close()
        except Exception as e:
            logger.debug("Failed to close file watch WS", error=str(e), exc_info=True)
