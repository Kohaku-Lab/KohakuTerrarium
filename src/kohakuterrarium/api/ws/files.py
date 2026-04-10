"""WebSocket endpoint for watching file changes in an agent's working directory.

Uses `watchfiles` (already a uvicorn dependency) to detect filesystem
changes and pushes them to the frontend as JSON frames:

    { "type": "change", "changes": [{"path": "...", "action": "modified"}] }
    { "type": "ready", "root": "/path/to/cwd" }

The frontend can use these events to refresh the file tree and editor
tabs without polling.
"""

import asyncio
from pathlib import Path

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from kohakuterrarium.api.deps import get_manager
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

router = APIRouter()

_ACTION_MAP = {1: "added", 2: "modified", 3: "deleted"}


async def _watch_directory(root: str, websocket: WebSocket) -> None:
    """Watch a directory for changes and push events via WebSocket."""
    try:
        from watchfiles import awatch
    except ImportError:
        await websocket.send_json({"type": "error", "text": "watchfiles not installed"})
        return

    root_path = Path(root)
    if not root_path.is_dir():
        await websocket.send_json({"type": "error", "text": f"Not a directory: {root}"})
        return

    logger.info("File watcher awatch starting", root=root)
    await websocket.send_json({"type": "ready", "root": root})

    try:
        async for changes in awatch(
            root,
            recursive=True,
            step=1000,
        ):
            batch = []
            for action, path_str in changes:
                # Skip hidden/build directories to reduce noise
                rel = Path(path_str).relative_to(root_path)
                parts = rel.parts
                if any(
                    p.startswith(".")
                    or p in ("node_modules", "__pycache__", ".git", "venv", ".venv")
                    for p in parts
                ):
                    continue
                batch.append(
                    {
                        "path": str(rel),
                        "abs_path": path_str,
                        "action": _ACTION_MAP.get(action, "unknown"),
                    }
                )
            if batch:
                await websocket.send_json({"type": "change", "changes": batch})
    except asyncio.CancelledError:
        pass


@router.websocket("/ws/files/{agent_id}")
async def watch_files(websocket: WebSocket, agent_id: str):
    """Watch file changes in an agent's working directory."""
    await websocket.accept()

    manager = get_manager()
    session = manager._agents.get(agent_id)
    if not session:
        await websocket.send_json(
            {"type": "error", "text": f"Agent not found: {agent_id}"}
        )
        await websocket.close()
        return

    agent = session.agent
    root = getattr(agent, "_working_dir", None)
    if not root:
        await websocket.send_json(
            {"type": "error", "text": "Agent has no working directory"}
        )
        await websocket.close()
        return

    logger.info("File watcher starting", root=str(root), agent_id=agent_id)
    try:
        await _watch_directory(str(root), websocket)
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.error(
            "file watch WS crashed", error=str(e), root=str(root), exc_info=True
        )
        try:
            await websocket.send_json({"type": "error", "text": str(e)})
            await websocket.close()
        except Exception:
            pass
