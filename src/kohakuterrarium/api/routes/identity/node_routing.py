"""Per-node routing for identity-management ops.

In lab-host mode the user picks WHICH node's credentials they're
managing (host or any connected worker).  Settings > Providers passes
the target via a ``?node=<id>`` query param.

When ``node == "_host"`` (or unspecified): the existing host-local
helpers run unchanged.

When ``node`` names a connected worker: the operation is forwarded
over Lab APP namespace ``studio.identity`` to the worker, whose
:class:`StudioIdentityAdapter` writes to its OWN ``KT_CONFIG_DIR``
(api_keys.yaml / codex-auth.json / …).  This is the only sound way
to manage Codex on a worker: OAuth tokens are process-scoped, so the
worker MUST run the login in its own process and keep the resulting
tokens on its own disk.

The helper raises :class:`fastapi.HTTPException` on transport / target
errors so route handlers can ``raise`` it directly.
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
    """Route a ``studio.identity`` op to ``node``'s adapter.

    ``service`` must be a ``MultiNodeTerrariumService`` (lab-host
    mode); standalone runs have no remotes so this is reached only
    when the caller already validated the mode.
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
