"""Ending a browser's process tree at once, without asking the browser, and seeing it end.

Bounded, and never through Playwright or CDP: a forced kill must work on a
browser that no longer answers. The kill itself blocks; ``in_thread`` runs it
off the event loop.
"""

import asyncio
import ctypes
import functools
import os
import signal
import subprocess
import sys
import threading
import weakref
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager

from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

TASKKILL_TIMEOUT_S = 5.0
_WIN_SYNCHRONIZE = 0x00100000
_WIN_ACCESS = 0x0001 | _WIN_SYNCHRONIZE | 0x1000
_WAIT_TIMEOUT = 0x102
_ERROR_INVALID_PARAMETER = 87


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
    then ``pid`` itself get ``SIGKILL``. It acts on the id alone: on Windows
    an open handle on the process (as ``BrowserProcesses`` keeps) stops the
    id being reused meanwhile; on POSIX a pidfd check just before narrows
    that window but cannot close it.
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


class _Finished:
    """An awaitable that is already done."""

    def __await__(self) -> Iterator[None]:
        return iter(())


def in_thread(work: Callable[[], None]) -> Awaitable[None]:
    """Start blocking ``work`` now, off the event loop; await the result to wait for its end.

    With a running loop ``work`` runs on a thread of its own that is not a
    daemon, so a cancelled wait, a closing loop or the interpreter's exit
    leave it to finish. Without a running loop, or when no thread can be
    started, it runs here before this returns. A failure of ``work`` is
    logged, never raised.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        _run_logged(work)
        return _Finished()
    finished = loop.create_future()

    def run() -> None:
        _run_logged(work)
        try:
            loop.call_soon_threadsafe(_settle, finished)
        except RuntimeError:
            pass  # the loop closed; nothing waits any more

    try:
        threading.Thread(target=run, name="browser-kill").start()
    except RuntimeError:
        _run_logged(work)
        finished.set_result(None)
    return finished


def _run_logged(work: Callable[[], None]) -> None:
    try:
        work()
    except Exception as exc:
        logger.warning("Ending browser processes failed", error=type(exc).__name__)


def _settle(finished: asyncio.Future) -> None:
    if not finished.done():
        finished.set_result(None)


class BrowserProcesses:
    """A launched browser's process ids (root first), held so a later kill hits only them.

    On Windows an open handle per process stops its id being reused; on
    Linux a pidfd names the exact process. ``kill`` (once; later calls do
    nothing) ends the root's tree only while the root is still the process
    that was held, then each held process; ``running`` tells whether any may
    still run; ``release`` forgets the pids and gives the holds back, as
    garbage collection does. Any thread may call these; holds a running
    ``kill`` or ``running`` uses stay open until it returns, a ``release``
    meanwhile included.
    """

    def __init__(self, pids: list[int]) -> None:
        self.pids = list(pids)
        self._holds = [_hold(pid) for pid in self.pids]
        self._killed = False
        self._lock = threading.Lock()
        self._users = 0
        self._release_due = False
        self._finalizer = weakref.finalize(self, _drop_all, list(self._holds))

    def kill(self) -> None:
        """End every held process that still runs, and the root's tree; blocks while it does."""
        with self._lock:
            if self._killed:
                return
            self._killed = True
        with self._in_use() as (pids, holds):
            if not pids:
                return
            if _still_running(holds[0], pids[0]):
                kill_tree(pids[0])
            for pid, hold in zip(pids, holds):
                _kill_held(hold, pid)

    def running(self) -> bool:
        """Whether any held process may still run; one that cannot be checked counts."""
        with self._in_use() as (pids, holds):
            return any(_may_run(hold, pid) for pid, hold in zip(pids, holds))

    def held(self) -> bool:
        """Whether every pid has an OS hold (handle or pidfd), so ``kill`` reaches it."""
        with self._lock:
            return all(hold is not None for hold in self._holds)

    def release(self) -> None:
        with self._lock:
            self.pids = []
            self._holds = []
            if self._users:
                self._release_due = True
                return
        self._finalizer()

    @contextmanager
    def _in_use(self) -> Iterator[tuple[list[int], list[int | None]]]:
        """The pids and holds, kept open until the block ends."""
        with self._lock:
            self._users += 1
            pids, holds = list(self.pids), list(self._holds)
        try:
            yield pids, holds
        finally:
            with self._lock:
                self._users -= 1
                drop = self._release_due and not self._users
                if drop:
                    self._release_due = False
            if drop:
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


def _may_run(hold: int | None, pid: int) -> bool:
    """Whether ``pid`` may still run: exact through a hold, else by its id (which may be reused)."""
    if sys.platform == "win32":
        if hold is not None:
            return _kernel32().WaitForSingleObject(hold, 0) == _WAIT_TIMEOUT
        handle = _kernel32().OpenProcess(_WIN_SYNCHRONIZE, False, pid)
        if not handle:
            return ctypes.get_last_error() != _ERROR_INVALID_PARAMETER
        try:
            return _kernel32().WaitForSingleObject(handle, 0) == _WAIT_TIMEOUT
        finally:
            _kernel32().CloseHandle(handle)
    try:
        if hold is not None:
            signal.pidfd_send_signal(hold, 0)
        else:
            os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True
    return not _awaiting_reaping(pid)


def _awaiting_reaping(pid: int) -> bool:
    """Whether ``pid`` has exited and only waits to be reaped (a Linux zombie)."""
    try:
        with open(f"/proc/{pid}/stat", "rb") as stat:
            fields = stat.read().rpartition(b")")[2].split()
    except OSError:
        return False
    return bool(fields) and fields[0] in (b"Z", b"X")


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
