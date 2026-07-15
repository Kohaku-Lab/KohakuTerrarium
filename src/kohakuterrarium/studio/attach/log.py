"""Expose the current process log as structured websocket frames.

The attachment is read-only and independent of the Terrarium engine. Existing log
lines are parsed into ``{ts, level, module, text}`` records before transmission.
"""

import asyncio
import os
import re
from pathlib import Path

from fastapi import WebSocket, WebSocketDisconnect

from kohakuterrarium.utils.logging import DEFAULT_LOG_DIR, get_logger

logger = get_logger(__name__)


# ColoredFormatter emits ``[time] [module] [level] message`` before file output.
_LINE_RE = re.compile(
    r"^\[(?P<ts>[^\]]+)\]\s+\[(?P<module>[^\]]+)\]\s+\[(?P<level>[^\]]+)\]\s+(?P<text>.*)$"
)


def _find_current_process_log() -> Path | None:
    """Locate the newest log file whose filename identifies this process."""
    if not DEFAULT_LOG_DIR.exists():
        return None
    pid = os.getpid()
    marker = f"pid{pid}_"
    candidates = [p for p in DEFAULT_LOG_DIR.glob("*.log") if marker in p.name]
    if not candidates:
        return None
    # PID reuse can leave older matches, so modification time breaks the tie.
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0]


def _parse_line(raw: str) -> dict[str, str]:
    """Parse one log line, preserving malformed input as unknown-level text."""
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
    """Send recent context and then follow new log lines until disconnection.

    A newly configured logger may not have created the file yet, so attachment waits
    for up to ten seconds before reporting it missing.
    """
    # Logger initialization may race the websocket attachment.
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
        # Recent context is bounded so attachment cost does not scale with log age.
        fh.seek(0, os.SEEK_END)
        size = fh.tell()
        tail_chunk = 32_768 if size > 32_768 else size
        fh.seek(size - tail_chunk)
        # A chunk starting mid-file usually begins inside a partial line.
        if size > tail_chunk:
            fh.readline()
        for line in fh.readlines()[-200:]:
            if not line.strip():
                continue
            await websocket.send_json({"type": "line", **_parse_line(line)})
        fh.seek(0, os.SEEK_END)

        # EOF is temporary because the process continues appending to the file.
        while True:
            line = fh.readline()
            if line:
                if line.strip():
                    await websocket.send_json({"type": "line", **_parse_line(line)})
                continue
            await asyncio.sleep(0.25)
    finally:
        fh.close()


async def run_log_attach(websocket: WebSocket) -> None:
    """Attach the websocket to the current server process log."""
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
        logger.warning("log WS error", error=str(e), exc_info=True)
        try:
            await websocket.send_json({"type": "error", "text": str(e)})
        except Exception:
            pass
        try:
            await websocket.close()
        except Exception as e:
            logger.warning("Failed to close log WS", error=str(e), exc_info=True)
