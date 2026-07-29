"""Cross-node channel replication helpers for :mod:`multi_node_service`.

The composite ``MultiNodeTerrariumService`` keeps every worker's engine
graph isolated; cross-node ``connect`` / ``disconnect`` and lazy
``wire_creature`` replication need a small body of shared logic to:

- Replicate a named channel onto a peer node's graph (idempotently).
- Find a channel only inside the same logical cluster component.
- Cross-subscribe the broadcast adapter so messages flow.
- Track cluster-link bookkeeping for the runtime-graph fold.
- Track per-subscription refcounts for clean teardown.

Each helper receives the service explicitly because replication mutates its
cluster-link and cross-subscription caches and uses its coordination adapter.
"""

import asyncio
from typing import TYPE_CHECKING, Any

from kohakuterrarium.terrarium.events import (
    ConnectionResult,
    DisconnectionResult,
)
from kohakuterrarium.terrarium.graph_identity import GraphNameConflictError
from kohakuterrarium.terrarium.multi_node_channels import cluster_members_for
from kohakuterrarium.utils.logging import get_logger

if TYPE_CHECKING:
    from kohakuterrarium.terrarium.multi_node_service import (
        MultiNodeTerrariumService,
    )

logger = get_logger(__name__)


async def local_broadcast_adapter(service: "MultiNodeTerrariumService"):
    """Reach the host-side cross-node broadcast adapter.

    The adapter is hung on the coordination engine by ``api/app.py``
    (``TerrariumBroadcastAdapter(coordination_engine, host)``).
    Returns ``None`` when no coordination engine is wired (unit-test
    setups, or a lab-host built without cross-node channels)."""
    if service._coordination_engine is None:
        return None
    return getattr(service._coordination_engine, "_broadcast_adapter", None)


def record_cross_sub(
    service: "MultiNodeTerrariumService",
    my_node: str,
    peer_node: str,
    graph_id: str,
    channel: str,
) -> None:
    """Track cross-node subscriptions for teardown bookkeeping."""
    key = (my_node, peer_node, graph_id, channel)
    service._cross_subs[key] = service._cross_subs.get(key, 0) + 1


def drop_cross_sub(
    service: "MultiNodeTerrariumService",
    my_node: str,
    peer_node: str,
    graph_id: str,
    channel: str,
) -> None:
    key = (my_node, peer_node, graph_id, channel)
    if service._cross_subs.get(key, 0) <= 1:
        service._cross_subs.pop(key, None)
    else:
        service._cross_subs[key] -= 1


def record_cluster_link(
    service: "MultiNodeTerrariumService",
    link: frozenset[tuple[str, str]],
) -> None:
    """Record one independently removable bridge between two engine graphs."""
    refs = getattr(service, "_cluster_link_refs", None)
    if refs is None:
        refs = {}
        service._cluster_link_refs = refs
    current = refs.get(link)
    if current is None:
        current = 1 if link in service._cluster_links else 0
    refs[link] = current + 1
    service._cluster_links.add(link)


def drop_cluster_link(
    service: "MultiNodeTerrariumService",
    link: frozenset[tuple[str, str]],
) -> None:
    """Remove one bridge while retaining the logical link for remaining channels."""
    refs = getattr(service, "_cluster_link_refs", None)
    if refs is None:
        refs = {}
        service._cluster_link_refs = refs
    current = refs.get(link)
    if current is None:
        current = 1 if link in service._cluster_links else 0
    if current <= 1:
        refs.pop(link, None)
        service._cluster_links.discard(link)
        return
    refs[link] = current - 1


async def _graph_aliases(remote: Any, graph_id: str) -> dict[str, set[str]]:
    aliases: dict[str, set[str]] = {}
    for info in await remote.list_creatures():
        if info.graph_id != graph_id:
            continue
        for alias in {info.name, getattr(info, "config_name", None)}:
            if alias:
                aliases.setdefault(alias, set()).add(info.creature_id)
    return aliases


