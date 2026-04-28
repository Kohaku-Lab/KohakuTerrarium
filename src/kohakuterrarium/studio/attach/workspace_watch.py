"""Workspace files live watcher (filesystem changes over WebSocket).

Drains the body of ``api/ws/files.py`` (110 LoC). The thin WS shell
in ``api/ws/files.py`` resolves the working directory of the agent
(or terrarium creature) and hands off to :func:`watch_directory`
here, which owns the ``watchfiles.awatch`` loop.

Wire format (server → client):

    { "type": "ready",  "root": "..." }
    { "type": "change", "changes": [{"path": "...", "action": "..."}] }
    { "type": "error",  "text": "..." }
"""

import asyncio
from pathlib import Path

from fastapi import WebSocket

from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

_ACTION_MAP = {1: "added", 2: "modified", 3: "deleted"}


async def watch_directory(root: str, websocket: WebSocket) -> None:
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
