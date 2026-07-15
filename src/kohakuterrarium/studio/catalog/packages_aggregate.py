"""Cross-node catalog aggregation for the lab-host controller.

Collects package catalogs from connected nodes and merges installations by
package name while preserving node-specific metadata and failures.

Result shape::

    {
        "<package_name>": {
            "name": str,
            "installations": {
                "<node_id>": <per-node pkg dict>,
            },
        },
        ...
    }

Each installation retains the local package payload, making version or source
divergence visible across nodes. Aggregation is read-only; mutations must target
a specific node.
"""

import asyncio
from typing import Any, Protocol, runtime_checkable

from kohakuterrarium.laboratory.protocols import LabSender
from kohakuterrarium.studio.catalog.packages import list_installed_packages
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


NAMESPACE = "studio.catalog"
DEFAULT_FANOUT_TIMEOUT_SECONDS = 5.0


@runtime_checkable
class _MultiNodeServiceLike(Protocol):
    """Minimal multi-node capabilities required for catalog fan-out."""

    @property
    def host(self) -> LabSender: ...
    def connected_nodes(self) -> tuple[str, ...]: ...


async def aggregate_packages(
    service: _MultiNodeServiceLike,
    *,
    timeout: float = DEFAULT_FANOUT_TIMEOUT_SECONDS,
    include_host_local: bool = True,
) -> dict[str, dict[str, Any]]:
    """Return package installations aggregated across connected nodes.

    A package present on multiple nodes shares one entry. Node failures are
    retained under ``__node_errors__`` so partial fan-out remains observable.
    ``include_host_local`` controls whether the coordination host's local package
    catalog participates.
    """
    sender = service.host
    nodes = list(service.connected_nodes())
    if not include_host_local and "_host" in nodes:
        nodes.remove("_host")

    async def fetch(node_id: str) -> tuple[str, Any]:
        # The coordination host is not a wire-addressable client, so its catalog
        # must be read locally rather than sent through ``LabSender.request``.
        if node_id == "_host":
            try:
                packages = await asyncio.to_thread(list_installed_packages)
            except Exception as e:
                return node_id, {"error": str(e)}
            return node_id, list(packages)
        try:
            body = await sender.request(
                to_node=node_id,
                namespace=NAMESPACE,
                type="list",
                body={},
                timeout=timeout,
            )
        except Exception as e:
            return node_id, {"error": str(e)}
        if isinstance(body, dict) and "error" in body:
            return node_id, {"error": body["error"].get("message", "")}
        return node_id, list(body.get("packages", []))

    results = await asyncio.gather(
        *(fetch(node_id) for node_id in nodes),
        return_exceptions=False,
    )

    aggregated: dict[str, dict[str, Any]] = {}
    for node_id, payload in results:
        if isinstance(payload, dict) and "error" in payload:
            # A sentinel keeps node failures visible without inventing a package
            # identity for an unsuccessful response.
            aggregated.setdefault("__node_errors__", {"installations": {}})
            aggregated["__node_errors__"]["installations"][node_id] = payload
            continue
        for pkg in payload:
            name = pkg.get("name")
            if not isinstance(name, str):
                continue
            entry = aggregated.setdefault(name, {"name": name, "installations": {}})
            entry["installations"][node_id] = pkg
    return aggregated


__all__ = ["DEFAULT_FANOUT_TIMEOUT_SECONDS", "NAMESPACE", "aggregate_packages"]
