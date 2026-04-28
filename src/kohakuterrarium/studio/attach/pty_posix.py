"""POSIX PTY session — bridges a forked shell with a WebSocket.

Drains ``api/ws/terminal.py:_pty_session:52`` (the POSIX branch).
Windows fallbacks live in :mod:`pty_windows`; the platform router in
:mod:`pty_router` picks between them.

Wire format (server ↔ client):

    Client → Server: { "type": "input",  "data": "ls\\n" }
    Client → Server: { "type": "resize", "rows": 24, "cols": 80 }
    Server → Client: { "type": "output", "data": "..." }
    Server → Client: { "type": "error",  "data": "..." }
"""

import asyncio
import json
import os
import signal
import struct

from fastapi import WebSocket, WebSocketDisconnect

from kohakuterrarium.studio.attach.pty_router import _find_shell
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


async def pty_session(websocket: WebSocket, cwd: str) -> None:
    """Spawn a POSIX PTY shell and bridge I/O with the WebSocket."""
    import fcntl
    import pty
    import termios

    shell = _find_shell()

    if not os.path.isdir(cwd):
        logger.warning("Terminal cwd does not exist, falling back to home", cwd=cwd)
        cwd = os.path.expanduser("~")

    logger.info("Starting Unix PTY terminal", shell=shell, cwd=cwd)

    master_fd, slave_fd = pty.openpty()

    env = {
        **os.environ,
        "TERM": "xterm-256color",
        "COLORTERM": "truecolor",
    }
    child_pid = os.fork()
    if child_pid == 0:
        # Child — become session leader, attach slave PTY, exec shell.
        try:
            os.setsid()
            os.close(master_fd)
            os.dup2(slave_fd, 0)
            os.dup2(slave_fd, 1)
            os.dup2(slave_fd, 2)
            if slave_fd > 2:
                os.close(slave_fd)
            os.chdir(cwd)
            os.execvpe(shell, [shell, "--login"], env)
        except Exception:
            os._exit(1)

    # Parent — close slave side, keep master.
    os.close(slave_fd)
    loop = asyncio.get_event_loop()

    await websocket.send_json({"type": "output", "data": ""})
    stop = asyncio.Event()

    async def read_pty():
        try:
            while not stop.is_set():
                try:
                    data = await loop.run_in_executor(
                        None, lambda: os.read(master_fd, 4096)
                    )
                except OSError:
                    break
                if not data:
                    break
                await websocket.send_json(
                    {
                        "type": "output",
                        "data": data.decode("utf-8", errors="replace"),
                    }
                )
        except asyncio.CancelledError:
            pass

    async def write_pty():
        try:
            while not stop.is_set():
                raw = await websocket.receive_text()
                msg = json.loads(raw)
                if msg.get("type") == "input" and msg.get("data"):
                    os.write(master_fd, msg["data"].encode("utf-8"))
                elif msg.get("type") == "resize":
                    rows = msg.get("rows", 24)
                    cols = msg.get("cols", 80)
                    winsize = struct.pack("HHHH", rows, cols, 0, 0)
                    fcntl.ioctl(master_fd, termios.TIOCSWINSZ, winsize)
        except WebSocketDisconnect:
            pass
        except asyncio.CancelledError:
            pass

    read_task = asyncio.create_task(read_pty())
    write_task = asyncio.create_task(write_pty())

    _, pending = await asyncio.wait(
        [read_task, write_task], return_when=asyncio.FIRST_COMPLETED
    )
    stop.set()
    for t in pending:
        t.cancel()
    await asyncio.gather(*pending, return_exceptions=True)

    try:
        os.close(master_fd)
    except OSError:
        pass
    try:
        os.kill(child_pid, signal.SIGTERM)
        os.waitpid(child_pid, 0)
    except (OSError, ChildProcessError):
        try:
            os.kill(child_pid, signal.SIGKILL)
            os.waitpid(child_pid, 0)
        except (OSError, ChildProcessError):
            pass
