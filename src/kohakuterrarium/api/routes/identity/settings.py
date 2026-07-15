"""Expose node-targeted Drive settings and runtime application routes.

The optional ``node`` parameter resolves either the host's local settings
surface or a connected worker's adapter. Reads are open, while persistence and
live application require admin authorization. Saving is optimistic and does not
imply that the running Drive runtime has accepted the new configuration.
"""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from starlette.requests import HTTPConnection

from kohakuterrarium.api.auth import verify_admin_token
from kohakuterrarium.api.auth.dependencies import get_auth_config
from kohakuterrarium.api.auth.engine_pool import EnginePool
from kohakuterrarium.api.deps import get_service
from kohakuterrarium.studio.identity.drive_settings import APPLIED_LIVE
from kohakuterrarium.studio.nodes import (
    _LocalNodeDriveSettings,
    build_node_map_if_multi_node,
)
from kohakuterrarium.terrarium.service import TerrariumService

router = APIRouter()

_LOCAL_NODES = {None, "", "_host"}


class DriveSettingsBody(BaseModel):
    """Carry a candidate Drive settings mapping for validation."""

    settings: dict[str, Any]


class SaveDriveSettingsBody(BaseModel):
    """Carry Drive settings and their optimistic concurrency precondition."""

    settings: dict[str, Any]
    expected_revision: str | None = None
    expected_exists: bool | None = None


def _drive_settings_for(service: TerrariumService, node: str | None):
    """Resolve the Drive-settings surface for ``node`` (local or worker)."""
    if node in _LOCAL_NODES:
        return _LocalNodeDriveSettings(service, "_host")
    node_map = build_node_map_if_multi_node(service)
    if node_map is None or node not in node_map:
        raise HTTPException(404, f"unknown or disconnected node: {node!r}")
    return node_map[node].settings.drives


@router.get("/drives")
async def drive_settings_status(
    node: str | None = Query(default=None),
    service: TerrariumService = Depends(get_service),
):
    """Return availability, enablement, and load status for the target node."""
    return await _drive_settings_for(service, node).status()


@router.get("/drives/config")
async def drive_settings_config(
    node: str | None = Query(default=None),
    service: TerrariumService = Depends(get_service),
):
    """Return the target node's validated settings and revision."""
    return await _drive_settings_for(service, node).get()


@router.get("/drives/runtime-status")
async def drive_runtime_status(
    node: str | None = Query(default=None),
    service: TerrariumService = Depends(get_service),
):
    """Return the target node's currently running Drive runtime snapshot."""
    return await _drive_settings_for(service, node).runtime_status()


@router.post("/drives/validate")
async def drive_settings_validate(
    body: DriveSettingsBody,
    node: str | None = Query(default=None),
    service: TerrariumService = Depends(get_service),
):
    """Validate a candidate settings mapping against the target node."""
    return await _drive_settings_for(service, node).validate(body.settings)


@router.put("/drives", dependencies=[Depends(verify_admin_token)])
async def drive_settings_save(
    body: SaveDriveSettingsBody,
    node: str | None = Query(default=None),
    service: TerrariumService = Depends(get_service),
):
    """Persist validated settings under an explicit optimistic precondition."""
    if body.expected_revision is None and body.expected_exists is not False:
        raise HTTPException(
            400,
            "Drive settings save requires expected_revision or expected_exists=false",
        )
    return await _drive_settings_for(service, node).save(
        body.settings,
        expected_revision=body.expected_revision,
        expected_exists=body.expected_exists,
    )


@router.post("/drives/apply", dependencies=[Depends(verify_admin_token)])
async def drive_settings_apply(
    conn_info: HTTPConnection,
    node: str | None = Query(default=None),
    service: TerrariumService = Depends(get_service),
):
    """Apply persisted settings and report their actual live scope.

    A local ``applied_live`` result evicts other pooled user engines so they
    rebuild from the shared settings file on next use. ``restart_required`` and
    ``rejected`` update no running engine, so they evict nothing and claim no
    live application scope.
    """
    result = await _drive_settings_for(service, node).apply()
    if node in _LOCAL_NODES:
        pool: EnginePool | None = getattr(conn_info.app.state, "engine_pool", None)
        auth_config = get_auth_config(conn_info)
        if pool is not None and auth_config.multi_user_enabled:
            if result.get("result") == APPLIED_LIVE:
                keep = getattr(service, "engine", None)
                evicted = pool.evict_others(keep)
                applied_engine: str | None = "request"
            else:
                # No running engine changed, so retaining pooled engines avoids
                # implying that the persisted settings are already active.
                evicted = []
                applied_engine = None
            result = {
                **result,
                "pooled_scope": {
                    "applied_live_engine": applied_engine,
                    "evicted_for_reload": [
                        None if uid is None else int(uid) for uid in evicted
                    ],
                },
            }
    return result
