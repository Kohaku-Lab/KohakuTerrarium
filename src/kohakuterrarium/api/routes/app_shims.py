"""Terminal command (``kt``) management for the machine running the server.

The shims let a desktop-app install run ``kt`` from a terminal. Installing writes
launcher scripts into a per-user directory and, on request, adds that directory to
the user's PATH; both only ever happen for requests from the same machine.
"""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from kohakuterrarium.api.auth.dependencies import verify_admin_token
from kohakuterrarium.cli.shims import (
    MODE_DEFER_PIPX,
    SHIM_SUBCOMMANDS,
    apply_plan,
    build_plan,
    is_ours,
    shim_path,
)
from kohakuterrarium.utils.user_path import (
    add_to_user_path,
    is_on_user_path,
    remove_from_user_path,
)

router = APIRouter()

_LOOPBACK = frozenset({"127.0.0.1", "::1", "localhost"})


class InstallRequest(BaseModel):
    add_to_path: bool = True


class UninstallRequest(BaseModel):
    remove_from_path: bool = True


def _is_local(request: Request) -> bool:
    return (request.client.host if request.client else "") in _LOOPBACK


def _local_only(request: Request) -> None:
    if not _is_local(request):
        raise HTTPException(403, "terminal commands can only be managed locally")


def _status(request: Request) -> dict[str, Any]:
    plan = build_plan()
    installed = [name for name in SHIM_SUBCOMMANDS if is_ours(shim_path(plan, name))]
    return {
        "local": _is_local(request),
        "supported": plan.mode != MODE_DEFER_PIPX,
        "mode": plan.mode,
        "note": plan.note,
        "target_dir": str(plan.target_dir),
        "commands": list(SHIM_SUBCOMMANDS),
        "installed": installed,
        "on_path": is_on_user_path(plan.target_dir),
    }


@router.get("/shims")
async def get_shims(request: Request) -> dict[str, Any]:
    """Report whether the terminal commands are installed and reachable."""
    return _status(request)


@router.post(
    "/shims/install",
    dependencies=[Depends(verify_admin_token)],
)
async def install_shims(request: Request, body: InstallRequest) -> dict[str, Any]:
    """Write the launcher scripts and optionally put their directory on PATH."""
    _local_only(request)
    plan = build_plan()
    if plan.mode == MODE_DEFER_PIPX:
        raise HTTPException(409, plan.note)
    messages = apply_plan(plan)
    if body.add_to_path:
        add_to_user_path(plan.target_dir)
    return {**_status(request), "messages": messages}


@router.post(
    "/shims/uninstall",
    dependencies=[Depends(verify_admin_token)],
)
async def uninstall_shims(request: Request, body: UninstallRequest) -> dict[str, Any]:
    """Remove the launcher scripts this app wrote, and optionally the PATH entry."""
    _local_only(request)
    plan = build_plan()
    removed = []
    for name in SHIM_SUBCOMMANDS:
        path = shim_path(plan, name)
        if is_ours(path):
            path.unlink()
            removed.append(name)
    if body.remove_from_path:
        remove_from_user_path(plan.target_dir)
    return {**_status(request), "removed": removed}
