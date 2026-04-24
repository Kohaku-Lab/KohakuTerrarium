"""Manifest sync route — append a workspace-authored module into
``kohaku.yaml`` so other creatures (and the catalog) can discover it.

Idempotent: calling the endpoint twice for the same ``(kind, name)``
is a no-op on the second call. The implementation lives on
:class:`LocalWorkspace` so the workspace protocol stays the single
home for round-trip YAML writes.
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from kohakuterrarium.api.studio.deps import get_workspace
from kohakuterrarium.api.studio.workspace.base import Workspace
from kohakuterrarium.api.studio.workspace.local import KNOWN_KINDS

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
