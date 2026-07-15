"""WebSocket endpoint for watching file changes in a creature's working directory.

Host-local: thin shell over
:func:`kohakuterrarium.studio.attach.workspace_watch.watch_directory`.
Remote creatures receive a structured error because workspace watching
requires a worker-side filesystem event stream; ``terrarium.files`` currently
supports only one-shot file operations.

Wire format (server → client):

    { "type": "ready",  "root": "/path/to/cwd" }
    { "type": "change", "changes": [{"path": "...", "action": "..."}] }
    { "type": "error",  "text": "..." }
"""

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect

from kohakuterrarium.api.auth.ws_auth import accept_with_auth_echo
from kohakuterrarium.api.deps import get_service
from kohakuterrarium.studio._runtime import host_engine_or_none
from kohakuterrarium.studio.attach.workspace_watch import watch_directory
from kohakuterrarium.studio.sessions.lifecycle import find_creature
from kohakuterrarium.terrarium.service import TerrariumService
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

router = APIRouter()


@router.websocket("/ws/files/{agent_id}")
async def watch_files(
    websocket: WebSocket,
    agent_id: str,
    service: TerrariumService = Depends(get_service),
):
    """Watch file changes in a creature's working directory."""
    await accept_with_auth_echo(websocket)

    # Lab hosts have no local engine, so lookup falls through to the
    # cluster-wide remote-creature check.
    engine = host_engine_or_none(service)
    creature = None
    if engine is not None:
        try:
            creature = find_creature(engine, "_", agent_id)
        except Exception:  # noqa: BLE001 — local-lookup failure routes to remote
            creature = None
    if creature is None:
        # Registry presence alone establishes that the creature is remote;
        # requiring graph metadata would reject otherwise valid entries.
        try:
            info = await service.get_creature_info(agent_id)
        except Exception:
            info = None
        if info is not None:
            await websocket.send_json(
                {
                    "type": "error",
                    "text": (
                        f"File watch for {agent_id!r} is not available — "
                        "the creature lives on a remote worker and "
                        "filesystem-watch is host-local only in v1.5.0."
                    ),
                }
            )
            await websocket.close()
            return
        await websocket.send_json(
            {"type": "error", "text": f"Agent not found: {agent_id}"}
        )
        await websocket.close()
        return

    agent = creature.agent
    root = getattr(agent, "_working_dir", None)
    if not root:
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
            logger.warning("Failed to close file watch WS", error=str(e), exc_info=True)
