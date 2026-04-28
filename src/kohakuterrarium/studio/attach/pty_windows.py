"""Windows PTY (ConPTY via winpty) session + pipe fallback.

Drains ``api/ws/terminal.py:_conpty_session:166`` and
``_pipe_session:267``. The platform router in :mod:`pty_router`
prefers ConPTY when ``winpty.PTY`` imports successfully and falls
back to plain subprocess pipes otherwise.
"""

import asyncio
import json
import os
import sys

from fastapi import WebSocket, WebSocketDisconnect

from kohakuterrarium.studio.attach.pty_router import _find_shell
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

if sys.platform == "win32":
    try:
        from winpty import PTY as _WinPTY
    except ImportError:
        _WinPTY = None
else:
    _WinPTY = None


async def conpty_session(websocket: WebSocket, cwd: str) -> None:
    """Windows ConPTY session via native winpty.PTY.

    Uses the low-level PTY class directly for reliable I/O.
    ConPTY handles line ending translation, ANSI escapes, and
    terminal emulation — we just forward raw bytes.
    """
    shell = _find_shell()

    # Validate cwd exists — invalid path crashes the spawned process.
    if not os.path.isdir(cwd):
        logger.warning("Terminal cwd does not exist, falling back to home", cwd=cwd)
        cwd = os.path.expanduser("~")

    logger.info("Starting ConPTY terminal", shell=shell, cwd=cwd)

    os.environ.setdefault("TERM", "xterm-256color")
    os.environ.setdefault("COLORTERM", "truecolor")

    try:
        pty = _WinPTY(80, 24)
        spawned = pty.spawn(shell, cwd=cwd)
        if not spawned:
            logger.error("ConPTY spawn returned False")
            await pipe_session(websocket, cwd)
            return
        # Verify the process is actually alive after spawn.
        if not pty.isalive():
            logger.error("ConPTY process died immediately after spawn")
            await pipe_session(websocket, cwd)
            return
    except Exception as e:
        logger.error("ConPTY spawn failed", error=str(e), exc_info=True)
        await pipe_session(websocket, cwd)
        return

    logger.info("ConPTY spawned", pid=pty.pid)

    await websocket.send_json({"type": "output", "data": ""})
    stop = asyncio.Event()
    loop = asyncio.get_event_loop()

    def _blocking_read():
        """Blocking read in thread — returns str or None on EOF/error."""
        try:
            return pty.read(blocking=True)
        except Exception:
            return None

    async def read_pty():
        try:
            while not stop.is_set():
                if not pty.isalive() and pty.iseof():
                    break
                data = await loop.run_in_executor(None, _blocking_read)
                if data is None:
                    break
                if data:
                    await websocket.send_json({"type": "output", "data": data})
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error("ConPTY read error", error=str(e), exc_info=True)

    async def write_pty():
        try:
            while not stop.is_set():
                raw = await websocket.receive_text()
                msg = json.loads(raw)
                if msg.get("type") == "input" and msg.get("data"):
                    pty.write(msg["data"])
                elif msg.get("type") == "resize":
                    cols = int(msg.get("cols", 80))
                    rows = int(msg.get("rows", 24))
                    pty.set_size(cols, rows)
        except WebSocketDisconnect:
            pass
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error("ConPTY write error", error=str(e), exc_info=True)

    read_task = asyncio.create_task(read_pty())
    write_task = asyncio.create_task(write_pty())
    _, pending = await asyncio.wait(
        [read_task, write_task], return_when=asyncio.FIRST_COMPLETED
    )
    stop.set()

    # Kill the shell so the blocking read thread unblocks and exits.
    try:
        pty.cancel_io()
    except Exception:
        pass

    for t in pending:
        t.cancel()
    await asyncio.gather(*pending, return_exceptions=True)
    logger.info("ConPTY session ended")


async def pipe_session(websocket: WebSocket, cwd: str) -> None:
    """Fallback: plain subprocess pipes (no PTY)."""
    shell = _find_shell()
    logger.warning("Using pipe session (no PTY)", shell=shell)
    proc = await asyncio.create_subprocess_exec(
        shell,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        cwd=cwd,
    )

    await websocket.send_json({"type": "output", "data": ""})
    stop = asyncio.Event()

    async def read_out():
        try:
            while not stop.is_set():
                data = await proc.stdout.read(4096)
                if not data:
                    break
                await websocket.send_json(
                    {"type": "output", "data": data.decode("utf-8", errors="replace")}
                )
        except asyncio.CancelledError:
            pass

    async def write_in():
        try:
            while not stop.is_set():
                raw = await websocket.receive_text()
                msg = json.loads(raw)
                if msg.get("type") == "input" and msg.get("data"):
                    proc.stdin.write(msg["data"].encode("utf-8"))
                    await proc.stdin.drain()
        except WebSocketDisconnect:
            pass
        except asyncio.CancelledError:
            pass

    read_task = asyncio.create_task(read_out())
    write_task = asyncio.create_task(write_in())
    _, pending = await asyncio.wait(
        [read_task, write_task], return_when=asyncio.FIRST_COMPLETED
    )
    stop.set()
    for t in pending:
        t.cancel()
    await asyncio.gather(*pending, return_exceptions=True)

    try:
        proc.terminate()
    except ProcessLookupError:
        pass


def has_conpty() -> bool:
    """Return True if winpty is available for ConPTY use."""
    return _WinPTY is not None
