"""Expose lab node discovery, status, and creature deployment routes.

The namespace returns 404 for standalone services rather than representing the
host as a one-node cluster. Node summaries tolerate unreachable workers so
operators can still see cluster membership and degraded status.
"""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from kohakuterrarium.api.deps import get_service
from kohakuterrarium.studio.deploy import DeployError, deploy_creature_to_node
from kohakuterrarium.terrarium.service import TerrariumService

router = APIRouter()


def _multi_node_service(service: TerrariumService):
    """Return the multi-node service or 404 if we're standalone."""
    if not hasattr(service, "connected_nodes"):
        raise HTTPException(
            404,
            "Node routes are lab-host-only; current mode is standalone.",
        )
    return service


@router.get("")
async def list_nodes(service: TerrariumService = Depends(get_service)):
    """List the host and connected workers with best-effort creature counts."""
    multi = _multi_node_service(service)
    nodes = list(multi.connected_nodes())
    out: list[dict[str, Any]] = []
    for node_id in nodes:
        entry: dict[str, Any] = {
            "node_id": node_id,
            "is_host": node_id == "_host",
            "status": "online",
        }
        # A node remains visible when its service call fails; status and a null
        # count distinguish transient unreachability from cluster removal.
        try:
            svc = multi.service_for(node_id)
            entry["creatures"] = len(await svc.list_creatures())
        except Exception:
            entry["creatures"] = None
            entry["status"] = "unreachable"
        out.append(entry)
    return {"nodes": out}


@router.get("/{node_id}/status")
async def node_status(node_id: str, service: TerrariumService = Depends(get_service)):
    """Return one node's status, creature count, and optional Drive state."""
    multi = _multi_node_service(service)
    if node_id not in multi.connected_nodes():
        raise HTTPException(404, f"unknown node: {node_id!r}")
    try:
        svc = multi.service_for(node_id)
        snapshot = await svc.status_snapshot()
        creatures = await svc.list_creatures()
    except Exception as e:
        raise HTTPException(503, f"node {node_id!r} unreachable: {e}")
    # Drive status is optional because disabled or older workers may not expose
    # the runtime surface; node health should not fail for that omission.
    try:
        drive = (await svc.drive_runtime_status()).to_dict()
    except Exception:
        drive = None
    return {
        "node_id": node_id,
        "is_host": node_id == "_host",
        "ok": True,
        "creatures": len(creatures),
        "status_snapshot": snapshot,
        "drive": drive,
    }


class DeployCreatureRequest(BaseModel):
    """Identify the host-local creature workspace to copy to a worker."""

    workspace_path: str


@router.post("/{node_id}/deploy/creature")
async def deploy_creature(
    node_id: str,
    req: DeployCreatureRequest,
    service: TerrariumService = Depends(get_service),
):
    """Copy a local creature workspace to a worker and return its remote path.

    The returned absolute path is suitable for a subsequent ``add_creature``
    request on that worker.
    """
    multi = _multi_node_service(service)
    if node_id == "_host":
        raise HTTPException(
            400, "Cannot deploy to '_host' — host already has the workspace"
        )
    if node_id not in multi.connected_nodes():
        raise HTTPException(404, f"unknown node: {node_id!r}")
    try:
        target_path = await deploy_creature_to_node(
            multi.host, node_id, req.workspace_path
        )
    except DeployError as e:
        raise HTTPException(409, str(e))
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))
    return {"target_path": target_path, "node_id": node_id}
