"""Runtime helpers for the rich CLI Application — output backend, task
spawner with exception trapping, and stderr-to-log redirection."""

import asyncio
import os
import sys

from prompt_toolkit.output import Output
from prompt_toolkit.output.vt100 import Vt100_Output

from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


class StderrToLogger:
    """Redirect stderr lines to logging while the live terminal UI is active."""

    def __init__(self):
        self._buf: str = ""

    def write(self, s: str) -> int:
        if not s:
            return 0
        self._buf += s
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            line = line.rstrip()
            if line:
                logger.error("[stderr] %s", line)
        return len(s)

    def flush(self) -> None:
        if self._buf.strip():
            logger.error("[stderr] %s", self._buf.rstrip())
            self._buf = ""

    def isatty(self) -> bool:
        return False

    def fileno(self) -> int:
        raise OSError("no fileno")


def spawn(coro) -> asyncio.Task:
    """Create a background task and log unobserved exceptions."""
    task = asyncio.create_task(coro)

    def _done(t: asyncio.Task) -> None:
        if t.cancelled():
            return
        exc = t.exception()
        if exc is not None:
            logger.error("background task failed", exc_info=exc)

    task.add_done_callback(_done)
    return task


# Escape sequences for enabling/disabling enhanced keyboard reporting.
# We send BOTH protocols at startup — terminals that don't understand
# one will silently ignore it.
#
# - ``ESC [ > 4 ; 2 m``  — xterm modifyOtherKeys=2 (Windows Terminal,
#   xterm, gnome-terminal). When enabled, Shift+Enter / Ctrl+Enter emit
#   ``ESC [ 27 ; <mod> ; 13 ~`` instead of plain ``\\r``.
# - ``ESC [ > 1 u``       — kitty keyboard protocol "report event types".
#   Used by kitty, foot, alacritty (with the protocol enabled), and
#   recent Windows Terminal builds. Emits ``ESC [ 13 ; <mod> u``.
ENABLE_ENHANCED_KEYBOARD = "\x1b[>4;2m\x1b[>1u"
DISABLE_ENHANCED_KEYBOARD = "\x1b[>4;0m\x1b[<u"


def enable_enhanced_keyboard() -> None:
    """Request distinct modified-Enter sequences when the terminal supports them."""
    try:
        sys.stdout.write(ENABLE_ENHANCED_KEYBOARD)
        sys.stdout.flush()
    except Exception as e:
        logger.debug("Failed to enable enhanced keyboard", error=str(e))


def disable_enhanced_keyboard() -> None:
    """Restore the terminal's default keyboard reporting."""
    try:
        sys.stdout.write(DISABLE_ENHANCED_KEYBOARD)
        sys.stdout.flush()
    except Exception as e:
        logger.debug("Failed to disable enhanced keyboard", error=str(e))


def make_output() -> Output | None:
    """Use VT100 output for Windows terminals that expose an ANSI-style PTY."""
    if sys.platform != "win32":
        return None
    term = os.environ.get("TERM", "")
    is_xterm_pty = term.startswith(("xterm", "screen", "tmux", "rxvt", "vt"))
    is_modern_term = bool(os.environ.get("WT_SESSION")) or bool(
        os.environ.get("COLORTERM")
    )
    if not (is_xterm_pty or is_modern_term):
        return None
    try:
        return Vt100_Output.from_pty(sys.stdout, term=term or "xterm-256color")
    except Exception as e:
        logger.debug("Vt100 output creation failed", error=str(e))
        return None
