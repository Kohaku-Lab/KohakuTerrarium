"""Platform router for the PTY attach.

Drains ``api/ws/terminal.py:_find_shell:38``, ``_session_cwd:323``,
and the dispatch logic of the two ``@router.websocket`` handlers
(``terminal_ws:331``, ``terminal_terrarium_ws:360``) — selecting
between the POSIX and Windows PTY backends based on
``sys.platform``.

Used by ``api/ws/pty.py`` (the thin WS shell) once the session/cwd
have been resolved against the manager.
"""

import os
import shutil
import sys

from fastapi import WebSocket


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


def _session_cwd(holder) -> str:
    """Resolve a creature's (or legacy AgentSession's) working directory.

    ``holder`` is anything that exposes ``.agent`` — both
    :class:`Creature` instances (engine-backed) and the historical
    ``AgentSession`` shape work.  Falls back to the server CWD if the
    executor does not advertise a ``_working_dir`` attribute.
    """
    cwd = None
    if hasattr(holder.agent, "executor"):
        cwd = getattr(holder.agent.executor, "_working_dir", None)
    return str(cwd or os.getcwd())


async def pty_session(websocket: WebSocket, cwd: str) -> None:
    """Spawn a PTY shell and bridge I/O with the WebSocket.

    Routes to :mod:`pty_posix` on POSIX and to :mod:`pty_windows` on
    Windows — preferring ConPTY when winpty is available, otherwise
    a plain subprocess-pipe fallback.
    """
    if sys.platform == "win32":
        from kohakuterrarium.studio.attach import pty_windows

        if pty_windows.has_conpty():
            await pty_windows.conpty_session(websocket, cwd)
        else:
            await pty_windows.pipe_session(websocket, cwd)
        return

    from kohakuterrarium.studio.attach import pty_posix

    await pty_posix.pty_session(websocket, cwd)
