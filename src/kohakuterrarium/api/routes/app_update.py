"""``/api/app/*`` — app-update API for the Vue ``Admin → Updates`` tab.

Wraps the launcher's update_runner + settings + version-check helpers
in HTTP + WebSocket form so the frontend can drive the same flow
``kt self-update`` exposes on the CLI.  Only mounted in standalone
and lab-host modes; worker installs (lab-client) return 404 across
the whole namespace.

See ``plans/1.5.0-roadmap/06-app-update/design.md`` §11 for the
contract.
"""

import asyncio
import json
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect

from kohakuterrarium.cli.self_update import _current_version, _latest_pypi_version
from kohakuterrarium.launcher import settings as _launcher_settings
from kohakuterrarium.launcher.migration import is_legacy_bundle
from kohakuterrarium.launcher.paths import wrapper_marker_path
from kohakuterrarium.launcher.update_runner import (
    UpdateResult,
    reset_to_bundled,
    rollback_to_previous,
    run_update,
)

router = APIRouter()
# WebSocket lives on its own router so the API router's ``/api/app``
# prefix doesn't shift the canonical ``/ws/app/update`` path.
ws_router = APIRouter()


def _wrapper_mode_only(request: Request) -> None:
    """Reject the call when the host isn't a wrapper / standalone install.

    Worker mode (``lab-client``) doesn't expose this surface — the
    operator manages the worker host's framework version via its own
    install path, not over a remote API.
    """
    lab_mode = getattr(request.app.state, "lab_mode", "standalone")
    if lab_mode == "lab-client":
        raise HTTPException(
            404, "app-update routes are not available in lab-client mode"
        )


def _settings_to_dict(s: _launcher_settings.AppSettings) -> dict[str, Any]:
    return {
        "source": asdict(s.source),
        "update": {
            "mode": s.update.mode,
            "check-cache-hours": s.update.check_cache_hours,
        },
        "runtime": {
            "venv-path": s.runtime.venv_path,
            "last-installed-version": s.runtime.last_installed_version,
            "last-check-at": s.runtime.last_check_at,
            "install-source": s.runtime.install_source,
        },
    }


def _result_to_dict(r: UpdateResult) -> dict[str, Any]:
    return {
        "ok": r.ok,
        "version": r.version,
        "error": r.error,
        "restart-required": r.restart_required,
        "skipped-reason": r.skipped_reason,
    }


def _install_kind() -> str:
    """Cheap probe for the install kind — wrapper-managed vs other."""
    return "wrapper" if wrapper_marker_path().is_file() else "user"


@router.get("/settings")
async def get_settings(request: Request) -> dict[str, Any]:
    _wrapper_mode_only(request)
    return _settings_to_dict(_launcher_settings.load())


@router.put("/settings")
async def put_settings(request: Request, body: dict[str, Any]) -> dict[str, Any]:
    _wrapper_mode_only(request)
    # Schema-validate by round-tripping through the launcher's
    # coercion layer — invalid fields fall back to defaults with a
    # warning, so the response always reflects what the launcher will
    # actually use.
    current = _launcher_settings.load()
    src = body.get("source") or {}
    upd = body.get("update") or {}
    if "kind" in src:
        if src["kind"] not in _launcher_settings.SOURCE_KINDS:
            raise HTTPException(400, f"invalid source.kind {src['kind']!r}")
        current.source.kind = src["kind"]
    if "spec" in src:
        spec = src["spec"]
        if spec is not None and not isinstance(spec, str):
            raise HTTPException(400, "source.spec must be a string or null")
        current.source.spec = spec
    if "extras" in src:
        extras = src["extras"] or []
        if not (isinstance(extras, list) and all(isinstance(e, str) for e in extras)):
            raise HTTPException(400, "source.extras must be a list of strings")
        current.source.extras = list(extras)
    if "mode" in upd:
        if upd["mode"] not in _launcher_settings.UPDATE_MODES:
            raise HTTPException(400, f"invalid update.mode {upd['mode']!r}")
        current.update.mode = upd["mode"]
    if "check-cache-hours" in upd:
        hours = upd["check-cache-hours"]
        if not (isinstance(hours, int) and hours > 0):
            raise HTTPException(400, "update.check-cache-hours must be a positive int")
        current.update.check_cache_hours = hours
    _launcher_settings.save(current)
    return _settings_to_dict(current)


