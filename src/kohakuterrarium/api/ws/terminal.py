"""WebSocket PTY terminal endpoint.

Spawns a shell subprocess (bash/sh) in the agent's working directory
and bridges stdin/stdout over WebSocket. Protocol matches KohakuRiver:

  Client → Server: { "type": "input", "data": "ls\n" }
  Client → Server: { "type": "resize", "rows": 24, "cols": 80 }
  Server → Client: { "type": "output", "data": "..." }
  Server → Client: { "type": "error", "data": "..." }
"""

import asyncio
import json
import os
import shutil
import signal
import struct
import sys

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from kohakuterrarium.api.deps import get_manager
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

router = APIRouter()


def _find_shell() -> str | list[str]:
    """Find a suitable shell binary. Returns path or [cmd, args] list."""
    if sys.platform == "win32":
        # Prefer PowerShell, fall back to cmd.exe
        pwsh = shutil.which("pwsh") or shutil.which("powershell")
        if pwsh:
            return pwsh
        return os.environ.get("COMSPEC", "cmd.exe")
    for sh in ("bash", "sh", "zsh"):
        path = shutil.which(sh)
        if path:
            return path
    return "sh"


async def _pty_session(websocket: WebSocket, cwd: str) -> None:
    """Spawn a PTY shell and bridge I/O with the WebSocket."""
    if sys.platform == "win32":
        await _subprocess_session(websocket, cwd)
        return

    import fcntl
    import pty
    import termios

    shell = _find_shell()
    pid, fd = pty.openpty()

    env = {**os.environ, "TERM": "xterm-256color"}
    child_pid = os.fork()
    if child_pid == 0:
        # Child process — become session leader, open slave PTY, exec shell.
        os.setsid()
        os.close(pid)
        slave_fd = os.open(os.ttyname(fd), os.O_RDWR)
        os.dup2(slave_fd, 0)
        os.dup2(slave_fd, 1)
        os.dup2(slave_fd, 2)
        os.close(slave_fd)
        os.close(fd)
        os.chdir(cwd)
        os.execvpe(shell, [shell, "--login"], env)

    # Parent — close slave side
    os.close(fd)
    master_fd = pid
    loop = asyncio.get_event_loop()

    await websocket.send_json({"type": "output", "data": ""})

    stop = asyncio.Event()

    async def read_pty():
        """Read from PTY master and send to WebSocket."""
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
        """Read from WebSocket and write to PTY master."""
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

    # Cleanup
    try:
        os.close(master_fd)
    except OSError:
        pass
    try:
        os.kill(child_pid, signal.SIGTERM)
        os.waitpid(child_pid, os.WNOHANG)
    except (OSError, ChildProcessError):
        pass


async def _subprocess_session(websocket: WebSocket, cwd: str) -> None:
    """Fallback for Windows — uses asyncio subprocess (no PTY)."""
    shell = _find_shell()
    logger.info("Starting subprocess terminal", shell=shell, cwd=cwd)
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
                    {
                        "type": "output",
                        "data": data.decode("utf-8", errors="replace"),
                    }
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


@router.websocket("/ws/terminal/{agent_id}")
async def terminal_ws(websocket: WebSocket, agent_id: str):
    """Interactive terminal in the agent's working directory."""
    await websocket.accept()

    manager = get_manager()
    session = manager._agents.get(agent_id)
    if not session:
        await websocket.send_json(
            {"type": "error", "data": f"Agent not found: {agent_id}"}
        )
        await websocket.close()
        return

    cwd = getattr(session.agent, "_working_dir", None) or os.getcwd()

    try:
        await _pty_session(websocket, str(cwd))
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.debug("terminal WS error", exc_info=True)
        try:
            await websocket.close()
        except Exception:
            pass
