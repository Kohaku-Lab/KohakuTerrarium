"""Channel layer for the Terrarium engine.

Bridges the pure-data topology (``terrarium.topology``) and the live
``Environment.shared_channels`` registry from ``core.channel``.  Owns
channel injection — when a creature joins a graph that has channels it
listens to, a :class:`ChannelTrigger` is added to its
``trigger_manager``.

Supports both static wiring (declared at recipe-load time) and live
hot-plug (creatures connecting after they're already running).

The ``connect_creatures`` / ``disconnect_creatures`` helpers below are
the bodies of ``Terrarium.connect`` / ``Terrarium.disconnect``;
they're kept here to keep ``engine.py`` under the 600-line cap and
because every line of logic in them is channel-related.
"""

from datetime import datetime
from typing import TYPE_CHECKING, Any

from kohakuterrarium.core.channel import ChannelRegistry
from kohakuterrarium.core.environment import Environment
from kohakuterrarium.modules.trigger.channel import ChannelTrigger
import kohakuterrarium.terrarium.session_coord as _session_coord
import kohakuterrarium.terrarium.topology as _topo
from kohakuterrarium.terrarium.events import (
    ConnectionResult,
    DisconnectionResult,
    EngineEvent,
    EventKind,
)
from kohakuterrarium.terrarium.topology import ChannelInfo, ChannelKind
from kohakuterrarium.utils.logging import get_logger

if TYPE_CHECKING:
    from kohakuterrarium.terrarium.engine import (
        CreatureRef,
        Terrarium,
    )

logger = get_logger(__name__)


# Map between the engine's ``ChannelKind`` enum and the string keys used
# by ``ChannelRegistry.get_or_create``.
_KIND_TO_REGISTRY: dict[ChannelKind, str] = {
    ChannelKind.BROADCAST: "broadcast",
    ChannelKind.QUEUE: "queue",
}


def register_channel_in_environment(
    registry: ChannelRegistry,
    info: ChannelInfo,
    *,
    maxsize: int = 0,
) -> Any:
    """Ensure the channel exists in the live ``ChannelRegistry``.

    Returns the channel object (queue- or broadcast-flavoured).
    """
    return registry.get_or_create(
        info.name,
        channel_type=_KIND_TO_REGISTRY.get(info.kind, "queue"),
        maxsize=maxsize,
        description=info.description,
    )


def inject_channel_trigger(
    agent: Any,
    *,
    subscriber_id: str,
    channel_name: str,
    registry: ChannelRegistry,
    prompt: str | None = None,
    ignore_sender: str | None = None,
) -> str:
    """Add a :class:`ChannelTrigger` to ``agent.trigger_manager``.

    Returns the trigger id.  Idempotent — re-injecting an existing
    trigger replaces it (so callers can re-run after a hot-plug change
    without leaking duplicates).
    """
    prompt = prompt or "[Channel '{channel}' from {sender}]: {content}"
    trigger = ChannelTrigger(
        channel_name=channel_name,
        subscriber_id=f"{subscriber_id}_{channel_name}",
        prompt=prompt,
        ignore_sender=ignore_sender or subscriber_id,
        registry=registry,
    )
    trigger_id = f"channel_{subscriber_id}_{channel_name}"
    agent.trigger_manager._triggers[trigger_id] = trigger
    agent.trigger_manager._created_at[trigger_id] = datetime.now()
    logger.debug(
        "Injected channel trigger",
        subscriber=subscriber_id,
        channel=channel_name,
        trigger_id=trigger_id,
    )
    return trigger_id


def remove_channel_trigger(
    agent: Any,
    *,
    subscriber_id: str,
    channel_name: str,
) -> bool:
    """Remove a previously-injected trigger.  Returns True if removed."""
    trigger_id = f"channel_{subscriber_id}_{channel_name}"
    removed = False
    if trigger_id in agent.trigger_manager._triggers:
        del agent.trigger_manager._triggers[trigger_id]
        removed = True
    agent.trigger_manager._created_at.pop(trigger_id, None)
    if removed:
        logger.debug(
            "Removed channel trigger",
            subscriber=subscriber_id,
            channel=channel_name,
            trigger_id=trigger_id,
        )
    return removed


# ---------------------------------------------------------------------------
# engine-level connect / disconnect — bodies of Terrarium.connect /
# Terrarium.disconnect.  Kept here to share the channel-injection
# bookkeeping with :func:`inject_channel_trigger` etc.
# ---------------------------------------------------------------------------


