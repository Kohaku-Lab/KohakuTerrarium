"""Runtime-topology snapshot + replay.

Recipes (``terrarium.yaml``) describe the topology a graph starts
with. Everything the user / a privileged tool adds AFTER the recipe
loads — extra channels via ``service.add_channel``, extra wires via
``service.connect``, ``unwire`` removals — only lives in the
in-memory :class:`GraphTopology`. Without persistence, a close +
resume cycle reverts to the original recipe.

This module captures the live topology as a primitive-dict snapshot
into the graph's :class:`SessionStore` ``meta`` after every mutation,
and replays the additions on top of the recipe-rebuilt topology at
resume time. The result: any channel / wire the user added survives
a close + reopen.

Snapshot shape (under ``meta["runtime_topology"]``):

::

    {
        "channels": [{"name": str, "description": str}, ...],
        "listen_edges": {creature_id: [channel_name, ...]},
        "send_edges":   {creature_id: [channel_name, ...]},
    }

Replay logic: the recipe rebuild stamps the recipe-described
channels + wires first; the replay then ADDS anything in the
snapshot that isn't already present. Removals are reflected because
the snapshot is a *full* snapshot of the live topology at write
time — anything the user removed simply isn't in the snapshot.
"""

from typing import TYPE_CHECKING, Any

import kohakuterrarium.terrarium.channels as _channels
import kohakuterrarium.terrarium.topology as _topo
from kohakuterrarium.utils.logging import get_logger

if TYPE_CHECKING:
    from kohakuterrarium.terrarium.engine import Terrarium

logger = get_logger(__name__)

META_KEY = "runtime_topology"


def snapshot(engine: "Terrarium", graph_id: str) -> None:
    """Write the graph's current topology snapshot into its store meta.

    No-op when no session store is attached to ``graph_id``. Best-
    effort: a write failure logs and continues — losing one snapshot
    just means resume reverts to the most recent successfully-written
    snapshot, not data corruption.
    """
    store = engine._session_stores.get(graph_id)
    if store is None:
        return
    g = engine._topology.graphs.get(graph_id)
    if g is None:
        return
    payload: dict[str, Any] = {
        "channels": [
            {"name": ch.name, "description": ch.description}
            for ch in g.channels.values()
        ],
        "listen_edges": {
            cid: sorted(chs) for cid, chs in g.listen_edges.items() if chs
        },
        "send_edges": {cid: sorted(chs) for cid, chs in g.send_edges.items() if chs},
    }
    try:
        store.meta[META_KEY] = payload
    except Exception:  # pragma: no cover - defensive
        logger.debug("runtime-topology snapshot write failed", exc_info=True)


def snapshot_all(engine: "Terrarium") -> None:
    """Snapshot every graph that currently has a session store.

    Used by topology ops whose result type doesn't carry the affected
    graph ids (e.g. ``DisconnectionResult``); cheaper than refactoring
    every result dataclass. Most engines have 1-2 graphs.
    """
    for gid in list(getattr(engine, "_session_stores", {}).keys()):
        snapshot(engine, gid)


async def replay(engine: "Terrarium", graph_id: str) -> None:
    """Replay the saved runtime additions on top of the loaded recipe.

    Called from ``_resume_terrarium_into_engine`` AFTER the recipe
    rebuild has produced the base topology. Reads
    ``meta[META_KEY]`` from the graph's session store and:

    - Declares any channels that aren't already in the graph
      (`add_channel`).
    - Wires any listen / send edges that aren't already there
      (`wire_creature_on_engine`).

    Edges referenced in the snapshot whose creature is no longer in
    the graph are silently skipped — they were either removed by a
    later mutation or live on a different graph after a split.
    """
    store = engine._session_stores.get(graph_id)
    if store is None:
        return
    try:
        meta = store.load_meta()
    except Exception:  # pragma: no cover - defensive
        return
    snap = meta.get(META_KEY)
    if not isinstance(snap, dict):
        return
    g = engine._topology.graphs.get(graph_id)
    if g is None:
        return
    # 1. Channels.
    saved_channels = snap.get("channels") or []
    for ch in saved_channels:
        if not isinstance(ch, dict):
            continue
        name = ch.get("name")
        if not isinstance(name, str) or not name:
            continue
        if name in g.channels:
            continue
        try:
            await engine.add_channel(
                graph_id, name, description=str(ch.get("description") or "")
            )
        except Exception:  # pragma: no cover - defensive
            logger.warning(
                "runtime-topology replay: add_channel %r failed", name, exc_info=True
            )

    # 2. Listen + send wires. Inline the wire body (mirrors
    # ``creature_ops.wire_creature_on_engine`` but stays a leaf module
    # to keep the import graph acyclic — creature_ops imports this
    # module for the snapshot hook, so we can't import back.)
    env = engine._environments.get(graph_id)
    registry = getattr(env, "shared_channels", None) if env is not None else None
    for edge_name in ("listen_edges", "send_edges"):
        edges = snap.get(edge_name) or {}
        if not isinstance(edges, dict):
            continue
        for creature_id, chans in edges.items():
            if not isinstance(chans, list):
                continue
            if not g.has_creature(creature_id):
                continue
            try:
                creature = engine.get_creature(creature_id)
            except KeyError:
                continue
            for chan in chans:
                if not isinstance(chan, str) or not chan:
                    continue
                if chan not in g.channels:
                    continue
                if edge_name == "listen_edges":
                    if chan in g.listen_edges.get(creature_id, set()):
                        continue
                    _topo.set_listen(
                        engine._topology, creature_id, chan, listening=True
                    )
                    if registry is not None:
                        _channels.register_channel_in_environment(
                            registry,
                            g.channels[chan],
                            engine=engine,
                            graph_id=graph_id,
                        )
                        _channels.inject_channel_trigger(
                            creature.agent,
                            subscriber_id=creature.name,
                            channel_name=chan,
                            registry=registry,
                            ignore_sender=creature.name,
                            ignore_sender_id=creature.creature_id,
                        )
                    if chan not in creature.listen_channels:
                        creature.listen_channels.append(chan)
                else:  # send_edges
                    if chan in g.send_edges.get(creature_id, set()):
                        continue
                    _topo.set_send(engine._topology, creature_id, chan, sending=True)
                    if chan not in creature.send_channels:
                        creature.send_channels.append(chan)


__all__ = ["META_KEY", "replay", "snapshot"]
