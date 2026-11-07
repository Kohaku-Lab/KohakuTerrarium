"""The launcher in a terminal: the wrappers ``kt shims`` writes and the one-line installers
start it with ``KT_LAUNCHER_TERMINAL=1``.

There, installs and updates report progress as text lines on stderr (no splash window), and
the launcher's own log keeps to warnings on stderr (everything still goes to its log file).
"""

import os
import sys

TERMINAL_ENV = "KT_LAUNCHER_TERMINAL"


def in_terminal() -> bool:
    """Started from a terminal wrapper or installer."""
    return os.environ.get(TERMINAL_ENV) == "1"


class TerminalProgress:
    """A ``ProgressCallback`` printing one line per phase, and the percent as it grows
    (at most every 10%) so a pipe or log stays short."""

    def __init__(self, stream=None) -> None:
        self.stream = stream or sys.stderr
        self.phase = ""
        self.shown = -10.0

    def __call__(self, phase: str, percent: float, message: str) -> None:
        if phase != self.phase:
            self.phase, self.shown = phase, -10.0
        if percent - self.shown < 10 and percent < 100:
            return
        self.shown = percent
        text = f"  {phase:<10} {percent:5.1f}%  {message}".rstrip()
        self.stream.write(text + "\n")
        self.stream.flush()


__all__ = ["TERMINAL_ENV", "TerminalProgress", "in_terminal"]
