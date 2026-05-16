"""Per-creature plugin routes — list + toggle.

Accepts the display ``name`` or the engine's ``creature_id`` in the
URL slot via :func:`resolve_creature_id` — the frontend stores
names, so without this resolver every plugin-panel hit 404s.
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from kohakuterrarium.api.deps import get_service
from kohakuterrarium.api.routes.sessions_v2._helpers import resolve_creature_id
from kohakuterrarium.terrarium.service import TerrariumService

router = APIRouter()


class TogglePluginRequest(BaseModel):
    """Optional body for the toggle endpoint.

    Frontend posts ``{"enabled": <bool>}``.  Absent body or an empty
    object defaults to ``True`` — matches the legacy behaviour where
    the route had no body parser at all and effectively flipped the
    plugin on.
    """

    enabled: bool = True


@router.get("/{session_id}/creatures/{creature_id}/plugins")
async def list_plugins(
    session_id: str,
    creature_id: str,
    service: TerrariumService = Depends(get_service),
):
    cid = await resolve_creature_id(service, creature_id)
    try:
        return await service.list_plugins(cid)
    except KeyError:
        raise HTTPException(404, f"creature {creature_id!r} not found")


@router.post("/{session_id}/creatures/{creature_id}/plugins/{plugin_name}/toggle")
async def toggle_plugin(
    session_id: str,
    creature_id: str,
    plugin_name: str,
    req: TogglePluginRequest | None = None,
    service: TerrariumService = Depends(get_service),
):
    cid = await resolve_creature_id(service, creature_id)
    enabled = req.enabled if req is not None else True
    try:
        return await service.toggle_plugin(cid, plugin_name, enabled)
    except KeyError:
        raise HTTPException(404, f"creature {creature_id!r} not found")
    except ValueError as e:
        raise HTTPException(404, str(e))
