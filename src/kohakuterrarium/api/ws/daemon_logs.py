"""WebSocket — stream the daemon's ``web.log`` to the UI.

Mounted at ``/ws/daemon/logs``. Equivalent of ``kt serve logs --follow``.

Frames are plain JSON ``{"line": "..."}`` so the client can decorate
without re-parsing the structured logger's bracket prefix. A terminal
``{"status": "closed", "reason": ...}`` is sent before the socket is
closed if a recoverable error short-circuited the stream.

Query params:

- ``follow``  — if ``"true"`` (default), continue tailing after the
  backlog. If ``"false"``, send only the last ``lines`` then close.
- ``lines``   — backlog size in lines (default 500, capped at 5000).
- ``level``   — minimum severity to send (``DEBUG`` / ``INFO`` /
  ``WARNING`` / ``ERROR``). Default ``INFO``.
"""

import asyncio
import json
import re
from pathlib import Path

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

router = APIRouter()


_LEVEL_ORDER = {
    "DEBUG": 10,
    "INFO": 20,
    "WARNING": 30,
    "WARN": 30,
    "ERROR": 40,
    "CRITICAL": 50,
}

_DEFAULT_LOG_PATH = Path.home() / ".kohakuterrarium" / "run" / "web.log"
_LEVEL_REGEX = re.compile(r"\[(DEBUG|INFO|WARNING|WARN|ERROR|CRITICAL)\]")


def _line_level(line: str) -> int:
    m = _LEVEL_REGEX.search(line)
    if not m:
        # Lines without a recognisable level (raw stack traces, blank
        # lines) ride through — treat as INFO so the default filter
        # doesn't silently drop them.
        return 20
    return _LEVEL_ORDER.get(m.group(1), 20)


def _read_backlog(path: Path, lines: int) -> list[str]:
    """Return the last ``lines`` lines of ``path`` (best-effort)."""
    if not path.is_file():
        return []
    try:
        with open(path, "rb") as f:
            # For very small / typical log files, just read all.
            data = f.read()
    except OSError:
        return []
    text = data.decode("utf-8", errors="replace")
    return text.splitlines()[-lines:]


async def _tail(path: Path, send) -> None:
    """Follow ``path`` for new lines until the WS closes.

    Polls every 0.5s — cheap on the local FS and avoids the
    cross-platform headaches of file-watch libraries.
    """
    pos = path.stat().st_size if path.is_file() else 0
    buf = ""
    try:
        while True:
            if path.is_file():
                try:
                    size = path.stat().st_size
                    # Detect rotation / truncation: a smaller-than-pos
                    # file means the operator (or the logging
                    # framework's RotatingFileHandler) replaced it.
                    # Reset so we pick up from the start of the new
                    # file instead of silently dropping everything.
                    if size < pos:
                        pos = 0
                    with open(path, "rb") as f:
                        f.seek(pos)
                        chunk = f.read()
                        pos = f.tell()
                except OSError:
                    chunk = b""
                if chunk:
                    buf += chunk.decode("utf-8", errors="replace")
                    while "\n" in buf:
                        line, _, buf = buf.partition("\n")
                        await send(line)
            await asyncio.sleep(0.5)
    except asyncio.CancelledError:
        raise
    except WebSocketDisconnect:
        return


@router.websocket("/ws/daemon/logs")
async def ws_daemon_logs(ws: WebSocket) -> None:
    await ws.accept()
    q = dict(ws.query_params)
    follow = q.get("follow", "true").lower() in ("1", "true", "yes")
    try:
        lines = max(0, min(5000, int(q.get("lines", "500"))))
    except ValueError:
        lines = 500
    level_name = q.get("level", "INFO").upper()
    min_level = _LEVEL_ORDER.get(level_name, 20)

    # SECURITY: the log path is fixed — clients cannot point this WS at
    # arbitrary files via a query string. Tests monkeypatch the
    # ``_DEFAULT_LOG_PATH`` module-level constant when they need to
    # redirect, which is what the tail-rotation test relies on.
    path = _DEFAULT_LOG_PATH

    async def send_line(line: str) -> None:
        if _line_level(line) < min_level:
            return
        try:
            await ws.send_text(json.dumps({"line": line}))
        except WebSocketDisconnect:
            raise

    try:
        # Backlog first.
        for line in _read_backlog(path, lines):
            await send_line(line)
        if not follow:
            await ws.send_text(
                json.dumps({"status": "closed", "reason": "follow=false"})
            )
            return
        await _tail(path, send_line)
    except WebSocketDisconnect:
        return
    except asyncio.CancelledError:
        return
    except Exception as e:  # pragma: no cover - defensive
        logger.exception("daemon-logs WS crashed")
        try:
            await ws.send_text(json.dumps({"status": "error", "reason": str(e)}))
        except Exception:
            pass
    finally:
        try:
            await ws.close()
        except Exception:  # pragma: no cover - already closed
            pass


__all__ = ["router"]
