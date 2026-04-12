"""WebSocket PTY terminal endpoint.

Spawns a shell subprocess in the agent's working directory
and bridges stdin/stdout over WebSocket. Protocol:

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

if sys.platform == "win32":
    try:
        from winpty import PTY as _WinPTY
    except ImportError:
        _WinPTY = None
else:
    _WinPTY = None

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from kohakuterrarium.api.deps import get_manager
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

router = APIRouter()


def _find_shell() -> str:
    """Find a suitable shell binary."""
    if sys.platform == "win32":
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
        if _WinPTY is not None:
            await _conpty_session(websocket, cwd)
        else:
            await _pipe_session(websocket, cwd)
        return

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


async def _conpty_session(websocket: WebSocket, cwd: str) -> None:
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
            await _pipe_session(websocket, cwd)
            return
        # Verify the process is actually alive after spawn.
        if not pty.isalive():
            logger.error("ConPTY process died immediately after spawn")
            await _pipe_session(websocket, cwd)
            return
    except Exception as e:
        logger.error("ConPTY spawn failed", error=str(e), exc_info=True)
        await _pipe_session(websocket, cwd)
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


async def _pipe_session(websocket: WebSocket, cwd: str) -> None:
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

    cwd = None
    if hasattr(session.agent, "executor"):
        cwd = getattr(session.agent.executor, "_working_dir", None)
    if not cwd:
        cwd = os.getcwd()
    logger.info("Terminal session", agent_id=agent_id, cwd=str(cwd))

    try:
        await _pty_session(websocket, str(cwd))
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.error("terminal WS error", error=str(e), exc_info=True)
        try:
            await websocket.close()
        except Exception:
            pass
