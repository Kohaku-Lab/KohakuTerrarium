"""Bridge a forked POSIX shell and websocket through a pseudo-terminal.

Client frames provide terminal input or dimensions; server frames carry decoded
terminal output or errors.
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


async def _wait_ready(fd: int, *, writable: bool = False) -> None:
    """Wait for a nonblocking descriptor to become ready."""
    loop = asyncio.get_running_loop()
    ready = loop.create_future()

    def wake() -> None:
        if not ready.done():
            ready.set_result(None)

    add = loop.add_writer if writable else loop.add_reader
    remove = loop.remove_writer if writable else loop.remove_reader
    add(fd, wake)
    try:
        await ready
    finally:
        remove(fd)


async def _read_fd(fd: int) -> bytes:
    """Read available terminal output without occupying a worker thread."""
    while True:
        try:
            return os.read(fd, 4096)
        except BlockingIOError:
            await _wait_ready(fd)


async def _write_fd(fd: int, data: bytes) -> None:
    """Write all terminal input, awaiting capacity after partial writes."""
    remaining = memoryview(data)
    while remaining:
        try:
            written = os.write(fd, remaining)
        except BlockingIOError:
            await _wait_ready(fd, writable=True)
        else:
            if written == 0:
                raise OSError("Terminal write made no progress")
            remaining = remaining[written:]


async def _reap_child(child_pid: int) -> None:
    """Terminate and reap the shell without blocking the event loop."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 1.0
    sent_term = sent_kill = False
    while True:
        try:
            if os.waitpid(child_pid, os.WNOHANG)[0]:
                return
        except ChildProcessError:
            return
        try:
            if not sent_term:
                os.kill(child_pid, signal.SIGTERM)
                sent_term = True
            elif not sent_kill and loop.time() >= deadline:
                os.kill(child_pid, signal.SIGKILL)
                sent_kill = True
        except ProcessLookupError:
            pass
        await asyncio.sleep(0.02)


async def _cleanup(master_fd: int, child_pid: int, tasks: list[asyncio.Task]) -> None:
    """Stop descriptor users, close the terminal, and reap its shell."""
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    try:
        os.close(master_fd)
    finally:
        await _reap_child(child_pid)


async def pty_session(websocket: WebSocket, cwd: str) -> None:
    """Run a login shell until either the PTY or websocket side closes."""
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
    try:
        child_pid = os.fork()
    except BaseException:
        os.close(master_fd)
        os.close(slave_fd)
        raise
    if child_pid == 0:
        # A session-leading child must bind all standard streams to the slave PTY.
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

    # The parent owns only the master endpoint used for asynchronous bridging.
    os.close(slave_fd)

    async def read_pty():
        while True:
            try:
                data = await _read_fd(master_fd)
            except OSError:
                return
            if not data:
                return
            await websocket.send_json(
                {"type": "output", "data": data.decode("utf-8", errors="replace")}
            )

    async def write_pty():
        try:
            while True:
                raw = await websocket.receive_text()
                msg = json.loads(raw)
                if msg.get("type") == "input" and msg.get("data"):
                    await _write_fd(master_fd, msg["data"].encode("utf-8"))
                elif msg.get("type") == "resize":
                    rows = msg.get("rows", 24)
                    cols = msg.get("cols", 80)
                    winsize = struct.pack("HHHH", rows, cols, 0, 0)
                    fcntl.ioctl(master_fd, termios.TIOCSWINSZ, winsize)
        except WebSocketDisconnect:
            pass

    tasks = []
    try:
        os.set_blocking(master_fd, False)
        await websocket.send_json({"type": "output", "data": ""})
        tasks = [asyncio.create_task(read_pty()), asyncio.create_task(write_pty())]
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            task.result()
    finally:
        cleanup = asyncio.create_task(_cleanup(master_fd, child_pid, tasks))
        cancelled = None
        while not cleanup.done():
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError as exc:
                cancelled = exc
        cleanup.result()
        if cancelled is not None:
            raise cancelled
