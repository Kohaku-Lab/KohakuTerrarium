"""TerrariumService spanning N remote worker nodes (lab-host mode).

``MultiNodeTerrariumService`` is what Studio holds in lab-host mode.
**The host process runs no agents** — only worker processes run
agents.  This service therefore composes *only* a dict
``{node_id: RemoteTerrariumService}`` for every connected worker; there
is no host-local :class:`LocalTerrariumService` and no host agent
engine.  Local is local, multi-node is multi-node — never mixed.

It keeps:

- A ``creature_id → home_node`` registry rebuilt from ``list_creatures``
  fan-out, and kept up-to-date as ``add_creature`` / ``remove_creature``
  succeed.
- An optional *coordination* engine — a bare :class:`Terrarium` on the
  host used **only** to host cross-node channel objects for the
  broadcast / output-wire forwarders.  It never runs an agent: nothing
  calls ``add_creature`` on it.

Routing rules — global reads fan out across workers + union;
per-creature ops resolve the home node first.  ``add_creature``
requires an ``on_node`` naming a connected worker — ``"_host"`` is
rejected (the host runs no agents).

Streaming methods (``chat``, ``subscribe``) yield from whichever
worker owns the creature (chat) or fan out across all workers
(subscribe).
"""

import asyncio
from collections.abc import AsyncIterator
from typing import Any

from kohakuterrarium.laboratory._internal.host import HostEngine
from kohakuterrarium.laboratory.streams import StreamDemux
from kohakuterrarium.terrarium.drive.multi_node import DriveRouteCache
from kohakuterrarium.terrarium.drive.multi_node_ops import MultiNodeDriveServiceMixin
from kohakuterrarium.terrarium.drive.service_protocol import (
    DriveServiceUnsupportedMixin,
)
from kohakuterrarium.terrarium.engine import Terrarium
from kohakuterrarium.terrarium.events import (
    ConnectionResult,
    DisconnectionResult,
    EngineEvent,
    EventFilter,
)
from kohakuterrarium.terrarium.multi_node_channels import (
    channel_history as _cluster_channel_history,
    cluster_members_for as _cluster_members_for_fn,
    list_channels as _cluster_list_channels,
)
from kohakuterrarium.terrarium.multi_node_cluster import fold_clusters
from kohakuterrarium.terrarium.multi_node_replication import (
    cross_node_connect,
    cross_node_disconnect,
    drop_cross_sub,
    ensure_channel_replicated,
    find_channel_elsewhere,
    local_broadcast_adapter,
    record_cross_sub,
)
from kohakuterrarium.terrarium.multi_node_runtime_options import (
    MultiNodeRuntimeOptionsMixin,
)
from kohakuterrarium.terrarium.multi_node_routing import (
    creature_graph_id,
    list_creatures_fanout,
    purge_node_caches,
    resolve_graph_home,
    resolve_home,
    route_per_creature,
    runtime_graph_snapshot_fanout,
    stream_subscribe,
)
from kohakuterrarium.terrarium.remote_service import RemoteTerrariumService
from kohakuterrarium.terrarium.service import (
    CreatureInfo,
    TerrariumService,
)
from kohakuterrarium.terrarium.service_dto import BranchMutationResult
from kohakuterrarium.terrarium.topology import (
    ChannelInfo,
    GraphTopology,
    TopologyDelta,
)
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

HOST_NODE = "_host"


class CrossNodeNotSupportedError(RuntimeError):
    """Raised for operations that cannot be routed across nodes."""


