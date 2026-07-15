"""Expose launcher settings, release discovery, updates, and rollback.

HTTP routes manage launcher state and initiate operations, while the dedicated
WebSocket route streams update progress. Lab clients reject this namespace
because only standalone and lab-host processes own their installation.
"""

import asyncio
import json
from typing import Any

from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect

from kohakuterrarium.launcher import settings as _launcher_settings
from kohakuterrarium.launcher.feeds import (
    FeedError,
    current_platform_tag,
    current_py_abi_tag,
    fetch_manifest,
    list_available_releases,
)
from kohakuterrarium.launcher.migration import (
    is_launcher_install,
    is_legacy_bundle,
)
from kohakuterrarium.launcher.tree_ops import (
    list_installed_versions,
    read_active_pointer,
)
from kohakuterrarium.launcher.update_runner import (
    UpdateResult,
    probe_only,
    rollback,
    run_update,
)

router = APIRouter()
# The WebSocket uses a separate router to preserve its root-level canonical
# path instead of inheriting the HTTP router's ``/api/app`` prefix.
ws_router = APIRouter()


def _wrapper_mode_only(request: Request) -> None:
    """Reject the call when the host isn't a wrapper / standalone install."""
    lab_mode = getattr(request.app.state, "lab_mode", "standalone")
    if lab_mode == "lab-client":
        raise HTTPException(
            404, "app-update routes are not available in lab-client mode"
        )


def _result_to_dict(r: UpdateResult) -> dict[str, Any]:
    """Convert an update result to the public response schema."""
    return {
        "ok": r.ok,
        "version": r.version,
        "build_id": r.build_id,
        "error": r.error,
        "restart-required": r.restart_required,
        "skipped-reason": r.skipped_reason,
    }


@router.get("/settings")
async def get_settings(request: Request) -> dict[str, Any]:
    """Return launcher settings in their public representation."""
    _wrapper_mode_only(request)
    return _launcher_settings.to_public_dict(_launcher_settings.load())


@router.put("/settings")
async def put_settings(request: Request, body: dict[str, Any]) -> dict[str, Any]:
    """Validate, persist, and return public launcher settings."""
    _wrapper_mode_only(request)
    if not isinstance(body, dict):
        raise HTTPException(400, "request body must be a JSON object")
    new_settings = _launcher_settings.from_public_dict(body)
    _launcher_settings.save(new_settings)
    return _launcher_settings.to_public_dict(new_settings)


@router.get("/state")
async def get_state(request: Request) -> dict[str, Any]:
    """Return installed versions, active version, settings, and platform state."""
    _wrapper_mode_only(request)
    cfg = _launcher_settings.load()
    ptr = read_active_pointer()
    installed = [
        {
            "version": p.version,
            "build_id": p.build_id,
            "installed_at": p.installed_at,
        }
        for p in list_installed_versions()
    ]
    return {
        "active": (
            {
                "version": ptr.version,
                "build_id": ptr.build_id,
                "installed_at": ptr.installed_at,
            }
            if ptr is not None
            else None
        ),
        "installed": installed,
        "settings": _launcher_settings.to_public_dict(cfg),
        "launcher_install": is_launcher_install(),
        "legacy_bundle": is_legacy_bundle(),
        "platform": current_platform_tag(),
        "py_abi": current_py_abi_tag(),
        "last_check_at": cfg.runtime.last_check_at,
        "last_check_error": cfg.runtime.last_check_error,
    }


@router.post("/feeds/probe")
async def post_feeds_probe(request: Request) -> dict[str, Any]:
    """Fetch the channel manifest and list compatible releases newest first."""
    _wrapper_mode_only(request)
    cfg = _launcher_settings.load()
    plat = current_platform_tag()
    abi = current_py_abi_tag()
    try:
        manifest = await asyncio.to_thread(fetch_manifest, cfg, force_refresh=True)
    except FeedError as e:
        raise HTTPException(502, f"feed probe failed: {e}") from e
    releases = list_available_releases(manifest, platform_tag=plat, py_abi_tag=abi)
    latest = releases[0]["version"] if releases else None
    return {
        "channel": cfg.channel,
        "feed": _launcher_settings.to_public_dict(cfg)["feed"],
        "platform": plat,
        "py_abi": abi,
        "latest_version": latest,
        "releases": releases,
    }


