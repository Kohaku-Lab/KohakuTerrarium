"""Locate a Python interpreter that child processes can run.

``sys.executable`` is not always an interpreter. In the embedded desktop stub it
is the application executable, which cannot run ``-c`` or ``-m`` by itself. The
stub accepts a dedicated flag that turns it into one (see
``launcher/interpreter_mode.py``).

This module imports only the standard library and the launcher flag constants so
it is safe to import from any layer.
"""

import os
import sys
from pathlib import Path

from kohakuterrarium.launcher.interpreter_mode import FLAG as STUB_FLAG
from kohakuterrarium.launcher.interpreter_mode import STUB_ENV

PYTHON_ENV = "KT_PYTHON"


def executable_is_stub() -> bool:
    """Return whether ``sys.executable`` is the embedded application stub."""
    if os.environ.get(STUB_ENV) == sys.executable:
        return True
    try:
        return any(Path(sys.executable).resolve().parent.glob("python3*._pth"))
    except OSError:
        return False


def is_briefcase_runtime() -> bool:
    """Return whether the process runs under the desktop launcher.

    True for the embedded stub and for a real interpreter started by the launcher
    handoff (``KT_LAUNCHER_EXEC=1``).
    """
    return os.environ.get("KT_LAUNCHER_EXEC") == "1" or executable_is_stub()


def python_command() -> list[str]:
    """Return the argv prefix that runs a Python interpreter.

    ``KT_PYTHON`` wins so a shell that bundles an interpreter can name it. The
    embedded stub is addressed through its interpreter flag.
    """
    override = os.environ.get(PYTHON_ENV)
    if override:
        return [override]
    if executable_is_stub():
        return [sys.executable, STUB_FLAG]
    return [sys.executable]
