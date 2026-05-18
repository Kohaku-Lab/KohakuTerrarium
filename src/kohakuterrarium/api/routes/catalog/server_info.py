"""Catalog server-info — runtime environment introspection.

Replaces ``api.routes.configs.server_info``.

The endpoint optionally accepts an ``on_node`` query parameter so the
New Creature / New Terrarium modal can ask for a worker-side default
working directory when the user picks a worker as the spawn target
(B5).  Standalone mode (no multi-node service) and ``on_node`` omitted
or ``"_host"`` both return the host process's ``os.getcwd()``.  In
lab-host mode with a worker name, the route forwards a
``terrarium.files getcwd`` RPC to the named worker and returns its
``Path.home()`` so the modal seeds a sensible workspace default
instead of the host's cwd.
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


# Captured once at import — used as the daemon's start time.
_PROCESS_START = time.time()


def _get_version() -> str:
    try:
        return _pkg_version("kohakuterrarium")
    except PackageNotFoundError:
        return "unknown"


def _install_kind() -> str:
    """Launcher-managed install vs other (dev, packaged python, etc.)."""
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
            # Unknown / disconnected node — fall through to host cwd
            # rather than 500 so the modal still gets a sane default.
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
            # Prefer ``home`` as the modal's pre-fill — the worker
            # process's cwd is rarely a useful workspace default, and
            # in same-host dev deployments the worker process inherits
            # the host's cwd which is exactly the bug we're fixing.
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
    """Full diagnostic snapshot for the About panel.

    Single endpoint covering version, runtime, paths, and daemon
    state — paste-ready content for bug reports. Equivalent of
    ``kt --version --verbose`` plus the daemon's PID / uptime / mode.
    """
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