def _logical_graph_members(
    service: "MultiNodeTerrariumService",
    node_id: str,
    graph_id: str,
) -> set[tuple[str, str]]:
    """Return the complete logical component containing one worker graph."""
    members = set(cluster_members_for(service, graph_id))
    return members or {(node_id, graph_id)}


async def _logical_graph_aliases(
    service: "MultiNodeTerrariumService",
    members: set[tuple[str, str]],
) -> dict[str, set[str]]:
    """Collect aliases across every worker-local graph in one component."""
    alias_sets = await asyncio.gather(
        *(
            _graph_aliases(service.service_for(node_id), graph_id)
            for node_id, graph_id in sorted(members)
        )
    )
    aliases: dict[str, set[str]] = {}
    for member_aliases in alias_sets:
        for alias, creature_ids in member_aliases.items():
            aliases.setdefault(alias, set()).update(creature_ids)
    return aliases


async def _guard_cross_node_name_conflicts(
    service: "MultiNodeTerrariumService",
    sender_home: str,
    sender_graph: str,
    receiver_home: str,
    receiver_graph: str,
) -> None:
    sender_members = _logical_graph_members(service, sender_home, sender_graph)
    receiver_members = _logical_graph_members(service, receiver_home, receiver_graph)
    if sender_members & receiver_members:
        return
    sender_aliases, receiver_aliases = await asyncio.gather(
        _logical_graph_aliases(service, sender_members),
        _logical_graph_aliases(service, receiver_members),
    )
    conflicts = sorted(set(sender_aliases) & set(receiver_aliases))
    if not conflicts:
        return
    alias = conflicts[0]
    raise GraphNameConflictError(
        graph_id=f"{sender_graph}+{receiver_graph}",
        name=alias,
        creature_ids=tuple(sorted(sender_aliases[alias] | receiver_aliases[alias])),
    )


async def cross_node_connect(
    service: "MultiNodeTerrariumService",
    sender_id: str,
    receiver_id: str,
    sender_home: str,
    receiver_home: str,
    channel: str | None,
) -> ConnectionResult:
    """Cross-node connect — both sides host part of the graph.

    Workflow:
      1. Resolve / create the channel name (auto if not given).
      2. Ensure both nodes have a channel object of that name in the
         receiver's graph.
      3. Wire sender's send-side on sender's node.
      4. Wire receiver's listen-side on receiver's node.
      5. Cross-subscribe via terrarium.broadcast so sender's local
         sends forward to receiver's node, where the inject path
         replays into the local channel registry and fires the
         receiver's listener.
    """
    info_send = await service.service_for(sender_home).get_creature_info(sender_id)
    info_recv = await service.service_for(receiver_home).get_creature_info(receiver_id)
    if info_send is None:
        raise KeyError(sender_id)
    if info_recv is None:
        raise KeyError(receiver_id)
    await _guard_cross_node_name_conflicts(
        service,
        sender_home,
        info_send.graph_id,
        receiver_home,
        info_recv.graph_id,
    )
    chan_name = channel or f"{info_send.name}_to_{info_recv.name}"
    # Both nodes need the channel object — add_channel is idempotent
    # at the engine layer when the channel already exists, but we
    # wrap each in try/except to tolerate that.
    for node_id, gid in (
        (sender_home, info_send.graph_id),
        (receiver_home, info_recv.graph_id),
    ):
        try:
            await service.service_for(node_id).add_channel(gid, chan_name)
        except Exception:
            logger.debug("add_channel on %s for %s already present", node_id, chan_name)
    # Per-side wiring.
    await service.wire_creature(
        info_send.graph_id, sender_id, chan_name, "send", enabled=True
    )
    await service.wire_creature(
        info_recv.graph_id, receiver_id, chan_name, "listen", enabled=True
    )
    # Cross-subscribe — ask receiver_home to subscribe ITSELF to
    # sender_home so sender's local sends fan out to the receiver.
    # The host can't issue ``subscribe`` directly because the
    # subscribe-side records the *calling* node, and that's the host,
    # not receiver_home.  ``proxy_subscribe`` round-trips so the
    # subscription topology matches the wire direction.
    bcast = await local_broadcast_adapter(service)
    if bcast is not None:
        try:
            await bcast.proxy_subscribe(
                proxy_node=receiver_home,
                peer_node=sender_home,
                graph_id=info_send.graph_id,
                channel=chan_name,
            )
            record_cross_sub(
                service, receiver_home, sender_home, info_send.graph_id, chan_name
            )
        except Exception:
            logger.exception(
                "cross-node broadcast subscribe failed",
            )
    # Cluster-graph linkage: connect() over a cross-site bridge also
    # makes sender's graph and receiver's graph one logical cluster.
    # Record it for ``runtime_graph_snapshot``.
    record_cluster_link(
        service,
        frozenset(
            {
                (sender_home, info_send.graph_id),
                (receiver_home, info_recv.graph_id),
            }
        ),
    )
    return ConnectionResult(
        channel=chan_name,
        graph_id=info_send.graph_id,
        delta_kind="cross_node",
    )


