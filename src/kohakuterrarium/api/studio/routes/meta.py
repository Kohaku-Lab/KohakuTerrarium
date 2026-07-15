"""Meta routes — health + version for studio backend."""

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version

from fastapi import APIRouter, Depends

from kohakuterrarium.api.deps import get_service
from kohakuterrarium.terrarium.service import TerrariumService

router = APIRouter()


STUDIO_VERSION = "0.1.0"


def _core_version() -> str:
    """Return the installed core version when package metadata is available."""
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
    """Return component versions and topology metadata for frontend startup."""
    # Only multi-node services expose ``connected_nodes``; its presence defines
    # lab-host mode and avoids a separate node-count request.
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