@router.post("/update")
async def post_update(request: Request) -> dict[str, Any]:
    """Validate update eligibility and return the progress WebSocket path."""
    _wrapper_mode_only(request)
    if not is_launcher_install():
        raise HTTPException(
            409,
            "this install is not launcher-managed; "
            "run `kt self-update` from the terminal instead",
        )
    return {"websocket": "/ws/app/update"}


@router.post("/rollback")
async def post_rollback(request: Request) -> dict[str, Any]:
    """Roll a launcher-managed installation back to its previous pointer."""
    _wrapper_mode_only(request)
    if not is_launcher_install():
        raise HTTPException(409, "rollback is launcher-only")
    result = await asyncio.to_thread(rollback)
    return _result_to_dict(result)


@router.post("/check")
async def post_check(request: Request) -> dict[str, Any]:
    """Probe what would be installed without doing it."""
    _wrapper_mode_only(request)
    result = await asyncio.to_thread(probe_only)
    return _result_to_dict(result)


_WS_PATH = "/ws/app/update"


async def _stream_update(ws: WebSocket) -> None:
    """Run the update in an executor and stream progress through ``ws``."""
    queue: asyncio.Queue = asyncio.Queue()

    def _push(phase: str, percent: float, message: str) -> None:
        """Translate update-runner progress into queued WebSocket frames."""
        try:
            queue.put_nowait({"phase": phase, "percent": percent, "message": message})
        except Exception:  # pragma: no cover - defensive
            pass

    loop = asyncio.get_running_loop()

    async def _pump() -> UpdateResult:
        """Execute the blocking updater outside the event loop."""
        return await loop.run_in_executor(None, lambda: run_update(_push))

    runner_task = asyncio.create_task(_pump())
    try:
        while True:
            done, _ = await asyncio.wait(
                [runner_task, asyncio.create_task(queue.get())],
                return_when=asyncio.FIRST_COMPLETED,
                timeout=0.5,
            )
            # Flush all available progress before testing for completion so the
            # terminal frame cannot overtake an already queued update.
            while not queue.empty():
                frame = queue.get_nowait()
                await ws.send_text(json.dumps(frame))
            if runner_task in done:
                break
    finally:
        result = await runner_task
    terminal = {
        "phase": "done" if result.ok else "failed",
        "percent": 100,
        "message": (
            f"updated to {result.version}" if result.ok else (result.error or "failed")
        ),
        "status": "ok" if result.ok else "failed",
        "restart-required": result.restart_required,
        "version": result.version,
        "build_id": result.build_id,
        "skipped-reason": result.skipped_reason,
    }
    await ws.send_text(json.dumps(terminal))


@ws_router.websocket(_WS_PATH)
async def ws_update(ws: WebSocket) -> None:
    """Stream an eligible launcher update or close with a mode-specific refusal."""
    lab_mode = getattr(ws.app.state, "lab_mode", "standalone")
    if lab_mode == "lab-client":
        await ws.close(code=4404)
        return
    if not is_launcher_install():
        await ws.accept()
        await ws.send_text(
            json.dumps(
                {
                    "phase": "refused",
                    "percent": 0,
                    "message": "non-launcher install; use `kt self-update` from terminal",
                    "status": "failed",
                }
            )
        )
        await ws.close()
        return
    await ws.accept()
    try:
        await _stream_update(ws)
    except WebSocketDisconnect:
        return
    finally:
        try:
            await ws.close()
        except Exception:  # pragma: no cover - already closed
            pass


__all__ = ["router", "ws_router"]