class MultiNodeTerrariumService(
    MultiNodeDriveServiceMixin,
    MultiNodeRuntimeOptionsMixin,
    DriveServiceUnsupportedMixin,
):
    """Composite TerrariumService for the controller (lab-host mode).
    Routes every agent operation to a connected worker.  The host runs
    no agents — :attr:`engine` raises and ``service_for("_host")`` is a
    ``KeyError``.  Drive ops route to the graph-repository home worker via
    ``MultiNodeDriveServiceMixin`` + the ``drive_id -> home_node`` route cache.
    """

    def __init__(
        self,
        *,
        host: HostEngine,
        coordination_engine: Terrarium | None = None,
    ) -> None:
        self._host = host
        self._coordination_engine = coordination_engine
        self._demux = StreamDemux(host)
        self._remotes: dict[str, RemoteTerrariumService] = {}
        self._membership_epoch = 0
        self._home: dict[str, str] = {}  # creature_id → node_id
        self._drive_routes = DriveRouteCache()  # drive/delivery/proposal → home
        # name → (node_id, creature_id).  Populated as a side effect of
        # ``list_creatures``.  Read by the controller's
        # :class:`TerrariumOutputWireAdapter` target resolver to route
        # cross-node output-wiring emits without an async hop.
        self._creature_name_cache: dict[str, set[tuple[str, str, str]]] = {}
        # Cross-node subscription bookkeeping — keyed by
        # ``(my_node, peer_node, graph_id, channel)`` to a refcount.
        self._cross_subs: dict[tuple[str, str, str, str], int] = {}
        # Cluster-graph linkage: a set of unordered pairs of
        # ``(node_id, engine_graph_id)`` tuples that have been linked
        # by a cross-node channel replication.  ``runtime_graph_snapshot``
        # does a union-find over this set to render multiple engine
        # graphs as ONE cluster graph in the UI — the
        # "Laboratory makes N terrariums look like 1" principle.  An
        # ordinary single-host graph has no entries here and renders
        # as itself.
        self._cluster_links: set[frozenset[tuple[str, str]]] = set()
        # Studio-tier session metadata lookup, injected at boot — used
        # to enrich ``runtime_graph_snapshot`` graphs with name / kind.
        self._runtime_graph_meta_lookup = None

    @property
    def node_id(self) -> str:
        return HOST_NODE

    @property
    def engine(self) -> Terrarium:
        """Always raises — the lab-host runs no host agent engine.

        Studio code reaching for ``service.engine`` (via ``as_engine``)
        is the dual local/remote mixing this redo removed.  Per-creature
        and per-graph ops MUST route through the ``TerrariumService``
        Protocol so they reach the worker that actually hosts the
        creature.  The cross-node channel-coordination engine, when one
        exists, is reachable via :attr:`coordination_engine` — it is
        NOT an agent runtime and must not be used as one.
        """
        raise RuntimeError(
            "lab-host mode runs no host agent engine — route through the "
            "TerrariumService Protocol (only worker processes run agents)"
        )

    @property
    def coordination_engine(self) -> Terrarium | None:
        """The host's cross-node channel-coordination engine, if any.

        Runs no agents — exists only so the broadcast / output-wire
        forwarders have an engine to hang cross-node channel objects on.
        Back-compat ``get_engine()`` returns this so unmigrated routes
        get an (always-empty) engine instead of a 500.
        """
        return self._coordination_engine

    def set_runtime_graph_meta_lookup(self, lookup) -> None:
        """Inject the studio-tier ``session_id -> meta dict`` lookup.

        Mirrors :meth:`LocalTerrariumService.set_runtime_graph_meta_lookup`
        so ``runtime_graph_snapshot`` can annotate each worker-sourced
        graph with its studio name / kind / created_at.
        """
        self._runtime_graph_meta_lookup = lookup

    @property
    def demux(self) -> StreamDemux:
        return self._demux

    @property
    def host(self) -> HostEngine:
        """The Lab host this service drives.  Used by NodeHandle to issue
        APP requests against remote workers."""
        return self._host

    # ------------------------------------------------------------------
    # Membership management — call these as clients join/leave.
    # ------------------------------------------------------------------

    def add_remote(self, node_id: str) -> RemoteTerrariumService:
        """Register a connected client as a remote terrarium.

        Schedules a background ``list_creatures`` fan-out so the
        cluster-wide ``_creature_name_cache`` warms up before the
        first cross-node output-wiring emit (the resolver is sync
        and can't pull this on demand).
        """
        if node_id in self._remotes:
            return self._remotes[node_id]
        remote = RemoteTerrariumService(self._host, node_id, demux=self._demux)
        self._remotes[node_id] = remote
        self._membership_epoch = getattr(self, "_membership_epoch", 0) + 1
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return remote
        loop.create_task(self._warm_caches_on_join(node_id))
        return remote

    async def _warm_caches_on_join(self, node_id: str) -> None:
        """Best-effort cache refresh after a worker joins."""
        try:
            await self.list_creatures()
        except Exception:
            logger.debug("warm-cache list_creatures failed on %s", node_id)

    def drop_remote(self, node_id: str) -> None:
        """Remove a client and purge every per-node cache.

        Delegates to :func:`multi_node_routing.purge_node_caches`, then drops the
        Drive route cache's active routes (sticky last-home survives so a read can
        still report "home offline").
        """
        if node_id not in self._remotes:
            return
        purge_node_caches(self, node_id)
        self._drive_routes.purge_node(node_id)
        self._membership_epoch = getattr(self, "_membership_epoch", 0) + 1

    def connected_nodes(self) -> tuple[str, ...]:
        """The connected *worker* nodes — the host is not in this set.

        The host runs no agents, so it is not a node anything is
        routed to.  ``lifecycle.list_sessions`` cross-checks ``_meta``
        ``on_node`` values against this set to purge zombie sessions.
        """
        return tuple(self._remotes.keys())

    def service_for(self, node_id: str) -> TerrariumService:
        """Resolve a connected worker's service.

        ``"_host"`` (or any unknown id) raises ``KeyError`` — the host
        runs no agents, so there is no host service to route to.
        """
        svc = self._remotes.get(node_id)
        if svc is None:
            raise KeyError(
                f"node {node_id!r} is not a connected worker "
                "(the lab-host runs no agents)"
            )
        return svc

    # ------------------------------------------------------------------
    # Global reads — fan out across workers, then union.
    # ------------------------------------------------------------------

    async def list_creatures(self) -> tuple[CreatureInfo, ...]:
        """Fan out across workers in parallel; merge prior cache on failure.

        Thin delegator to :func:`multi_node_routing.list_creatures_fanout`.
        """
        return await list_creatures_fanout(self)

    async def list_graphs(self) -> tuple[GraphTopology, ...]:
        results: list[GraphTopology] = []
        for node_id, svc in list(self._remotes.items()):
            try:
                results.extend(await svc.list_graphs())
            except Exception:
                logger.exception("list_graphs failed on %s", node_id)
        return tuple(results)

    async def status_snapshot(self) -> dict[str, Any]:
        snap: dict[str, Any] = {}
        for node_id, svc in list(self._remotes.items()):
            try:
                snap[node_id] = await svc.status_snapshot()
            except Exception:
                logger.exception("status_snapshot failed on %s", node_id)
                snap[node_id] = {"error": "unreachable"}
        return snap

    # ------------------------------------------------------------------
    # Per-creature reads — resolve home first.
    # ------------------------------------------------------------------

    async def get_creature_info(self, creature_id: str) -> CreatureInfo | None:
        try:
            return await self._route_per_creature(
                creature_id, lambda svc: svc.get_creature_info(creature_id)
            )
        except KeyError:
            return None

    async def creature_status(self, creature_id: str) -> dict[str, Any] | None:
        try:
            return await self._route_per_creature(
                creature_id, lambda svc: svc.creature_status(creature_id)
            )
        except KeyError:
            return None

    async def get_graph(self, graph_id: str) -> GraphTopology | None:
        # Ask each connected worker — the host hosts no graphs.
        for svc in self._remotes.values():
            g = await svc.get_graph(graph_id)
            if g is not None:
                return g
        return None

    async def list_channels(self, graph_id: str) -> tuple[ChannelInfo, ...]:
        """Thin delegator to :func:`multi_node_channels.list_channels`."""
        return await _cluster_list_channels(self, graph_id)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def add_creature(
        self,
        config: Any,
        *,
        graph_id: str | None = None,
        creature_id: str | None = None,
        is_privileged: bool = False,
        parent_creature_id: str | None = None,
        start: bool = True,
        pwd: str | None = None,
        llm: Any = None,
        on_node: str = HOST_NODE,
        name: str | None = None,
    ) -> CreatureInfo:
        """Spawn a creature, routed to ``on_node``.

        Workers must already be connected — pass a known ``node_id``
        from :meth:`connected_nodes`.  ``name`` is the user's chosen
        display-name override, threaded to the target node so the
        spawned creature carries it.
        """
        svc = self.service_for(on_node)
        info = await svc.add_creature(
            config,
            graph_id=graph_id,
            creature_id=creature_id,
            is_privileged=is_privileged,
            parent_creature_id=parent_creature_id,
            start=start,
            pwd=pwd,
            llm=llm,
            name=name,
        )
        self._home[info.creature_id] = on_node
        identity = (info.graph_id, on_node, info.creature_id)
        self._creature_name_cache.setdefault(info.creature_id, set()).add(identity)
        self._creature_name_cache.setdefault(info.name, set()).add(identity)
        config_name = getattr(info, "config_name", None)
        if config_name:
            self._creature_name_cache.setdefault(config_name, set()).add(identity)
        return info

    async def remove_creature(self, creature_id: str) -> None:
        await self._route_per_creature(
            creature_id, lambda svc: svc.remove_creature(creature_id)
        )
        self._home.pop(creature_id, None)
        # Also purge every name-cache entry whose value's creature_id
        # matches the removed creature.  The sync output-wire resolver
        # reads this cache without an async hop, so a stale entry would
        # keep routing emits to the dead address until the next
        # ``list_creatures`` fan-out.
        for key, entries in list(self._creature_name_cache.items()):
            retained = {entry for entry in entries if entry[2] != creature_id}
            if retained:
                self._creature_name_cache[key] = retained
            else:
                self._creature_name_cache.pop(key, None)

    async def start_creature(self, creature_id: str) -> None:
        await self._route_per_creature(
            creature_id, lambda svc: svc.start_creature(creature_id)
        )

    async def stop_creature(self, creature_id: str) -> None:
        await self._route_per_creature(
            creature_id, lambda svc: svc.stop_creature(creature_id)
        )

    async def shutdown(self) -> None:
        """No-op — the host runs no agent engine to tear down.

        Worker engines live in separate processes and are torn down by
        their own ``kt lab-client`` lifecycle; the host's Lab transport
        is stopped by the API lifespan's ``host_engine.stop()``.  The
        coordination engine (if any) is shut down by the lifespan too.
        """
        return None

    # ------------------------------------------------------------------
    # Channel mutations route through the graph's home worker.
    # Cross-node creature wiring is coordinated separately through replication.
    # ------------------------------------------------------------------

    async def add_channel(
        self,
        graph_id: str,
        name: str,
        description: str = "",
    ) -> ChannelInfo:
        node_id = await self._resolve_graph_home(graph_id)
        return await self.service_for(node_id).add_channel(graph_id, name, description)

    async def remove_channel(self, graph_id: str, name: str) -> TopologyDelta:
        node_id = await self._resolve_graph_home(graph_id)
        return await self.service_for(node_id).remove_channel(graph_id, name)

    async def channel_history(
        self,
        graph_id: str,
        name: str,
        *,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """Thin delegator to :func:`multi_node_channels.channel_history`."""
        return await _cluster_channel_history(self, graph_id, name, limit=limit)

    async def send_channel_message(
        self,
        graph_id: str,
        name: str,
        content: str | list[dict[str, Any]],
        *,
        sender: str = "human",
    ) -> str:
        node_id = await self._resolve_graph_home(graph_id)
        return await self.service_for(node_id).send_channel_message(
            graph_id, name, content, sender=sender
        )

    async def connect(
        self,
        sender_id: str,
        receiver_id: str,
        *,
        channel: str | None = None,
    ) -> ConnectionResult:
        sender_home = await self._resolve_home(sender_id)
        receiver_home = await self._resolve_home(receiver_id)
        if sender_home is None:
            raise KeyError(sender_id)
        if receiver_home is None:
            raise KeyError(receiver_id)
        if sender_home == receiver_home:
            return await self.service_for(sender_home).connect(
                sender_id, receiver_id, channel=channel
            )
        return await cross_node_connect(
            self,
            sender_id,
            receiver_id,
            sender_home,
            receiver_home,
            channel,
        )

    async def disconnect(
        self,
        sender_id: str,
        receiver_id: str,
        *,
        channel: str | None = None,
    ) -> DisconnectionResult:
        sender_home = await self._resolve_home(sender_id)
        receiver_home = await self._resolve_home(receiver_id)
        if sender_home is None:
            raise KeyError(sender_id)
        if receiver_home is None:
            raise KeyError(receiver_id)
        if sender_home == receiver_home:
            return await self.service_for(sender_home).disconnect(
                sender_id, receiver_id, channel=channel
            )
        return await cross_node_disconnect(
            self,
            sender_id,
            receiver_id,
            sender_home,
            receiver_home,
            channel,
        )

    async def _local_broadcast_adapter(self):
        """Thin delegator to :func:`multi_node_replication.local_broadcast_adapter`."""
        return await local_broadcast_adapter(self)

    def _record_cross_sub(
        self, my_node: str, peer_node: str, graph_id: str, channel: str
    ) -> None:
        """Thin delegator to :func:`multi_node_replication.record_cross_sub`."""
        record_cross_sub(self, my_node, peer_node, graph_id, channel)

    def _drop_cross_sub(
        self, my_node: str, peer_node: str, graph_id: str, channel: str
    ) -> None:
        """Thin delegator to :func:`multi_node_replication.drop_cross_sub`."""
        drop_cross_sub(self, my_node, peer_node, graph_id, channel)

    # ------------------------------------------------------------------
    # Interaction
    # ------------------------------------------------------------------

    async def inject_input(
        self,
        creature_id: str,
        message: str | list[dict[str, Any]],
        *,
        source: str = "chat",
    ) -> None:
        await self._route_per_creature(
            creature_id,
            lambda svc: svc.inject_input(creature_id, message, source=source),
        )

    # ------------------------------------------------------------------
    # Per-creature control — route-by-home
    # ------------------------------------------------------------------

    async def interrupt(self, creature_id: str) -> None:
        await self._route_per_creature(
            creature_id, lambda svc: svc.interrupt(creature_id)
        )

    async def list_jobs(self, creature_id: str) -> list[dict[str, Any]]:
        return await self._route_per_creature(
            creature_id, lambda svc: svc.list_jobs(creature_id)
        )

    async def stop_job(self, creature_id: str, job_id: str) -> bool:
        return await self._route_per_creature(
            creature_id, lambda svc: svc.stop_job(creature_id, job_id)
        )

    async def promote_job(self, creature_id: str, job_id: str) -> bool:
        return await self._route_per_creature(
            creature_id, lambda svc: svc.promote_job(creature_id, job_id)
        )

    # ------------------------------------------------------------------
    # Per-creature chat / state / mutation / wiring — all route by home.
    # ------------------------------------------------------------------

    async def chat_history(self, creature_id: str) -> dict[str, Any]:
        return await self._route_per_creature(
            creature_id, lambda svc: svc.chat_history(creature_id)
        )

    async def chat_branches(self, creature_id: str) -> list[dict[str, Any]]:
        return await self._route_per_creature(
            creature_id, lambda svc: svc.chat_branches(creature_id)
        )

    async def regenerate(
        self,
        creature_id: str,
        *,
        turn_index: int | None = None,
        branch_view: dict[int, int] | None = None,
        request_id: str | None = None,
    ) -> BranchMutationResult:
        return await self._route_per_creature(
            creature_id,
            lambda svc: svc.regenerate(
                creature_id,
                turn_index=turn_index,
                branch_view=branch_view,
                request_id=request_id,
            ),
        )

    async def edit_message(
        self,
        creature_id: str,
        msg_idx: int,
        content: str | list[dict[str, Any]],
        *,
        turn_index: int | None = None,
        user_position: int | None = None,
        branch_view: dict[int, int] | None = None,
        request_id: str | None = None,
    ) -> BranchMutationResult:
        return await self._route_per_creature(
            creature_id,
            lambda svc: svc.edit_message(
                creature_id,
                msg_idx,
                content,
                turn_index=turn_index,
                user_position=user_position,
                branch_view=branch_view,
                request_id=request_id,
            ),
        )

    async def rewind(self, creature_id: str, msg_idx: int) -> None:
        await self._route_per_creature(
            creature_id, lambda svc: svc.rewind(creature_id, msg_idx)
        )

    async def execute_command(
        self,
        creature_id: str,
        command: str,
        args: dict[str, Any] | str | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        # The trusted ``principal`` and ``is_operator`` context rides to the home worker.
        return await self._route_per_creature(
            creature_id,
            lambda svc: svc.execute_command(creature_id, command, args, **kwargs),
        )

    async def list_output_wiring(self, creature_id: str) -> list[dict[str, Any]]:
        return await self._route_per_creature(
            creature_id, lambda svc: svc.list_output_wiring(creature_id)
        )

    async def wire_output(
        self,
        creature_id: str,
        target: str | dict[str, Any],
    ) -> dict[str, Any]:
        return await self._route_per_creature(
            creature_id, lambda svc: svc.wire_output(creature_id, target)
        )

    async def unwire_output(self, creature_id: str, edge_id: str) -> bool:
        return await self._route_per_creature(
            creature_id, lambda svc: svc.unwire_output(creature_id, edge_id)
        )

    async def unwire_output_sink(self, creature_id: str, sink_id: str) -> bool:
        return await self._route_per_creature(
            creature_id, lambda svc: svc.unwire_output_sink(creature_id, sink_id)
        )

    async def wire_creature(
        self,
        graph_id: str,
        creature_id: str,
        channel: str,
        direction: str,
        *,
        enabled: bool = True,
    ) -> None:
        """Wire one creature half of a channel edge on its home node.

        ``"root"`` resolves against the graph's home (the privileged
        creature lives on whichever node owns the graph) — not via the
        ``_home`` registry, which is keyed by concrete creature id.

        Cross-node lazy replication: when ``enabled`` and the named
        channel does not yet exist on the target creature's graph but
        *does* exist somewhere else in the cluster, the channel is
        replicated on the target's graph and a broadcast cross-
        subscription is installed matching the wire direction.  This
        preserves the user-named channel across worker boundaries —
        without it, the graph editor's "drag a wire from channel X to a
        creature on a different worker" gesture would 400 and fall
        back to an auto-named ``a_to_b`` channel that ignored the
        user's name.
        """
        if creature_id == "root":
            node_id = await self._resolve_graph_home(graph_id)
            if enabled:
                await self._ensure_channel_replicated(node_id, graph_id, channel)
            await self.service_for(node_id).wire_creature(
                graph_id, creature_id, channel, direction, enabled=enabled
            )
            return
        # Cluster-graph rewrite: the frontend may pass the cluster's
        # primary graph_id (e.g. worker-1's graph_a) for a creature
        # that actually lives on a peer worker's engine-graph
        # (worker-2's graph_b).  Each worker's engine only knows
        # about its own graphs, so we rewrite ``graph_id`` to the
        # creature's ACTUAL home graph before crossing the wire.
        target_home = await self._resolve_home(creature_id)
        target_graph = await self._creature_graph_id(creature_id) or graph_id
        if enabled and target_home is not None:
            await self._ensure_channel_replicated(
                target_home, target_graph, channel, direction=direction
            )
        await self._route_per_creature(
            creature_id,
            lambda svc: svc.wire_creature(
                target_graph, creature_id, channel, direction, enabled=enabled
            ),
        )

    async def _creature_graph_id(self, creature_id: str) -> str | None:
        """Thin delegator to :func:`multi_node_routing.creature_graph_id`."""
        return await creature_graph_id(self, creature_id)

    async def _ensure_channel_replicated(
        self,
        target_node: str,
        target_graph: str,
        channel: str,
        *,
        direction: str | None = None,
    ) -> None:
        """Thin delegator to :func:`multi_node_replication.ensure_channel_replicated`."""
        await ensure_channel_replicated(
            self, target_node, target_graph, channel, direction=direction
        )

    async def _find_channel_elsewhere(
        self, channel: str, *, exclude: str, graph_id: str
    ) -> tuple[str, str] | None:
        """Thin delegator to graph-scoped channel lookup."""
        return await find_channel_elsewhere(
            self,
            channel,
            exclude=exclude,
            graph_id=graph_id,
        )

    async def attach_policies(self, creature_id: str) -> list[str]:
        return await self._route_per_creature(
            creature_id, lambda svc: svc.attach_policies(creature_id)
        )

    async def session_attach_policies(self, session_id: str) -> list[str]:
        # Cluster sessions span multiple workers, and each member's worker may
        # advertise its own subset of policies (e.g. one has
        # an input module → IO, another has channels → OBSERVER). Union
        # across cluster members so the UI shows the full set rather
        # than only the primary's slice. ``cluster_members_for`` returns
        # ``[]`` for non-cluster graphs, where we fall back to the
        # single-home resolve.
        members = self._cluster_members_for(session_id)
        if members:
            seen: list[str] = []
            for node_id, member_sid in members:
                try:
                    svc = self.service_for(node_id)
                except KeyError:
                    continue
                try:
                    member_policies = await svc.session_attach_policies(member_sid)
                except Exception:
                    continue
                for p in member_policies:
                    if p not in seen:
                        seen.append(p)
            return seen
        # Route to the worker that hosts the graph.  When no connected
        # worker hosts it (not-yet-known / already-gone session) there
        # are no policies to report — return empty rather than 500.
        try:
            node_id = await self._resolve_graph_home(session_id)
        except KeyError:
            return []
        return await self.service_for(node_id).session_attach_policies(session_id)

    async def runtime_graph_snapshot(self) -> dict[str, Any]:
        """Fan-out across workers, then fold cross-linked engine graphs
        into cluster graphs so the UI sees ONE graph per logical group.

        Per-graph ``node_id`` is set by the originating remote service.
        Engine graphs that have been linked by a cross-node channel
        wire (tracked in :attr:`_cluster_links`) are merged into a
        synthesized cluster graph whose ``graph_id`` is the
        lexicographically-smallest member's id and whose ``members``
        list carries the per-node engine-graph references the frontend
        needs to issue ops against the underlying engines.  Single
        engine graphs without any cross-link surface unchanged.

        Each graph is then enriched with its studio-tier session
        metadata (name / kind / created_at) via the injected lookup so
        the graph editor renders worker graphs with proper labels.

        The "Lab makes N terrariums look like 1" UX invariant: a user
        who wires a channel cross-node sees one graph, not two.
        """
        engine_graphs, version = await runtime_graph_snapshot_fanout(self)
        clustered = self._fold_clusters(engine_graphs)

        lookup = self._runtime_graph_meta_lookup
        if lookup is not None:
            for graph in clustered:
                meta = lookup(graph.get("graph_id", "")) or {}
                for key in ("name", "kind", "created_at", "config_path"):
                    if key in meta and key not in graph:
                        graph[key] = meta[key]
        return {"version": version, "graphs": clustered}

    def _fold_clusters(
        self, engine_graphs: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Thin delegator to :func:`multi_node_cluster.fold_clusters`."""
        return fold_clusters(engine_graphs, self._cluster_links)

    async def default_workdir(self, node_id: str) -> dict[str, str]:
        """Return a worker's default working-directory hints.

        Used by ``GET /api/configs/server-info?on_node=<node_id>`` to
        seed the New Creature / New Terrarium modal's "Working
        directory" field with a worker-side path rather than the host's
        ``os.getcwd()``. Routes through the ``terrarium.files``
        APP namespace ``getcwd`` verb on the named worker.

        Returns a dict with ``cwd``, ``home``, and ``platform`` keys.
        Raises ``KeyError`` if ``node_id`` is not a connected worker.
        """
        if node_id == HOST_NODE or node_id not in self._remotes:
            raise KeyError(
                f"node {node_id!r} is not a connected worker; "
                "cannot query its default working directory"
            )
        body = await self._host.request(
            to_node=node_id,
            namespace="terrarium.files",
            type="getcwd",
            body={},
            timeout=10.0,
        )
        if isinstance(body, dict) and isinstance(body.get("error"), dict):
            err = body["error"]
            raise RuntimeError(
                f"worker {node_id!r} getcwd failed: "
                f"{err.get('kind')}: {err.get('message')}"
            )
        return {
            "cwd": str((body or {}).get("cwd", "")),
            "home": str((body or {}).get("home", "")),
            "platform": str((body or {}).get("platform", "")),
        }

    def chat(
        self,
        creature_id: str,
        message: str | list[dict[str, Any]],
    ) -> AsyncIterator[str]:
        return self._stream_chat(creature_id, message)

    async def _stream_chat(self, creature_id, message):
        node_id = await self._resolve_home(creature_id)
        if node_id is None:
            raise KeyError(creature_id)
        async for chunk in self.service_for(node_id).chat(creature_id, message):
            yield chunk

    def subscribe(
        self,
        filter: EventFilter | None = None,
    ) -> AsyncIterator[EngineEvent]:
        return self._stream_subscribe(filter)

    async def _stream_subscribe(self, filter):
        """Thin delegator to :func:`multi_node_routing.stream_subscribe`."""
        async for ev in stream_subscribe(self, filter):
            yield ev

    # ------------------------------------------------------------------
    # Internal: home resolution
    # ------------------------------------------------------------------

    async def _resolve_home(self, creature_id: str) -> str | None:
        """Thin delegator to :func:`multi_node_routing.resolve_home`."""
        return await resolve_home(self, creature_id)

    async def _route_per_creature(self, creature_id: str, fn):
        """Thin delegator to :func:`multi_node_routing.route_per_creature`."""
        return await route_per_creature(self, creature_id, fn)

    async def _resolve_graph_home(self, graph_id: str) -> str:
        """Thin delegator to :func:`multi_node_routing.resolve_graph_home`."""
        return await resolve_graph_home(self, graph_id)

    def _cluster_members_for(self, graph_id: str) -> list[tuple[str, str]]:
        """Thin delegator to :func:`multi_node_channels.cluster_members_for`."""
        return _cluster_members_for_fn(self, graph_id)


__all__ = [
    "CrossNodeNotSupportedError",
    "MultiNodeTerrariumService",
    "HOST_NODE",
]
