"""Cross-node catalog aggregation for the lab-host controller.

Fans out ``studio.catalog.list`` to every connected worker, then
merges into a unified per-package view with availability annotations.
The host's own catalog comes in via the same code path (host has its
own adapter registered).

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

Where each per-node dict is the same shape that
:func:`list_installed_packages` returns locally (name, version, source,
…) — so per-node metadata divergences (an out-of-date worker) are
visible in the aggregated view.

The aggregator is read-only.  Installs / uninstalls scope to a single
node — call ``node_handle.runtime`` / a future ``node_handle.catalog``
to target one worker explicitly.
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
    """Slim view over MultiNodeTerrariumService — host (LabSender) + node list."""

    @property
    def host(self) -> LabSender: ...
    def connected_nodes(self) -> tuple[str, ...]: ...


async def aggregate_packages(
    service: _MultiNodeServiceLike,
    *,
    timeout: float = DEFAULT_FANOUT_TIMEOUT_SECONDS,
    include_host_local: bool = True,
) -> dict[str, dict[str, Any]]:
    """Fan out a ``studio.catalog.list`` to every connected node.

    Returns ``{package_name: {"name", "installations": {node_id: pkg}}}``.
    A package present on multiple nodes is one entry with multiple
    installations.  Nodes that error or timeout get recorded as
    ``installations[node_id] = {"error": "..."}`` so the UI can still
    show "unreachable" rather than silently dropping the node.

    ``include_host_local`` controls whether the host's own catalog is
    queried over the wire (via its self-registered adapter) — set
    False if you have the host's packages another way.
    """
    sender = service.host
    nodes = list(service.connected_nodes())
    if not include_host_local and "_host" in nodes:
        nodes.remove("_host")

    async def fetch(node_id: str) -> tuple[str, Any]:
        # ``_host`` is not a Lab client — ``host.request(to_node="_host")``
        # raises ``KeyError("unknown client '_host'")``.  Read the local
        # package catalog directly instead; the host registered its own
        # ``StudioCatalogAdapter(is_host=True)`` but that handler is
        # only reached via wire dispatch from connected clients.
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
            # Record the failure under a sentinel package name so the
            # UI can surface it without invented package entries.
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
