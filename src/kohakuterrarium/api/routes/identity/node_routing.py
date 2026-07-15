"""Route identity operations to the host store or a connected worker.

Worker operations execute through the ``studio.identity`` lab namespace so each
node reads and writes its own configuration directory. This is required for
process-local credentials such as Codex OAuth tokens. Routing and transport
failures are normalized to HTTP errors for route handlers.
"""

from typing import Any

from fastapi import HTTPException

from kohakuterrarium.terrarium.service import TerrariumService

HOST_NODE = "_host"
NAMESPACE = "studio.identity"


def is_host_target(node: str | None) -> bool:
    """``True`` when the route should hit the host's own local store."""
    return not node or node == HOST_NODE


async def call_node_identity(
    service: TerrariumService,
    node: str,
    type_: str,
    body: dict[str, Any] | None = None,
    *,
    timeout: float = 30.0,
) -> dict[str, Any]:
    """Invoke a worker's ``studio.identity`` adapter and normalize failures.

    The target must be connected through a multi-node service. Adapter error
    kinds map to client-facing status codes; transport and unknown failures map
    to 502.
    """
    host = getattr(service, "host", None)
    connected = (
        list(service.connected_nodes()) if hasattr(service, "connected_nodes") else []
    )
    if host is None or node not in connected:
        raise HTTPException(
            status_code=404,
            detail=f"node={node!r} is not a connected lab node",
        )
    try:
        resp = await host.request(
            to_node=node,
            namespace=NAMESPACE,
            type=type_,
            body=body or {},
            timeout=timeout,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"lab transport error to {node!r}: {exc}",
        ) from exc
    if isinstance(resp, dict) and "error" in resp:
        err = resp["error"] if isinstance(resp["error"], dict) else {}
        kind = err.get("kind") or ""
        message = err.get("message") or ""
        status = {
            "not_found": 404,
            "invalid": 400,
            "unknown_type": 400,
        }.get(kind, 502)
        raise HTTPException(
            status_code=status,
            detail=f"{node}: {message}" if message else f"{node}: identity op failed",
        )
    return resp


__all__ = ["HOST_NODE", "call_node_identity", "is_host_target"]