async def connect_creatures(
    engine: "Terrarium",
    sender: "CreatureRef",
    receiver: "CreatureRef",
    *,
    channel: str | None = None,
    kind: ChannelKind = ChannelKind.QUEUE,
) -> ConnectionResult:
    """Body of :meth:`Terrarium.connect` — see the engine docstring.

    Cross-graph connects merge the two graphs and union their
    environments so all creatures in the new combined graph see the
    same channel registry.
    """
    sid = engine._resolve_creature_id(sender)
    rid = engine._resolve_creature_id(receiver)
    sender_creature = engine.get_creature(sid)
    receiver_creature = engine.get_creature(rid)

    channel_name, delta = _topo.connect(
        engine._topology, sid, rid, channel=channel, kind=kind
    )
    if delta.kind == "merge":
        # The kept graph's id is delta.new_graph_ids[0].  Move every
        # channel + re-point every trigger from the dropped env into
        # the kept env, then drop the env entry for the orphan graph.
        keep_gid = delta.new_graph_ids[0]
        drop_gids = [g for g in delta.old_graph_ids if g != keep_gid]
        for drop_gid in drop_gids:
            _merge_environment_into(engine, keep_gid, drop_gid)
        # Update graph_id on every moved creature.
        for cid in delta.affected_creatures:
            c = engine._creatures.get(cid)
            if c is not None:
                c.graph_id = engine._topology.creature_to_graph.get(cid, c.graph_id)
        # Coordinate session-store side of the merge.
        _session_coord.apply_merge(engine, delta)

    gid = sender_creature.graph_id  # refreshed by the loop above
    env = engine._environments[gid]
    info = engine._topology.graphs[gid].channels[channel_name]
    register_channel_in_environment(env.shared_channels, info)
    trigger_id = inject_channel_trigger(
        receiver_creature.agent,
        subscriber_id=receiver_creature.name,
        channel_name=channel_name,
        registry=env.shared_channels,
        ignore_sender=receiver_creature.name,
    )
    if channel_name not in receiver_creature.listen_channels:
        receiver_creature.listen_channels.append(channel_name)
    if channel_name not in sender_creature.send_channels:
        sender_creature.send_channels.append(channel_name)

    if delta.kind != "nothing":
        engine._emit(
            EngineEvent(
                kind=EventKind.TOPOLOGY_CHANGED,
                graph_id=gid,
                payload={
                    "kind": delta.kind,
                    "old_graph_ids": list(delta.old_graph_ids),
                    "new_graph_ids": list(delta.new_graph_ids),
                    "affected": sorted(delta.affected_creatures),
                },
            )
        )
    return ConnectionResult(
        channel=channel_name,
        trigger_id=trigger_id,
        delta_kind=delta.kind,
    )


def _merge_environment_into(engine: "Terrarium", keep_gid: str, drop_gid: str) -> None:
    """Union the dropped graph's environment into the surviving one.

    For every channel registered in the dropped env, register a
    matching channel in the kept env.  For every creature that's now
    in the kept graph but used to live in the dropped graph, re-inject
    its channel triggers using the kept env's registry so it actually
    receives messages.
    """
    keep_env = engine._environments[keep_gid]
    drop_env = engine._environments.pop(drop_gid, None)
    if drop_env is None:
        return
    keep_g = engine._topology.graphs[keep_gid]

    # Copy channels.  Both registries store ``BaseChannel`` objects;
    # we re-create rather than alias so ownership is unambiguous.
    for ch_name, info in keep_g.channels.items():
        register_channel_in_environment(keep_env.shared_channels, info)

    # Re-inject triggers for creatures whose graph_id is now keep_gid
    # but whose existing triggers still point at drop_env.
    for cid in keep_g.creature_ids:
        creature = engine._creatures.get(cid)
        if creature is None:
            continue
        for ch_name in keep_g.listen_edges.get(cid, set()):
            # remove any stale trigger (pointing at drop_env) and
            # inject a fresh one pointing at keep_env.
            remove_channel_trigger(
                creature.agent,
                subscriber_id=creature.name,
                channel_name=ch_name,
            )
            inject_channel_trigger(
                creature.agent,
                subscriber_id=creature.name,
                channel_name=ch_name,
                registry=keep_env.shared_channels,
                ignore_sender=creature.name,
            )


async def disconnect_creatures(
    engine: "Terrarium",
    sender: "CreatureRef",
    receiver: "CreatureRef",
    *,
    channel: str | None = None,
) -> DisconnectionResult:
    """Body of :meth:`Terrarium.disconnect` — see engine docstring."""
    sid = engine._resolve_creature_id(sender)
    rid = engine._resolve_creature_id(receiver)
    sender_creature = engine.get_creature(sid)
    receiver_creature = engine.get_creature(rid)
    if sender_creature.graph_id != receiver_creature.graph_id:
        return DisconnectionResult(channels=[], delta_kind="nothing")

    gid = sender_creature.graph_id
    g = engine._topology.graphs[gid]
    targets = (
        [channel]
        if channel is not None
        else sorted(g.send_edges.get(sid, set()) & g.listen_edges.get(rid, set()))
    )
    delta = _topo.disconnect(engine._topology, sid, rid, channel=channel)
    for ch in targets:
        remove_channel_trigger(
            receiver_creature.agent,
            subscriber_id=receiver_creature.name,
            channel_name=ch,
        )
        if ch in receiver_creature.listen_channels:
            receiver_creature.listen_channels.remove(ch)
        if ch in sender_creature.send_channels:
            sender_creature.send_channels.remove(ch)

    if delta.kind == "split":
        for new_gid in delta.new_graph_ids:
            if new_gid not in engine._environments:
                engine._environments[new_gid] = Environment(env_id=f"env_{new_gid}")
        for cid in delta.affected_creatures:
            c = engine._creatures.get(cid)
            if c is not None:
                c.graph_id = engine._topology.creature_to_graph.get(cid, c.graph_id)
        # Coordinate session-store side of the split.
        _session_coord.apply_split(engine, delta)
        engine._emit(
            EngineEvent(
                kind=EventKind.TOPOLOGY_CHANGED,
                payload={
                    "kind": delta.kind,
                    "old_graph_ids": list(delta.old_graph_ids),
                    "new_graph_ids": list(delta.new_graph_ids),
                    "affected": sorted(delta.affected_creatures),
                },
            )
        )
    return DisconnectionResult(channels=list(targets), delta_kind=delta.kind)
