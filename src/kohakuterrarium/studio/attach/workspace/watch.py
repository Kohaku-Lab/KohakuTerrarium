"""Stream visible workspace filesystem changes over a websocket.

The server emits ``ready``, batched ``change``, and ``error`` frames. Change entries
include relative and absolute paths plus a normalized action name.
"""

import asyncio
from pathlib import Path

from fastapi import WebSocket

from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

_ACTION_MAP = {1: "added", 2: "modified", 3: "deleted"}


async def watch_directory(root: str, websocket: WebSocket) -> None:
    """Watch a directory recursively and send filtered change batches."""
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
                # Generated and hidden paths are not actionable in workspace navigation.
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