async def cross_node_disconnect(
    service: "MultiNodeTerrariumService",
    sender_id: str,
    receiver_id: str,
    sender_home: str,
    receiver_home: str,
    channel: str | None,
) -> DisconnectionResult:
    """Cross-node disconnect — undo wire on each side and unsubscribe
    the cross-forward."""
    info_send = await service.service_for(sender_home).get_creature_info(sender_id)
    info_recv = await service.service_for(receiver_home).get_creature_info(receiver_id)
    if info_send is None:
        raise KeyError(sender_id)
    if info_recv is None:
        raise KeyError(receiver_id)
    chan_name = channel or ""
    if not chan_name:
        # No channel name — best effort: cross-side disconnect of ALL
        # channels both creatures share.  For now this is a no-op
        # since the API caller is expected to pass the channel name.
        # Surface as an explicit error.
        raise ValueError("cross-node disconnect requires an explicit channel name")
    await service.wire_creature(
        info_send.graph_id, sender_id, chan_name, "send", enabled=False
    )
    await service.wire_creature(
        info_recv.graph_id, receiver_id, chan_name, "listen", enabled=False
    )
    bcast = await local_broadcast_adapter(service)
    if bcast is not None:
        try:
            await bcast.proxy_unsubscribe(
                proxy_node=receiver_home,
                peer_node=sender_home,
                graph_id=info_send.graph_id,
                channel=chan_name,
            )
            drop_cross_sub(
                service, receiver_home, sender_home, info_send.graph_id, chan_name
            )
        except Exception:
            logger.debug("cross-node broadcast unsubscribe failed")
    # Cluster-graph linkage cleanup: the matching frozenset recorded
    # in ``connect`` (or by ``ensure_channel_replicated``) must be
    # removed so ``runtime_graph_snapshot`` stops folding the two
    # engine graphs into one cluster after the bridge is gone.
    link_key = frozenset(
        {
            (sender_home, info_send.graph_id),
            (receiver_home, info_recv.graph_id),
        }
    )
    drop_cluster_link(service, link_key)
    return DisconnectionResult(
        channels=[chan_name],
        delta_kind="cross_node",
    )


