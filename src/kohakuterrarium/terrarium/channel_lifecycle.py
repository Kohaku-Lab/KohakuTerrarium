"""Engine-side topology lifecycle helpers — disconnect, split
bookkeeping, channel removal.

Split out of :mod:`terrarium.channels` to keep that file under the
600-line per-file budget. The functions here are tightly coupled:

- :func:`disconnect_creatures` is the body of
  :meth:`Terrarium.disconnect` — drops listen/send edges, removes
  triggers, and may propagate a split.
- :func:`apply_split_bookkeeping` runs after any topology mutation
  that may have produced a split (creature removal, channel removal,
  edge unwiring). It allocates envs for new components, registers
  topology channels into them, repoints affected creatures, and
  re-injects listen triggers so messages flow on the new env's
  channel objects.
- :func:`remove_channel_from_graph` is the body of
  :meth:`Terrarium.remove_channel` — tears down listen triggers,
  drops the channel from the live registry, removes it from topology
  (which may propagate a split), and runs split bookkeeping.

Lives next to :mod:`terrarium.channels` rather than at engine layer
because every function here reaches into the channel registry +
trigger machinery owned by that module. The dependency is one-way
(this module imports ``channels``; ``channels`` does not import
back), satisfying the dep-graph cycle check.
"""

from typing import TYPE_CHECKING

import kohakuterrarium.terrarium.session_coord as _session_coord
import kohakuterrarium.terrarium.topology as _topo
from kohakuterrarium.core.environment import Environment
from kohakuterrarium.terrarium.channels import (
    bind_creature_to_environment,
    inject_channel_trigger,
    register_channel_in_environment,
    register_engine_handle,
    remove_channel_trigger,
)
from kohakuterrarium.terrarium.events import (
    DisconnectionResult,
    EngineEvent,
    EventKind,
)

if TYPE_CHECKING:
    from kohakuterrarium.terrarium.engine import CreatureRef, Terrarium


async def disconnect_creatures(
    engine: "Terrarium",
    sender: "CreatureRef",
    receiver: "CreatureRef",
    *,
    channel: str | None = None,
) -> DisconnectionResult:
    """Body of :meth:`Terrarium.disconnect`.

    Drops the listen/send edge pair (sender→channel→receiver) for the
    named channel — or, when ``channel`` is None, every channel where
    sender currently sends to and receiver currently listens on. May
    split the graph; emits ``TOPOLOGY_CHANGED`` either way (split
    handler emits its own; intra-graph unwire emits a no-split event
    so per-creature prompts refresh).
    """
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
        apply_split_bookkeeping(engine, delta)
    elif targets:
        # Intra-graph unwire still mutated listen/send lists, so the
        # runtime-graph prompt block must refresh both peers.
        engine._emit(
            EngineEvent(
                kind=EventKind.TOPOLOGY_CHANGED,
                graph_id=gid,
                payload={
                    "kind": "nothing",
                    "old_graph_ids": [gid],
                    "new_graph_ids": [gid],
                    "affected": sorted({sid, rid}),
                },
            )
        )
    return DisconnectionResult(channels=list(targets), delta_kind=delta.kind)


