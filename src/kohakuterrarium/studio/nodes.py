"""Per-node access handles for Studio in multi-node mode.

``Studio.nodes`` maps node IDs to handles that group each node's runtime,
filesystem, deployment, settings, identity, and catalog capabilities. Local
handles use in-process services where available; remote handles route supported
operations through the laboratory transport. Unsupported capabilities fail on
access rather than silently presenting a partial implementation.
"""

import asyncio
from typing import Any

from kohakuterrarium.studio.deploy import deploy_creature_to_node
from kohakuterrarium.studio.files import RemoteFiles
from kohakuterrarium.studio.identity import drive_settings as _drive_settings
from kohakuterrarium.terrarium.service import LocalTerrariumService, TerrariumService
from kohakuterrarium.terrarium.wire import drive_error_from_body


def _raise_settings_error(body: Any) -> Any:
    """Reconstruct a typed Drive/settings error from a worker envelope, else pass."""
    err = drive_error_from_body(body)
    if err is not None:
        raise err
    if isinstance(body, dict) and isinstance(body.get("error"), dict):
        e = body["error"]
        raise RuntimeError(f"{e.get('kind', 'error')}: {e.get('message', '')}")
    return body


class _RemoteNodeDriveSettings:
    """Client for a worker's remote ``studio.settings`` Drive surface."""

    def __init__(self, sender: Any, node_id: str, *, timeout: float = 30.0) -> None:
        self._sender = sender
        self._node_id = node_id
        self._timeout = timeout

    async def _req(self, type_: str, body: dict[str, Any] | None = None) -> Any:
        result = await self._sender.request(
            to_node=self._node_id,
            namespace="studio.settings",
            type=type_,
            body=body or {},
            timeout=self._timeout,
        )
        return _raise_settings_error(result)

    async def status(self) -> dict[str, Any]:
        return (await self._req("drive_status"))["status"]

    async def get(self) -> dict[str, Any]:
        return await self._req("drive_get")

    async def validate(self, settings: dict[str, Any]) -> dict[str, Any]:
        return await self._req("drive_validate", {"settings": settings})

    async def save(
        self,
        settings: dict[str, Any],
        *,
        expected_revision: str | None = None,
        expected_exists: bool | None = None,
    ) -> dict[str, Any]:
        return await self._req(
            "drive_save",
            {
                "settings": settings,
                "expected_revision": expected_revision,
                "expected_exists": expected_exists,
            },
        )

    async def apply(self) -> dict[str, Any]:
        return (await self._req("drive_apply"))["result"]

    async def runtime_status(self) -> dict[str, Any]:
        return (await self._req("drive_runtime_status"))["runtime_status"]


class _LocalNodeDriveSettings:
    """Manage host-process Drive settings for a local runtime service.

    Settings are stored in the host configuration and applied to its live engine.
    A laboratory coordination host has no agent engine, so applying there reports
    ``restart_required`` instead of configuring an inapplicable runtime.
    """

    def __init__(self, runtime: TerrariumService, node_id: str = "_host") -> None:
        self._runtime = runtime
        self._node_id = node_id

    async def status(self) -> dict[str, Any]:
        return _drive_settings.settings_status(self._node_id)

    async def get(self) -> dict[str, Any]:
        settings = _drive_settings.load_settings()
        return {
            "settings": _drive_settings.settings_to_dict(settings),
            "revision": settings.revision,
        }

    async def validate(self, settings: dict[str, Any]) -> dict[str, Any]:
        _drive_settings.parse_settings(settings)
        return {"ok": True}

    async def save(
        self,
        settings: dict[str, Any],
        *,
        expected_revision: str | None = None,
        expected_exists: bool | None = None,
    ) -> dict[str, Any]:
        saved = await asyncio.to_thread(
            _drive_settings.save_settings,
            settings,
            expected_revision=expected_revision,
            expected_exists=expected_exists,
        )
        return {
            "ok": True,
            "revision": saved.settings.revision,
            "durability": saved.durability.value,
        }

    async def apply(self) -> dict[str, Any]:
        # Coordination-only hosts expose no agent engine; treat that state as a
        # deferred application rather than an invalid settings document.
        try:
            engine = self._runtime.engine
        except Exception:
            engine = None
        if engine is None:
            return {
                "result": _drive_settings.RESTART_REQUIRED,
                "desired_revision": _drive_settings.current_revision(),
                "running_revision": None,
                "warnings": [
                    "this node runs no Drive-capable engine; settings apply on a "
                    "real execution node"
                ],
            }
        return _drive_settings.apply_runtime(engine, node=self._node_id)

    async def runtime_status(self) -> dict[str, Any]:
        status = await self._runtime.drive_runtime_status()
        return status.to_dict()