async def ensure_channel_replicated(
    service: "MultiNodeTerrariumService",
    target_node: str,
    target_graph: str,
    channel: str,
    *,
    direction: str | None = None,
) -> None:
    """Replicate ``channel`` onto ``target_graph`` if it lives elsewhere.

    If the named channel is already present on the target node's
    graph, this is a no-op.  Otherwise, fan out across other nodes to
    find a graph that hosts a channel with the same name; if found,
    declare the channel on the target graph (idempotent) and install
    a broadcast cross-subscription so messages flow.

    The ``direction`` argument controls the cross-subscription
    topology:

    - ``"listen"`` (default) — the target listens to the remote
      source's sends: target_node subscribes to source_node's
      ``(source_graph, channel)`` sends.
    - ``"send"`` — the target sends; the remote side should receive:
      source_node subscribes to target_node's
      ``(target_graph, channel)`` sends.
    - ``None`` — install both subscriptions (most permissive; used
      for the privileged-``root`` wire path which doesn't tell us the
      direction).
    """
    target_svc = service.service_for(target_node)
    try:
        existing = await target_svc.list_channels(target_graph)
    except KeyError:
        # graph_id unknown to this node — let the downstream wire
        # call raise the canonical error.
        return
    if any(getattr(c, "name", None) == channel for c in existing):
        return
    source = await find_channel_elsewhere(
        service,
        channel,
        exclude=target_node,
        graph_id=target_graph,
    )
    if source is None:
        # Channel doesn't exist anywhere — let the wire call below
        # raise the canonical "channel not found" error so the user
        # sees an honest message instead of a silent no-op.
        return
    source_node, source_graph = source
    try:
        await target_svc.add_channel(target_graph, channel)
    except Exception:
        logger.debug(
            "replicate channel %s on %s: already present (race-OK)",
            channel,
            target_node,
        )
    # Record the (node, graph) pairing so ``runtime_graph_snapshot``
    # folds these two engine graphs into one cluster graph.  The UI
    # then renders ONE graph spanning workers — the "single graph
    # for cross-node connection" UX invariant.  Stored as a frozenset
    # so direction doesn't matter and the same pair doesn't appear
    # twice.
    record_cluster_link(
        service,
        frozenset({(target_node, target_graph), (source_node, source_graph)}),
    )
    bcast = await local_broadcast_adapter(service)
    if bcast is None:
        return
    if direction in (None, "listen"):
        try:
            await bcast.proxy_subscribe(
                proxy_node=target_node,
                peer_node=source_node,
                graph_id=source_graph,
                channel=channel,
            )
            record_cross_sub(service, target_node, source_node, source_graph, channel)
        except Exception:
            logger.exception(
                "wire_creature cross-sub (listen) failed",
            )
    if direction in (None, "send"):
        try:
            await bcast.proxy_subscribe(
                proxy_node=source_node,
                peer_node=target_node,
                graph_id=target_graph,
                channel=channel,
            )
            record_cross_sub(service, source_node, target_node, target_graph, channel)
        except Exception:
            logger.exception(
                "wire_creature cross-sub (send) failed",
            )


async def find_channel_elsewhere(
    service: "MultiNodeTerrariumService",
    channel: str,
    *,
    exclude: str,
    graph_id: str,
) -> tuple[str, str] | None:
    """Locate one same-component graph hosting ``channel``."""
    members = {
        member_graph_id
        for _node_id, member_graph_id in cluster_members_for(service, graph_id)
    }
    candidates: list[tuple[str, str]] = []
    for node_id, svc in list(service._remotes.items()):
        if node_id == exclude:
            continue
        try:
            graphs = await svc.list_graphs()
        except Exception:
            logger.debug("list_graphs failed on %s during channel lookup", node_id)
            continue
        for graph in graphs:
            candidate_graph_id = getattr(graph, "graph_id", None) or ""
            if candidate_graph_id not in members:
                continue
            try:
                channels = await svc.list_channels(candidate_graph_id)
            except Exception:
                continue
            if any(getattr(item, "name", None) == channel for item in channels):
                candidates.append((node_id, candidate_graph_id))
    return candidates[0] if len(candidates) == 1 else None


__all__ = [
    "cross_node_connect",
    "cross_node_disconnect",
    "drop_cluster_link",
    "drop_cross_sub",
    "ensure_channel_replicated",
    "find_channel_elsewhere",
    "local_broadcast_adapter",
    "record_cluster_link",
    "record_cross_sub",
]