@router.get("/update-status")
async def get_update_status(request: Request) -> dict[str, Any]:
    _wrapper_mode_only(request)
    cfg = _launcher_settings.load()
    cur = cfg.runtime.last_installed_version or _current_version()
    # No probe here — UI reads cached state.  Use POST /check-now to
    # force a fresh probe.
    return {
        "current-version": cur,
        "latest-version": None,
        "available": None,
        "last-check-at": cfg.runtime.last_check_at,
        "source-kind": cfg.source.kind,
        "install-source": cfg.runtime.install_source,
        "install-kind": _install_kind(),
        "legacy-bundle": is_legacy_bundle(),
    }


@router.post("/check-now")
async def check_now(request: Request) -> dict[str, Any]:
    _wrapper_mode_only(request)
    cfg = _launcher_settings.load()
    cur = cfg.runtime.last_installed_version or _current_version()
    # Probe runs in a worker thread so the event loop stays responsive
    # while urlopen does its DNS + TLS dance.
    latest = await asyncio.to_thread(_latest_pypi_version)
    if latest is not None:
        cfg.runtime.last_check_at = datetime.now(timezone.utc).isoformat(
            timespec="seconds"
        )
        _launcher_settings.save(cfg)
    available = bool(latest and cur and latest != cur)
    return {
        "current-version": cur,
        "latest-version": latest,
        "available": available,
        "last-check-at": cfg.runtime.last_check_at,
        "source-kind": cfg.source.kind,
        "install-source": cfg.runtime.install_source,
        "install-kind": _install_kind(),
        "legacy-bundle": is_legacy_bundle(),
    }


@router.post("/update")
async def post_update(request: Request) -> dict[str, Any]:
    _wrapper_mode_only(request)
    if _install_kind() != "wrapper":
        raise HTTPException(
            409,
            "this install is not wrapper-managed; run `kt self-update` "
            "from the terminal instead",
        )
    # Just acknowledge — the actual run happens over the WebSocket
    # so progress can stream.  Returning the WS path here lets the
    # frontend always reach the same endpoint shape regardless of
    # base URL changes.
    return {"websocket": "/ws/app/update"}


@router.post("/rollback")
async def post_rollback(request: Request) -> dict[str, Any]:
    _wrapper_mode_only(request)
    if _install_kind() != "wrapper":
        raise HTTPException(409, "rollback is wrapper-only")
    result = await asyncio.to_thread(rollback_to_previous)
    return _result_to_dict(result)


@router.post("/reset-venv")
async def post_reset_venv(request: Request) -> dict[str, Any]:
    _wrapper_mode_only(request)
    if _install_kind() != "wrapper":
        raise HTTPException(409, "reset-venv is wrapper-only")
    result = await asyncio.to_thread(reset_to_bundled)
    return _result_to_dict(result)


# WebSocket -----------------------------------------------------------


_WS_PATH = "/ws/app/update"


async def _stream_update(ws: WebSocket) -> None:
    """Drive run_update() to completion, streaming a coarse progress feed.

    The launcher's update_runner doesn't yet expose a per-step
    progress callback — Phase H wiring will pipe pip's stdout into a
    real percent figure.  For now we emit pre/post frames so the
    client UI exercises the same shape.
    """
    await ws.send_text(json.dumps({"phase": "starting", "percent": 5, "message": ""}))
    await ws.send_text(
        json.dumps({"phase": "installing", "percent": 35, "message": "pip install ..."})
    )
    result = await asyncio.to_thread(run_update)
    if result.ok:
        await ws.send_text(
            json.dumps(
                {
                    "phase": "ready",
                    "percent": 100,
                    "message": f"updated to {result.version}",
                    "status": "ok",
                    "restart-required": result.restart_required,
                }
            )
        )
    else:
        await ws.send_text(
            json.dumps(
                {
                    "phase": "failed",
                    "percent": 100,
                    "message": result.error or "update failed",
                    "status": "failed",
                    "restart-required": False,
                }
            )
        )


@ws_router.websocket(_WS_PATH)
async def ws_update(ws: WebSocket) -> None:
    lab_mode = getattr(ws.app.state, "lab_mode", "standalone")
    if lab_mode == "lab-client":
        await ws.close(code=4404)
        return
    if _install_kind() != "wrapper":
        await ws.accept()
        await ws.send_text(
            json.dumps(
                {
                    "phase": "refused",
                    "percent": 0,
                    "message": "non-wrapper install; use `kt self-update` from terminal",
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