class _NodeSettings:
    """The ``.settings`` namespace on a :class:`NodeHandle` (Drive settings)."""

    def __init__(self, drives: Any) -> None:
        self.drives = drives


class _Pending:
    """Fail explicitly when a node capability is unavailable."""

    def __init__(self, name: str, unit: str) -> None:
        self._name = name
        self._unit = unit

    def __getattr__(self, attr: str):
        raise NotImplementedError(
            f"NodeHandle.{self._name} lands in {self._unit}; "
            f"not available yet (tried to access .{attr})"
        )


class _Deploy:
    """Bind creature deployment requests to one target node."""

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
        """Deploy a local creature directory and return its worker-side path.

        The returned absolute path is suitable for a subsequent remote
        ``add_creature`` call.
        """
        return await deploy_creature_to_node(
            self._sender,
            self._target_node,
            local_path,
            name=name,
            timeout=timeout,
        )


class NodeHandle:
    """Expose the runtime and supported auxiliary services for one node."""

    def __init__(
        self,
        node_id: str,
        runtime: TerrariumService,
        *,
        sender: Any = None,
    ) -> None:
        self._node_id = node_id
        self.runtime: TerrariumService = runtime
        # A sender identifies a remotely addressable worker. Local runtimes
        # bypass laboratory transport and use host-side services instead.
        if sender is not None and not isinstance(runtime, LocalTerrariumService):
            self.files: Any = RemoteFiles(sender, node_id)
            self.deploy: Any = _Deploy(sender, node_id)
            # Remote settings must be read and applied by the target worker so
            # filesystem state and the live runtime remain on the same node.
            self.settings: Any = _NodeSettings(
                _RemoteNodeDriveSettings(sender, node_id)
            )
        else:
            self.files = _Pending("files", "available only on remote nodes")
            self.deploy = _Pending("deploy", "available only on remote nodes")
            self.settings = _NodeSettings(_LocalNodeDriveSettings(runtime, node_id))
        self.identity = _Pending("identity", "Unit E")
        self.catalog = _Pending("catalog", "Unit F")

    @property
    def node_id(self) -> str:
        return self._node_id


class NodeMap:
    """Map connected node IDs to lazily constructed service handles.

    Remote membership is checked on every lookup, and cached handles are replaced
    when a reconnect yields a new runtime service instance.
    """

    def __init__(self, service) -> None:
        self._service = service
        self._handles: dict[str, NodeHandle] = {}

    def __getitem__(self, node_id: str) -> NodeHandle:
        # The service's own node remains addressable independently of remote
        # membership and can safely reuse its cached handle.
        if node_id == self._service.node_id:
            handle = self._handles.get(node_id)
            if handle is None:
                handle = NodeHandle(node_id, self._service.service_for(node_id))
                self._handles[node_id] = handle
            return handle
        # Remote handles are valid only while membership reports the node as
        # connected; stale cached handles must not outlive disconnection.
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
    """Return a node map for services exposing multi-node membership.

    Capability detection avoids importing a concrete laboratory service and
    keeps the Studio facade dependent only on the behavior it needs.
    """
    if hasattr(service, "connected_nodes") and hasattr(service, "service_for"):
        return NodeMap(service)
    return None


__all__ = ["NodeHandle", "NodeMap", "build_node_map_if_multi_node"]
