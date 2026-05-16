"""Lab-only routes — node discovery + per-node status.

Mounted at ``/api/nodes``. Lab-only: every endpoint returns 404 in
standalone mode (the service has no ``connected_nodes`` surface) so
a misconfigured frontend fails loudly instead of silently treating
the standalone host as a one-node cluster.

Unblocks: the node-picker dropdown in the creature-create dialog,
per-node admin tab, "is this worker alive" tooltip badges.
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
    """List every connected node (host + remote workers).

    Each entry carries ``node_id`` + ``is_host`` + ``status``. The
    node-picker dropdown source.
    """
    multi = _multi_node_service(service)
    nodes = list(multi.connected_nodes())
    out: list[dict[str, Any]] = []
    for node_id in nodes:
        entry: dict[str, Any] = {
            "node_id": node_id,
            "is_host": node_id == "_host",
            "status": "online",
        }
        # Best-effort creature count without blocking on a wire fetch
        # (the dashboard polls this endpoint).
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
    """Per-node health + creature count + status snapshot."""
    multi = _multi_node_service(service)
    if node_id not in multi.connected_nodes():
        raise HTTPException(404, f"unknown node: {node_id!r}")
    try:
        svc = multi.service_for(node_id)
        snapshot = await svc.status_snapshot()
        creatures = await svc.list_creatures()
    except Exception as e:
        raise HTTPException(503, f"node {node_id!r} unreachable: {e}")
    return {
        "node_id": node_id,
        "is_host": node_id == "_host",
        "ok": True,
        "creatures": len(creatures),
        "status_snapshot": snapshot,
    }


class DeployCreatureRequest(BaseModel):
    workspace_path: str


@router.post("/{node_id}/deploy/creature")
async def deploy_creature(
    node_id: str,
    req: DeployCreatureRequest,
    service: TerrariumService = Depends(get_service),
):
    """Push a local creature workspace folder to a worker.

    Returns the worker-side absolute path that the subsequent
    ``add_creature`` body should reference.  Wraps
    :func:`studio.deploy.deploy_creature_to_node`.
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
