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
import codecs
import json
import re
from pathlib import Path

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from kohakuterrarium.api.auth.ws_auth import accept_with_auth_echo

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
    """Return the numeric severity encoded in a structured log line."""
    m = _LEVEL_REGEX.search(line)
    if not m:
        # Stack-trace continuations and unstructured lines should remain visible
        # under the default filter, so they inherit INFO severity.
        return 20
    return _LEVEL_ORDER.get(m.group(1), 20)


_READ_CHUNK_BYTES = 64 * 1024


def _read_backlog(path: Path, lines: int) -> list[str]:
    """Read a suffix in bounded chunks, preserving splitlines semantics."""
    if lines <= 0:
        return []
    try:
        with open(path, "rb") as f:
            pos = f.seek(0, 2)
            chunks = []
            newlines = 0
            # One extra newline excludes the first, potentially partial line.
            while pos and newlines <= lines:
                size = min(pos, _READ_CHUNK_BYTES)
                pos -= size
                f.seek(pos)
                chunk = f.read(size)
                chunks.append(chunk)
                newlines += chunk.count(b"\n")
    except OSError:
        return []
    return (
        b"".join(reversed(chunks))
        .decode("utf-8", errors="replace")
        .splitlines()[-lines:]
    )


def _log_size(path: Path) -> int:
    """Return the current end offset, tolerating missing log files."""
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _read_chunk(path: Path, pos: int) -> tuple[bytes, int, bool]:
    """Read at most one chunk; signal a truncation to reset partial text."""
    try:
        with open(path, "rb") as f:
            reset = f.seek(0, 2) < pos
            if reset:
                pos = 0
            f.seek(pos)
            chunk = f.read(_READ_CHUNK_BYTES)
            return chunk, pos + len(chunk), reset
    except OSError:
        return b"", pos, False


async def _tail(path: Path, send) -> None:
    """Poll for complete new lines, performing disk work off the event loop."""
    pos = await asyncio.to_thread(_log_size, path)
    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
    buf = ""
    try:
        while True:
            chunk, pos, reset = await asyncio.to_thread(_read_chunk, path, pos)
            if reset:
                decoder.reset()
                buf = ""
            if chunk:
                buf += decoder.decode(chunk)
                complete = buf.split("\n")
                buf = complete.pop()
                for line in complete:
                    await send(line)
            # Drain bursts promptly; each read still yields to the event loop.
            if len(chunk) < _READ_CHUNK_BYTES:
                await asyncio.sleep(0.5)
    except asyncio.CancelledError:
        raise
    except WebSocketDisconnect:
        return


@router.websocket("/ws/daemon/logs")
async def ws_daemon_logs(ws: WebSocket) -> None:
    """Send a filtered log backlog and optionally follow new entries."""
    await accept_with_auth_echo(ws)
    q = dict(ws.query_params)
    follow = q.get("follow", "true").lower() in ("1", "true", "yes")
    try:
        lines = max(0, min(5000, int(q.get("lines", "500"))))
    except ValueError:
        lines = 500
    level_name = q.get("level", "INFO").upper()
    min_level = _LEVEL_ORDER.get(level_name, 20)

    # The fixed path prevents clients from using this endpoint to read arbitrary
    # files; tests may replace the module constant without exposing a query input.
    path = _DEFAULT_LOG_PATH

    async def send_line(line: str) -> None:
        """Send a line when it satisfies the requested severity threshold."""
        if _line_level(line) < min_level:
            return
        try:
            await ws.send_text(json.dumps({"line": line}))
        except WebSocketDisconnect:
            raise

    try:
        for line in await asyncio.to_thread(_read_backlog, path, lines):
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
