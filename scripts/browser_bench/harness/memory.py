"""Available physical memory, for the harness's ``--min-free-gb`` start gate.

``free_memory_gb()`` returns GiB, or ``None`` when this machine's memory
cannot be read; it never raises. Readers:

- Windows: ``GlobalMemoryStatusEx`` (``ullAvailPhys``).
- Linux: ``MemAvailable`` from ``/proc/meminfo``, else ``SC_AVPHYS_PAGES``.
- macOS: ``vm_stat`` (free + inactive + speculative pages); macOS has no
  ``SC_AVPHYS_PAGES``. A missing ``vm_stat`` reads as ``None``.
- Anything else: ``SC_AVPHYS_PAGES`` when the platform defines it.

A reading of zero or less is treated as unreadable.
"""

import ctypes
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

GIB = 2**30
MEMINFO = Path("/proc/meminfo")
VM_STAT_TIMEOUT_S = 5.0
VM_STAT_PAGES = ("Pages free", "Pages inactive", "Pages speculative")


class _MemoryStatus(ctypes.Structure):
    _fields_ = [
        ("dwLength", ctypes.c_ulong),
        ("dwMemoryLoad", ctypes.c_ulong),
        ("ullTotalPhys", ctypes.c_ulonglong),
        ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong),
        ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong),
        ("ullAvailVirtual", ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


def windows_free_gb(call: Callable[[Any], int] | None = None) -> float | None:
    """``ullAvailPhys`` from ``GlobalMemoryStatusEx``; ``None`` if the call fails."""
    try:
        if call is None:
            call = ctypes.windll.kernel32.GlobalMemoryStatusEx
        status = _MemoryStatus()
        status.dwLength = ctypes.sizeof(_MemoryStatus)
        if not call(ctypes.byref(status)):
            return None
        return status.ullAvailPhys / GIB
    except (AttributeError, OSError):
        return None


def sysconf_free_gb(
    sysconf: Callable[[str], int] = getattr(os, "sysconf", None),
    names: Mapping[str, int] = getattr(os, "sysconf_names", {}),
) -> float | None:
    """``SC_AVPHYS_PAGES × SC_PAGE_SIZE``; ``None`` where either name is missing."""
    if sysconf is None or "SC_AVPHYS_PAGES" not in names or "SC_PAGE_SIZE" not in names:
        return None
    try:
        return sysconf("SC_AVPHYS_PAGES") * sysconf("SC_PAGE_SIZE") / GIB
    except (OSError, ValueError, TypeError):
        return None


def linux_free_gb(
    meminfo: Path = MEMINFO,
    sysconf: Callable[[str], int] = getattr(os, "sysconf", None),
    names: Mapping[str, int] = getattr(os, "sysconf_names", {}),
) -> float | None:
    """``MemAvailable`` (kB) from ``meminfo``, else the sysconf reading."""
    try:
        text = meminfo.read_text(encoding="utf-8", errors="replace")
    except OSError:
        text = ""
    found = re.search(r"^MemAvailable:\s+(\d+)\s*kB", text, re.M)
    if found:
        return int(found.group(1)) * 1024 / GIB
    return sysconf_free_gb(sysconf, names)


def macos_free_gb(
    run: Callable[..., Any] = subprocess.run,
    which: Callable[[str], str | None] = shutil.which,
) -> float | None:
    """Free, inactive and speculative pages from ``vm_stat``; ``None`` on any failure."""
    tool = which("vm_stat")
    if tool is None:
        return None
    try:
        done = run([tool], capture_output=True, text=True, timeout=VM_STAT_TIMEOUT_S)
    except (OSError, subprocess.SubprocessError):
        return None
    if done.returncode != 0:
        return None
    page = re.search(r"page size of (\d+) bytes", done.stdout)
    if not page:
        return None
    pages = 0
    for label in VM_STAT_PAGES:
        found = re.search(rf"^{label}:\s+(\d+)\.?\s*$", done.stdout, re.M)
        if not found:
            return None
        pages += int(found.group(1))
    return pages * int(page.group(1)) / GIB


def free_memory_gb(platform: str = sys.platform) -> float | None:
    """Available physical memory in GiB, or ``None`` when it cannot be read."""
    if platform == "win32":
        reader = windows_free_gb
    elif platform.startswith("linux"):
        reader = linux_free_gb
    elif platform == "darwin":
        reader = macos_free_gb
    else:
        reader = sysconf_free_gb
    try:
        free = reader()
    except Exception:
        return None
    return float(free) if isinstance(free, (int, float)) and free > 0 else None
