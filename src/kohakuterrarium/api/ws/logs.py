"""WebSocket endpoint for tailing the API server's log file.

Each Python process writes its own log file under
``~/.kohakuterrarium/logs/YYYY-MM-DD_HHMMSS_pid<N>_<pwdhash>.log``
(see ``utils/logging.py``). When the web frontend wants a live log
view, it connects to this stream and receives parsed lines as
``{ts, level, module, text}`` JSON messages.

Read-only. No new logging behavior — just a tail on the existing file.
"""

import asyncio
import os
import re
from pathlib import Path

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from kohakuterrarium.utils.logging import DEFAULT_LOG_DIR, get_logger

logger = get_logger(__name__)

router = APIRouter()


# Matches the format produced by ColoredFormatter in utils/logging.py.
# Example line:
#   [12:34:56] [kohakuterrarium.core.agent] [INFO] Starting agent
_LINE_RE = re.compile(
    r"^\[(?P<ts>[^\]]+)\]\s+\[(?P<module>[^\]]+)\]\s+\[(?P<level>[^\]]+)\]\s+(?P<text>.*)$"
)


def _find_current_process_log() -> Path | None:
    """Locate the log file for THIS process by PID match in filename."""
    if not DEFAULT_LOG_DIR.exists():
        return None
    pid = os.getpid()
    marker = f"pid{pid}_"
    candidates = [p for p in DEFAULT_LOG_DIR.glob("*.log") if marker in p.name]
    if not candidates:
        return None
    # If several matches (e.g. PID reuse across days), pick the newest.
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0]


def _parse_line(raw: str) -> dict[str, str]:
    """Parse one log line into a structured dict.

    Malformed lines fall back to ``{level: "unknown", text: <raw>}``.
    """
    m = _LINE_RE.match(raw.rstrip())
    if m is None:
        return {"ts": "", "level": "unknown", "module": "", "text": raw.rstrip()}
    return {
        "ts": m.group("ts"),
        "level": m.group("level").lower(),
        "module": m.group("module"),
        "text": m.group("text"),
    }


async def _tail_file(path: Path, websocket: WebSocket) -> None:
    """Tail ``path`` and push each new line as a JSON frame.

    Exits when the websocket is closed. If the file doesn't exist yet
    it polls for it for up to ten seconds.
    """
    # Wait for the file if it's not there yet.
    for _ in range(20):
        if path.exists():
            break
        await asyncio.sleep(0.5)
    if not path.exists():
        await websocket.send_json(
            {"type": "error", "text": f"log file not found: {path}"}
        )
        return

    fh = open(path, "r", encoding="utf-8", errors="replace")
    try:
        # Seed the stream with the most recent ~200 lines so the client
        # has context without replaying an hour of history.
        fh.seek(0, os.SEEK_END)
        size = fh.tell()
        tail_chunk = 32_768 if size > 32_768 else size
        fh.seek(size - tail_chunk)
        # Drop the first (likely partial) line if we didn't start at 0.
        if size > tail_chunk:
            fh.readline()
        for line in fh.readlines()[-200:]:
            if not line.strip():
                continue
            await websocket.send_json({"type": "line", **_parse_line(line)})
        fh.seek(0, os.SEEK_END)

        # Then follow new lines as they land.
        while True:
            line = fh.readline()
            if line:
                if line.strip():
                    await websocket.send_json({"type": "line", **_parse_line(line)})
                continue
            await asyncio.sleep(0.25)
    finally:
        fh.close()


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
    except Exception:
        logger.debug("log WS error", exc_info=True)
        try:
            await websocket.close()
        except Exception:
            pass
