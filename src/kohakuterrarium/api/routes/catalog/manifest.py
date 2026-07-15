"""Register workspace modules in ``kohaku.yaml`` for catalog discovery.

Sync is idempotent for each ``(kind, name)``, and the workspace owns the YAML
round trip so manifest writes remain centralized.
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from kohakuterrarium.api.routes.catalog._deps import get_workspace
from kohakuterrarium.studio.editors.workspace_fs import KNOWN_KINDS
from kohakuterrarium.studio.editors.workspace_manifest import Workspace

router = APIRouter()


class ManifestSyncBody(BaseModel):
    kind: str
    name: str


@router.post("/sync")
async def sync_manifest(
    body: ManifestSyncBody, ws: Workspace = Depends(get_workspace)
) -> dict:
    if body.kind not in KNOWN_KINDS:
        raise HTTPException(
            400,
            detail={
                "code": "unknown_kind",
                "message": f"unknown module kind: {body.kind!r}",
                "valid_kinds": list(KNOWN_KINDS),
            },
        )
    try:
        return ws.sync_manifest(body.kind, body.name)  # type: ignore[attr-defined]
    except FileNotFoundError:
        raise HTTPException(
            404,
            detail={
                "code": "not_found",
                "message": f"{body.kind}/{body.name} not found",
            },
        )
    except ValueError as e:
        raise HTTPException(400, detail={"code": "invalid_name", "message": str(e)})