def apply_split_bookkeeping(engine: "Terrarium", delta: _topo.TopologyDelta) -> None:
    """Run the engine-side bookkeeping for a topology split.

    For every new graph component:

    1. Allocate a fresh :class:`Environment` (and register the engine
       weakref on it) when one isn't already there.
    2. Register the component's topology channels in the new env's
       :class:`ChannelRegistry`. Without this, creatures repointed at
       the new env would have empty ``shared_channels`` and their
       sends would silently fall on the floor.
    3. Repoint every affected creature's ``graph_id`` + agent
       environment to its new graph.
    4. Re-inject every affected creature's listen-channel triggers so
       they fire on the *new* env's channel objects (the originals
       still live in the kept env). Triggers from before the split
       were bound to the original env's channel objects; left alone,
       they'd never see sends in the new component.

    Then runs session-store split coordination and emits the
    ``TOPOLOGY_CHANGED`` event. No-op when ``delta.kind != "split"``.

    Shared between :func:`terrarium.channels.disconnect_creatures`,
    :func:`remove_channel_from_graph`,
    :meth:`Terrarium.remove_creature`, and the
    ``group_channel(action='unwire')`` tool path so all four handle
    splits identically.
    """
    if delta.kind != "split":
        return

    # 1. Allocate envs.
    for new_gid in delta.new_graph_ids:
        if new_gid not in engine._environments:
            new_env = Environment(env_id=f"env_{new_gid}")
            engine._environments[new_gid] = new_env
            register_engine_handle(new_env, engine)

    # 2. Sync each new graph's live registry to its topology channels.
    # The surviving graph keeps its old Environment, so it can still
    # contain channel objects that were moved/dropped during the split;
    # remove those stale registry entries before registering current
    # topology channels.
    for new_gid in delta.new_graph_ids:
        graph = engine._topology.graphs.get(new_gid)
        env = engine._environments.get(new_gid)
        if graph is None or env is None:
            continue
        live_names = set(env.shared_channels.list_channels())
        topo_names = set(graph.channels)
        for stale in sorted(live_names - topo_names):
            env.shared_channels.remove(stale)
        for info in graph.channels.values():
            register_channel_in_environment(env.shared_channels, info)

    # 3. Repoint creatures + 4. reinject listen triggers on the new env.
    for cid in delta.affected_creatures:
        c = engine._creatures.get(cid)
        if c is None:
            continue
        c.graph_id = engine._topology.creature_to_graph.get(cid, c.graph_id)
        new_env = engine._environments.get(c.graph_id)
        if new_env is None:
            continue
        bind_creature_to_environment(c, new_env)
        graph = engine._topology.graphs.get(c.graph_id)
        if graph is None:
            continue
        listen_set = graph.listen_edges.get(cid, set())
        for ch_name in sorted(listen_set):
            # ``remove_channel_trigger`` is idempotent; skipping the
            # remove step would leak a stale trigger pointing at the
            # old env's channel object.
            remove_channel_trigger(c.agent, subscriber_id=c.name, channel_name=ch_name)
            inject_channel_trigger(
                c.agent,
                subscriber_id=c.name,
                channel_name=ch_name,
                registry=new_env.shared_channels,
                ignore_sender=c.name,
            )

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


async def remove_channel_from_graph(
    engine: "Terrarium", graph_id: str, name: str
) -> _topo.TopologyDelta:
    """Body of :meth:`Terrarium.remove_channel`.

    Tears down listen triggers for every listener, drops the channel
    from the live :class:`ChannelRegistry`, removes it from the topology
    (which propagates listen/send edge cleanup), and may split the graph
    if the channel was the only connectivity bridge between two
    components.
    """
    g = engine._topology.graphs.get(graph_id)
    if g is None:
        raise KeyError(f"graph {graph_id!r} does not exist")
    if name not in g.channels:
        raise KeyError(f"channel {name!r} not in graph {graph_id!r}")

    # 1. Tear down listen triggers on every listener.
    for cid, listens in list(g.listen_edges.items()):
        if name not in listens:
            continue
        creature = engine._creatures.get(cid)
        if creature is not None:
            remove_channel_trigger(
                creature.agent,
                subscriber_id=creature.name,
                channel_name=name,
            )
            if name in creature.listen_channels:
                creature.listen_channels.remove(name)

    # 2. Sync the per-creature send-channel list (topology mutation
    #    below clears the topo edges; we mirror that on the Creature).
    for cid, sends in list(g.send_edges.items()):
        if name in sends:
            creature = engine._creatures.get(cid)
            if creature is not None and name in creature.send_channels:
                creature.send_channels.remove(name)

    # 3. Drop from the live registry.
    env = engine._environments.get(graph_id)
    if env is not None:
        env.shared_channels.remove(name)

    # 4. Drop from topology (clears edges + propagates split if any).
    delta = _topo.remove_channel(engine._topology, graph_id, name)

    # 5. Split bookkeeping shared with disconnect_creatures + the
    # group_channel(action="unwire") tool path. If no split happened,
    # still emit so runtime-graph prompts refresh their listen/send
    # lists after the channel disappeared.
    if delta.kind == "split":
        apply_split_bookkeeping(engine, delta)
    else:
        engine._emit(
            EngineEvent(
                kind=EventKind.TOPOLOGY_CHANGED,
                graph_id=graph_id,
                payload={
                    "kind": "nothing",
                    "old_graph_ids": [graph_id],
                    "new_graph_ids": [graph_id],
                    "affected": sorted(delta.affected_creatures),
                },
            )
        )
    return delta
