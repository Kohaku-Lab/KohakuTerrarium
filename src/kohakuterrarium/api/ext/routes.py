"""``/api/extensions/ui``: list package UI, toggle it, and mint asset tokens."""

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from kohakuterrarium.api._io_executor import run_in_io_executor
from kohakuterrarium.api.auth.asset_token import mint_asset_token
from kohakuterrarium.api.auth.dependencies import get_auth_config
from kohakuterrarium.api.ext import require_admin, require_user
from kohakuterrarium.api.ext.policy import default_enabled, dev_overrides, slot_enabled
from kohakuterrarium.packages.slots.ui import get_package_ui, list_package_ui
from kohakuterrarium.studio.identity import ui_extensions

router = APIRouter()


class EnableRequest(BaseModel):
    enabled: bool | None = None


class AssetTokenRequest(BaseModel):
    package: str = Field(min_length=1)


def _listing(auth_config) -> dict:
    dev = dev_overrides()
    choices = ui_extensions.load_choices()
    packages = []
    for slot in list_package_ui():
        record = slot.to_dict()
        record["enabled"] = slot_enabled(slot, auth_config)
        record["enabled_by_default"] = default_enabled(auth_config)
        record["choice"] = choices.get(slot.package)
        record["dev_url"] = dev.get(slot.package, "")
        packages.append(record)
    return {"packages": packages}


@router.get("")
async def list_ui_extensions(request: Request, _user=Depends(require_user)) -> dict:
    """Every installed package's UI contributions and whether each is live."""
    return await run_in_io_executor(_listing, get_auth_config(request))


@router.put("/{package}")
async def set_ui_extension_enabled(
    package: str,
    req: EnableRequest,
    request: Request,
    _admin: None = Depends(require_admin),
) -> dict:
    """Enable or disable a package's UI; ``enabled: null`` restores the default."""
    if get_package_ui(package) is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "not_found", "message": f"no UI in package {package}"},
        )
    await run_in_io_executor(ui_extensions.set_enabled, package, req.enabled)
    return await run_in_io_executor(_listing, get_auth_config(request))


@router.post("/asset-token")
async def create_asset_token(
    req: AssetTokenRequest, request: Request, _user=Depends(require_user)
) -> dict:
    """A short-lived token for one package's ``/api/ext/<pkg>/ui/<token>/<file>`` URLs."""
    secret = getattr(request.app.state, "ui_asset_secret", None)
    if not isinstance(secret, bytes):
        raise HTTPException(
            status_code=503,
            detail={"error": "unavailable", "message": "asset tokens not configured"},
        )
    slot = get_package_ui(req.package)
    if slot is None or not slot_enabled(slot, get_auth_config(request)):
        raise HTTPException(
            status_code=404,
            detail={"error": "not_found", "message": f"no enabled UI in {req.package}"},
        )
    token, expires_at = mint_asset_token(secret, slot.package)
    return {"token": token, "expires_at": expires_at, "package": slot.package}
