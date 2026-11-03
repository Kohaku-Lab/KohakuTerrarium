"""Expose global module defaults for newly created Studio sessions."""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from kohakuterrarium.api.auth import verify_admin_token
from kohakuterrarium.studio.identity.module_defaults import (
    list_defaults,
    patch_defaults,
)
from kohakuterrarium.utils.file.lock import FileLockBusy

router = APIRouter()


class ModuleDefaultsPatch(BaseModel):
    """Patch one module's default options and optional plugin enablement."""

    values: dict[str, Any] = Field(default_factory=dict)
    enabled: bool | None = None


@router.get("/module-defaults")
def get_module_defaults():
    """Return catalog schemas with the defaults for new sessions."""
    return {"modules": list_defaults()}


@router.put(
    "/module-defaults/{module_type}/{name}", dependencies=[Depends(verify_admin_token)]
)
def update_module_defaults(module_type: str, name: str, req: ModuleDefaultsPatch):
    """Save one module without applying anything to existing sessions."""
    try:
        return patch_defaults(module_type, name, req.values, enabled=req.enabled)
    except KeyError as exc:
        raise HTTPException(404, "Unknown configurable module") from exc
    except FileLockBusy as exc:
        raise HTTPException(409, "Module defaults are being saved; retry") from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
