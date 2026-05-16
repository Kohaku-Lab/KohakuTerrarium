"""Per-node access handles for Studio in multi-node mode.

``Studio.nodes`` is a mapping ``{node_id: NodeHandle}``.  Each handle
gives access to per-node services:

- ``.runtime`` — the :class:`RemoteTerrariumService` (or
  :class:`LocalTerrariumService` for ``_host``).
- ``.files`` — terrarium.files client (Unit B).
- ``.identity`` — studio.identity client (lands in Unit E).
- ``.catalog`` — studio.catalog client (lands in Unit F).
- ``.deploy`` — studio.deploy client (lands in Unit C).

Stub attributes for not-yet-implemented surfaces raise a clear error
when accessed so call sites get a helpful pointer at the unit that
lands the feature.
"""

from typing import Any

from kohakuterrarium.studio.deploy import deploy_creature_to_node
from kohakuterrarium.studio.files import RemoteFiles
from kohakuterrarium.terrarium.service import LocalTerrariumService, TerrariumService


class _Pending:
    """Placeholder for sub-services not yet implemented."""

    def __init__(self, name: str, unit: str) -> None:
        self._name = name
        self._unit = unit

    def __getattr__(self, attr: str):
        raise NotImplementedError(
            f"NodeHandle.{self._name} lands in {self._unit}; "
            f"not available yet (tried to access .{attr})"
        )


class _Deploy:
    """Per-node deploy surface.  Thin wrapper to keep call sites tidy."""

    def __init__(self, sender: Any, target_node: str) -> None:
        self._sender = sender
        self._target_node = target_node

    async def push_creature(
        self,
        local_path: "str | Any",
        *,
        name: str | None = None,
        timeout: float = 30.0,
    ) -> str:
        """Deploy a local creature directory to this node.

        Returns the absolute target path on the worker; pass it to
        :meth:`MultiNodeTerrariumService.add_creature`.
        """
        return await deploy_creature_to_node(
            self._sender,
            self._target_node,
            local_path,
            name=name,
            timeout=timeout,
        )


class NodeHandle:
    """Per-node service surface for Studio.

    Construct via :class:`MultiNodeTerrariumService` membership.
    """

    def __init__(
        self,
        node_id: str,
        runtime: TerrariumService,
        *,
        sender: Any = None,
    ) -> None:
        self._node_id = node_id
        self.runtime: TerrariumService = runtime
        # ``sender`` is the Lab node (HostEngine in lab-host mode) the
        # controller uses for APP requests against this worker.  Local
        # nodes (``_host``) have no remote surface — files / identity /
        # catalog reach into Studio's own helpers directly instead of
        # going over Lab.
        if sender is not None and not isinstance(runtime, LocalTerrariumService):
            self.files: Any = RemoteFiles(sender, node_id)
            self.deploy: Any = _Deploy(sender, node_id)
        else:
            self.files = _Pending("files", "available only on remote nodes")
            self.deploy = _Pending("deploy", "available only on remote nodes")
        self.identity = _Pending("identity", "Unit E")
        self.catalog = _Pending("catalog", "Unit F")

    @property
    def node_id(self) -> str:
        return self._node_id


class NodeMap:
    """Mapping facade over a :class:`MultiNodeTerrariumService`'s nodes.

    Exposes ``studio.nodes[node_id]`` and iteration over connected
    nodes.  Drops handles automatically when the underlying service
    drops a remote (clients disconnecting).
    """

    def __init__(self, service) -> None:
        self._service = service
        self._handles: dict[str, NodeHandle] = {}

    def __getitem__(self, node_id: str) -> NodeHandle:
        # Local: always present.
        if node_id == self._service.node_id:
            handle = self._handles.get(node_id)
            if handle is None:
                handle = NodeHandle(node_id, self._service.service_for(node_id))
                self._handles[node_id] = handle
            return handle
        # Remote: must be currently connected.
        connected = self._service.connected_nodes()
        if node_id not in connected:
            raise KeyError(f"no connected node {node_id!r}")
        handle = self._handles.get(node_id)
        if handle is None or handle.runtime is not self._service.service_for(node_id):
            handle = NodeHandle(
                node_id,
                self._service.service_for(node_id),
                sender=self._service.host,
            )
            self._handles[node_id] = handle
        return handle

    def __contains__(self, node_id: str) -> bool:
        return node_id in self._service.connected_nodes()

    def __iter__(self):
        for node_id in self._service.connected_nodes():
            yield node_id

    def keys(self) -> tuple[str, ...]:
        return self._service.connected_nodes()


def build_node_map_if_multi_node(service) -> "NodeMap | None":
    """Return a :class:`NodeMap` if ``service`` is multi-node, else ``None``.

    Uses duck typing (``connected_nodes`` + ``service_for``) to detect
    a multi-node service so this module doesn't need to import
    :class:`MultiNodeTerrariumService` — keeps the studio surface
    independent of which concrete service is in use.
    """
    if hasattr(service, "connected_nodes") and hasattr(service, "service_for"):
        return NodeMap(service)
    return None


__all__ = ["NodeHandle", "NodeMap", "build_node_map_if_multi_node"]
