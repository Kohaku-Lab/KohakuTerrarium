"""Expose host or worker environment defaults and diagnostic metadata.

Worker targets prefer their home directory because a worker process may inherit
the host's cwd, which is not a meaningful default workspace for that node.
"""

import os
import platform
import sys
import time
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version
from typing import Any

from fastapi import APIRouter, Depends, Request

from kohakuterrarium.api.deps import get_service
from kohakuterrarium.launcher.migration import is_launcher_install
from kohakuterrarium.utils.config_dir import config_dir
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

router = APIRouter()


# Import time is the stable baseline for daemon uptime.
_PROCESS_START = time.time()


def _get_version() -> str:
    try:
        return _pkg_version("kohakuterrarium")
    except PackageNotFoundError:
        return "unknown"


def _install_kind() -> str:
    """Classify the installation as launcher-managed or user-managed."""
    try:
        return "launcher" if is_launcher_install() else "user"
    except OSError:
        return "user"


@router.get("")
async def server_info(
    on_node: str | None = None,
    service: Any = Depends(get_service),
) -> dict[str, str]:
    """Return server environment info (cwd, platform, etc.).

    With ``on_node`` set to a connected worker's name, asks that
    worker for its default working directory via the
    ``terrarium.files`` adapter and returns the worker-side path
    instead of the host's cwd.
    """
    if on_node and on_node != "_host" and hasattr(service, "default_workdir"):
        try:
            info = await service.default_workdir(on_node)
        except KeyError:
            # A stale worker selection should still yield a usable host default.
            logger.warning(
                "server-info on_node=%r is not a connected worker; "
                "falling back to host cwd",
                on_node,
            )
        except Exception:
            logger.exception(
                "server-info default_workdir failed for on_node=%r", on_node
            )
        else:
            # A worker may inherit the host cwd, so its home is the safer workspace default.
            cwd = info.get("home") or info.get("cwd") or ""
            return {
                "cwd": cwd,
                "platform": info.get("platform") or sys.platform,
            }
    return {
        "cwd": os.getcwd(),
        "platform": sys.platform,
    }


@router.get("/diagnostics")
async def diagnostics(request: Request) -> dict[str, Any]:
    """Return a paste-ready snapshot of runtime, paths, and daemon state."""
    home = config_dir()
    python_impl = platform.python_implementation()
    arch = platform.machine() or "unknown"
    bits = "64-bit" if sys.maxsize > 2**32 else "32-bit"
    return {
        "version": _get_version(),
        "python": {
            "version": platform.python_version(),
            "implementation": python_impl,
            "bits": bits,
        },
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": arch,
            "raw": sys.platform,
        },
        "install_kind": _install_kind(),
        "paths": {
            "home": str(home),
            "sessions": str(home / "sessions"),
            "packages": str(home / "packages"),
            "logs": str(home / "logs"),
            "runtime": str(home / "runtime"),
            "venv": str(home / "runtime" / "venv"),
        },
        "daemon": {
            "pid": os.getpid(),
            "uptime_seconds": int(time.time() - _PROCESS_START),
            "mode": getattr(request.app.state, "lab_mode", "standalone"),
            "lab_bind": getattr(request.app.state, "lab_bind", ""),
        },
    }
