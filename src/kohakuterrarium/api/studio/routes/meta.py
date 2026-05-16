"""Meta routes — health + version for studio backend."""

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version

from fastapi import APIRouter, Depends

from kohakuterrarium.api.deps import get_service
from kohakuterrarium.terrarium.service import TerrariumService

router = APIRouter()


STUDIO_VERSION = "0.1.0"


def _core_version() -> str:
    try:
        return _pkg_version("kohakuterrarium")
    except PackageNotFoundError:
        return "unknown"


@router.get("/health")
async def health() -> dict:
    """Liveness probe."""
    return {"ok": True}


@router.get("/version")
async def version(service: TerrariumService = Depends(get_service)) -> dict:
    """Return studio + core versions plus the runtime mode.

    Frontend reads ``mode`` at boot to decide whether to render
    node-pickers / per-node badges (``"lab-host"``) or stay in
    single-node UI (``"standalone"``). When in lab-host mode also
    surfaces the connected node count so the dashboard can show a
    cluster-size badge without a separate ``/api/nodes`` round-trip.
    """
    # Multi-node services expose ``connected_nodes`` returning
    # ("_host", "worker-1", ...).  Local services don't have it.
    nodes_fn = getattr(service, "connected_nodes", None)
    if callable(nodes_fn):
        nodes = tuple(nodes_fn())
        mode = "lab-host"
        node_count = len(nodes)
    else:
        mode = "standalone"
        node_count = 1
    return {
        "studio": STUDIO_VERSION,
        "core": _core_version(),
        "mode": mode,
        "node_count": node_count,
    }
