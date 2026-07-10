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
snapshot that isn't already present. This is an ADDITIONS-ONLY
delta over the recipe base: a runtime removal of a snapshot-only
item stays removed (it isn't in the snapshot), but a runtime
removal of a RECIPE-defined channel/wire is recreated by the recipe
rebuild and NOT re-removed — surviving recipe-item removals needs
explicit tombstones. Edges are keyed by ``creature_id``; replay
silently skips ids the rebuilt graph doesn't carry, so id re-rolls
across resume can drop edges (stable-identity mapping is the
follow-up).
"""

from typing import TYPE_CHECKING, Any

import kohakuterrarium.terrarium.channels as _channels
import kohakuterrarium.terrarium.topology as _topo
from kohakuterrarium.terrarium.topology_leftovers import (
    distribute_leftovers,
    merge_into,
    transfer_leftovers,
)
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
    if graph_id in getattr(engine, "_restoring_topology_graphs", ()):
        # Mid-replay mutations must not persist a half-built graph
        # (channels present, edges still empty) — replay writes ONE
        # authoritative snapshot when the full topology is live.
        # Per-graph: a replay on graph A must not suppress snapshots
        # for concurrent mutations on graph B.
        return
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
    # Entries an incomplete replay could not re-attach (creature on
    # another graph after a split, transient channel failure) are NOT
    # in the live graph — without this union the next ordinary
    # mutation-snapshot would erase them permanently.
    leftovers = getattr(engine, "_topology_replay_leftovers", {}).get(graph_id)
    if leftovers:
        merge_into(payload, leftovers)
    try:
        store.meta[META_KEY] = payload
    except Exception:  # pragma: no cover - defensive
        logger.warning("runtime-topology snapshot write failed", exc_info=True)


def snapshot_all(engine: "Terrarium") -> None:
    """Snapshot every graph that currently has a session store.

    Used by topology ops whose result type doesn't carry the affected
    graph ids (e.g. ``DisconnectionResult``); cheaper than refactoring
    every result dataclass. Most engines have 1-2 graphs.
    """
    for gid in list(getattr(engine, "_session_stores", {}).keys()):
        snapshot(engine, gid)  # per-graph suppression applies inside


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
    restoring = getattr(engine, "_restoring_topology_graphs", None)
    if restoring is None:
        restoring = engine._restoring_topology_graphs = set()
    leftover_map = getattr(engine, "_topology_replay_leftovers", None)
    if leftover_map is None:
        leftover_map = engine._topology_replay_leftovers = {}
    restoring.add(graph_id)
    try:
        leftovers = await _replay_into(engine, graph_id, g, snap)
    finally:
        restoring.discard(graph_id)
    if leftovers is None:
        leftover_map.pop(graph_id, None)
        # One authoritative snapshot AFTER the complete topology is live.
        snapshot(engine, graph_id)
    else:
        # Keep the saved snapshot AND remember the unresolved remnant —
        # every later mutation-snapshot unions it back in, so a
        # transient failure or post-split edge is never erased.
        leftover_map[graph_id] = leftovers
        logger.warning(
            "runtime-topology replay incomplete — keeping saved snapshot",
            graph_id=graph_id,
        )


async def _replay_into(
    engine: "Terrarium", graph_id: str, g: Any, snap: dict[str, Any]
) -> dict[str, Any] | None:
    """Apply the snapshot. Returns ``None`` when EVERY saved addition
    was applied or already present; otherwise the unresolved remnant
    (same shape as the snapshot payload)."""
    leftovers: dict[str, Any] = {
        "channels": [],
        "listen_edges": {},
        "send_edges": {},
    }
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
            leftovers["channels"].append(dict(ch))
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
            # Sanitize on ENTRY to the remnant — malformed persisted
            # values (non-string channel names) must be ignored like
            # the ordinary replay path does, not crash the closure /
            # union passes downstream.
            valid_chans = [c for c in chans if isinstance(c, str) and c]
            if not g.has_creature(creature_id):
                if valid_chans:
                    leftovers[edge_name][creature_id] = valid_chans
                continue
            try:
                creature = engine.get_creature(creature_id)
            except KeyError:
                if valid_chans:
                    leftovers[edge_name][creature_id] = valid_chans
                continue
            for chan in chans:
                if not isinstance(chan, str) or not chan:
                    continue
                if chan not in g.channels:
                    leftovers[edge_name].setdefault(creature_id, []).append(chan)
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
    if leftovers["listen_edges"] or leftovers["send_edges"]:
        # Dependency closure: an unresolved edge must carry the
        # declaration of the channel it references. The channel itself
        # may have restored fine, but graph normalization (e.g. a later
        # split) prunes channels no LIVE edge uses — a remnant holding
        # only the edge would then persist as a dangling record that
        # cold replay can never resolve.
        declared = {c.get("name") for c in leftovers["channels"]}
        saved_by_name = {
            c.get("name"): c
            for c in (snap.get("channels") or [])
            if isinstance(c, dict) and isinstance(c.get("name"), str)
        }
        for edges in (leftovers["listen_edges"], leftovers["send_edges"]):
            for chans in edges.values():
                for chan in chans:
                    if chan in declared:
                        continue
                    declared.add(chan)
                    leftovers["channels"].append(
                        dict(
                            saved_by_name.get(chan) or {"name": chan, "description": ""}
                        )
                    )
    if leftovers["channels"] or leftovers["listen_edges"] or leftovers["send_edges"]:
        return leftovers
    return None


__all__ = [
    "META_KEY",
    "distribute_leftovers",
    "replay",
    "snapshot",
    "transfer_leftovers",
]
