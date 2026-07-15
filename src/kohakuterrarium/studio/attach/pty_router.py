"""Resolve shell settings and dispatch PTY attachments by platform."""

import os
import shutil
import sys

from fastapi import WebSocket


def _find_shell() -> str:
    """Return the preferred available shell for the current platform."""
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


def session_cwd(holder) -> str:
    """Resolve the working directory from any holder exposing ``.agent``.

    Creature and legacy session holders share this shape. The server working
    directory is the fallback when the executor does not advertise one.
    """
    cwd = None
    if hasattr(holder.agent, "executor"):
        cwd = getattr(holder.agent.executor, "_working_dir", None)
    return str(cwd or os.getcwd())


async def pty_session(websocket: WebSocket, cwd: str) -> None:
    """Bridge a shell to the websocket using the platform-specific backend.

    Windows prefers ConPTY when available and otherwise uses subprocess pipes;
    POSIX systems use the native PTY implementation.
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
