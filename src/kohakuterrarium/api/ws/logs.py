"""WebSocket endpoint for tailing the API server's log file.

Thin shell over :mod:`kohakuterrarium.studio.attach.log`. The URL
``/ws/logs`` is preserved to keep the existing frontend
``useLogStream`` composable working without changes.

The helpers (:func:`_find_current_process_log`, :func:`_tail_file`) are
re-exported at module scope so test code can monkeypatch them on this
module the same way it has historically. The route body is intentionally
small — all real work lives in the studio attach layer.
"""

import os

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from kohakuterrarium.studio.attach.log import (
    _find_current_process_log,
    _tail_file,
)
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

router = APIRouter()


@router.websocket("/ws/logs")
async def tail_logs(websocket: WebSocket):
    """Live tail of the current API server process log file."""
    await websocket.accept()
    path = _find_current_process_log()
    if path is None:
        await websocket.send_json(
            {"type": "error", "text": "no log file found for current process"}
        )
        await websocket.close()
        return

    await websocket.send_json({"type": "meta", "path": str(path), "pid": os.getpid()})

    try:
        await _tail_file(path, websocket)
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.debug("log WS error", error=str(e), exc_info=True)
        try:
            await websocket.send_json({"type": "error", "text": str(e)})
        except Exception:
            pass
        try:
            await websocket.close()
        except Exception as e:
            logger.debug("Failed to close log WS", error=str(e), exc_info=True)
