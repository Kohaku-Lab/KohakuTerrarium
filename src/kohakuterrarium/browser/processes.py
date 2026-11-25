"""Ending a browser's process tree at once, without asking the browser.

Synchronous and bounded: a forced kill must work on a browser that no longer
answers, so nothing here goes through Playwright or CDP.
"""

import ctypes
import functools
import os
import signal
import subprocess
import sys
import weakref

from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

TASKKILL_TIMEOUT_S = 5.0
_WIN_ACCESS = 0x0001 | 0x00100000 | 0x1000
_WAIT_TIMEOUT = 0x102


@functools.cache
def _kernel32():
    """``kernel32`` with handle-sized argument and result types (Windows only)."""
    dll = ctypes.WinDLL("kernel32", use_last_error=True)
    handle = ctypes.c_void_p
    dll.OpenProcess.argtypes = (ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32)
    dll.OpenProcess.restype = handle
    dll.WaitForSingleObject.argtypes = (handle, ctypes.c_uint32)
    dll.WaitForSingleObject.restype = ctypes.c_uint32
    dll.TerminateProcess.argtypes = (handle, ctypes.c_uint32)
    dll.CloseHandle.argtypes = (handle,)
    return dll


def kill_tree(pid: int) -> None:
    """End process ``pid`` and every process it started; never raises.

    On Windows ``taskkill /T /F`` walks the tree; elsewhere the process
    group ``pid`` leads (Playwright starts Chromium as a group leader) and
    then ``pid`` itself get ``SIGKILL``. Call it only for a pid that cannot
    have been reused, as ``BrowserProcesses`` ensures.
    """
    if pid <= 0:
        return
    if sys.platform == "win32":
        _taskkill(pid)
        return
    for kill in (os.killpg, os.kill):
        try:
            kill(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            pass


def _taskkill(pid: int) -> None:
    try:
        subprocess.run(
            ["taskkill", "/T", "/F", "/PID", str(pid)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=TASKKILL_TIMEOUT_S,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.debug("taskkill failed", error=type(exc).__name__)


class BrowserProcesses:
    """A launched browser's process ids (root first), held so a later kill hits only them.

    On Windows an open handle per process stops its id being reused; on
    Linux a pidfd names the exact process. ``kill`` ends the root's tree only
    while the root is still the process that was held, then each held
    process; ``release`` gives the holds back and forgets the pids, as
    garbage collection does.
    """

    def __init__(self, pids: list[int]) -> None:
        self.pids = list(pids)
        self._holds = [_hold(pid) for pid in self.pids]
        self._finalizer = weakref.finalize(self, _drop_all, list(self._holds))

    def kill(self) -> None:
        """End every held process that still runs, and the root's tree."""
        if not self.pids:
            return
        if _still_running(self._holds[0], self.pids[0]):
            kill_tree(self.pids[0])
        for pid, hold in zip(self.pids, self._holds):
            _kill_held(hold, pid)

    def release(self) -> None:
        self.pids = []
        self._holds = []
        self._finalizer()


def _hold(pid: int) -> int | None:
    """An OS handle (Windows) or pidfd (Linux) on ``pid``; ``None`` where neither exists."""
    try:
        if sys.platform == "win32":
            return _kernel32().OpenProcess(_WIN_ACCESS, False, pid) or None
        if hasattr(os, "pidfd_open"):
            return os.pidfd_open(pid)
    except OSError:
        return None
    return None


def _still_running(hold: int | None, pid: int) -> bool:
    """Whether the held root is the same process and still runs."""
    if sys.platform == "win32":
        if hold is None:
            return False
        return _kernel32().WaitForSingleObject(hold, 0) == _WAIT_TIMEOUT
    if hold is not None:
        try:
            signal.pidfd_send_signal(hold, 0)
            return True
        except OSError:
            return False
    try:
        return os.getpgid(pid) == pid
    except OSError:
        return False


def _kill_held(hold: int | None, pid: int) -> None:
    try:
        if sys.platform == "win32":
            if hold is not None:
                _kernel32().TerminateProcess(hold, 1)
        elif hold is not None:
            signal.pidfd_send_signal(hold, signal.SIGKILL)
    except OSError:
        pass


def _drop_all(holds: list[int | None]) -> None:
    for hold in holds:
        _drop(hold)


def _drop(hold: int | None) -> None:
    if hold is None:
        return
    try:
        if sys.platform == "win32":
            _kernel32().CloseHandle(hold)
        else:
            os.close(hold)
    except OSError:
        pass
