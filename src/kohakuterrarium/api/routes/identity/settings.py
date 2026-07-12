"""Node-targeted Drive settings routes (design §8.4, §8.5, Phase H).

Mounted under ``/api/settings`` → ``/api/settings/drives*``. Every endpoint takes
an optional ``node`` query param:

- absent / ``_host`` / standalone mode -> the host's own config home + engine
  (via :class:`~kohakuterrarium.studio.nodes._LocalNodeDriveSettings`);
- a connected worker id -> that worker's ``studio.settings`` adapter through
  ``Studio.nodes[node].settings.drives`` (the worker reads/applies its own file).

Reads are open; mutations (save/apply) sit behind the existing admin-token
dependency, matching the config-file editor routes. Typed Drive/settings errors
(validation, optimistic-concurrency conflict, home offline) propagate to the
global ``KTError`` -> HTTP mapper (409/400/404/500).
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
    settings: dict[str, Any]


class SaveDriveSettingsBody(BaseModel):
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
    """Settings-file view (available/enabled/load-error) for the target node."""
    return await _drive_settings_for(service, node).status()


@router.get("/drives/config")
async def drive_settings_config(
    node: str | None = Query(default=None),
    service: TerrariumService = Depends(get_service),
):
    """The raw validated settings + revision for the target node."""
    return await _drive_settings_for(service, node).get()


@router.get("/drives/runtime-status")
async def drive_runtime_status(
    node: str | None = Query(default=None),
    service: TerrariumService = Depends(get_service),
):
    """The *running* Drive runtime snapshot on the target node."""
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
    """Persist validated settings with an explicit optimistic precondition."""
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
    """Apply the persisted settings to the target node's live runtime. Admin-gated.

    Returns ``applied_live`` / ``restart_required`` / ``rejected`` plus desired /
    running revisions and warnings; a save never implies a live apply (design §8.6).

    Under L4 the request engine is one of many pooled per-user engines that all
    resolve the shared host ``drive-settings.yaml``. The cross-engine scope is
    reported honestly, keyed on the actual apply outcome (R1-30):

    - ``applied_live`` — the request engine took the change live; the other
      pooled engines are evicted so they rebuild from the new settings on next
      use, and the applied scope is the request engine.
    - ``restart_required`` — no running engine was updated (a live apply is not
      possible without a restart), so no engine is evicted and no live scope is
      claimed; the persisted settings take effect when engines are restarted.
    - ``rejected`` — nothing changed, so nothing is evicted.
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
                # restart_required / rejected changed no running engine; evict
                # none and claim no live scope (R1-30).
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
