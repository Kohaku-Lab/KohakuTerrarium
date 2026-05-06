"""Pure-data topology model for the Terrarium runtime engine.

Holds the structural state of all running creatures: which graph each
creature belongs to, which channels exist, who listens to / sends on
which channel. No live ``Agent`` references live in this layer — the
runtime engine layers those on top.

The model is testable without asyncio, without an LLM, without a
session store. ``tests/unit/terrarium/test_topology.py`` exercises every
function here with string IDs only.

## Concepts

- A **graph** is a connected component of creatures + channels.
- Two creatures are in the same graph iff there is a path between them
  through channels they share.
- ``connect(a, b, channel=...)`` may merge two graphs.
- ``disconnect(a, b, channel=...)`` may split one graph.
- All other topology changes (rewire within a graph) preserve graph
  membership.

These rules drive the session merge/split policy in
``terrarium.session_coord``.
"""

from collections import deque
from dataclasses import dataclass, field
from uuid import uuid4


@dataclass(frozen=True)
class ChannelInfo:
    """Static metadata about a channel.

    Graph topology channels are always broadcast — every listener
    receives every send. Channel-kind variants (queue) live in
    :mod:`core.channel` for sub-agent private comms only.
    """

    name: str
    description: str = ""


@dataclass
class GraphTopology:
    """One connected component of the engine.

    Pure data — no live agent references. ``creature_ids`` are opaque
    strings the engine assigns; they are unique across the whole engine,
    not just this graph.
    """

    graph_id: str
    creature_ids: set[str] = field(default_factory=set)
    channels: dict[str, ChannelInfo] = field(default_factory=dict)
    # creature_id -> set of channel names they listen to
    listen_edges: dict[str, set[str]] = field(default_factory=dict)
    # creature_id -> set of channel names they send to
    send_edges: dict[str, set[str]] = field(default_factory=dict)

    def has_creature(self, creature_id: str) -> bool:
        return creature_id in self.creature_ids

    def has_channel(self, name: str) -> bool:
        return name in self.channels


@dataclass
class TopologyState:
    """Engine-wide topology — collection of graphs + reverse index.

    Mutators are pure functions in this module so the model can be
    snapshot/copied for testing without aliasing issues.
    """

    graphs: dict[str, GraphTopology] = field(default_factory=dict)
    # creature_id -> graph_id
    creature_to_graph: dict[str, str] = field(default_factory=dict)

    def graph_of(self, creature_id: str) -> GraphTopology:
        gid = self.creature_to_graph.get(creature_id)
        if gid is None:
            raise KeyError(f"creature {creature_id!r} not in any graph")
        return self.graphs[gid]

    def creature_count(self) -> int:
        return len(self.creature_to_graph)

    def graph_count(self) -> int:
        return len(self.graphs)


# ---------------------------------------------------------------------------
# delta types — describe the membership change a topology op caused
# ---------------------------------------------------------------------------


@dataclass
class TopologyDelta:
    """Result of a topology mutation describing the membership change.

    ``kind == "nothing"`` means no graph membership changed (e.g. a
    rewire inside one graph).  ``kind == "merge"`` means two or more
    graphs combined into one — ``old_graph_ids`` lists the original
    graphs and ``new_graph_ids`` has exactly one entry.  ``kind ==
    "split"`` means one graph fragmented into two or more —
    ``old_graph_ids`` has exactly one entry and ``new_graph_ids`` has
    the post-split graphs.

    Engine consumers (notably ``session_coord``) inspect ``kind`` to
    decide whether to copy session stores.
    """

    kind: str  # "nothing" | "merge" | "split"
    old_graph_ids: list[str] = field(default_factory=list)
    new_graph_ids: list[str] = field(default_factory=list)
    affected_creatures: set[str] = field(default_factory=set)


# ---------------------------------------------------------------------------
# pure operations on TopologyState
# ---------------------------------------------------------------------------


def new_graph_id() -> str:
    """Mint a fresh graph id."""
    return f"graph_{uuid4().hex[:12]}"


def add_creature(
    state: TopologyState,
    creature_id: str,
    *,
    graph_id: str | None = None,
) -> str:
    """Add a creature.  Returns the graph_id it ended up in.

    ``graph_id=None`` creates a new singleton graph.  Otherwise the
    creature joins the named existing graph.
    """
    if creature_id in state.creature_to_graph:
        raise ValueError(f"creature {creature_id!r} already exists")

    if graph_id is None:
        graph_id = new_graph_id()
        state.graphs[graph_id] = GraphTopology(graph_id=graph_id)
    elif graph_id not in state.graphs:
        raise KeyError(f"graph {graph_id!r} does not exist")

    g = state.graphs[graph_id]
    g.creature_ids.add(creature_id)
    g.listen_edges.setdefault(creature_id, set())
    g.send_edges.setdefault(creature_id, set())
    state.creature_to_graph[creature_id] = graph_id
    return graph_id


def remove_creature(state: TopologyState, creature_id: str) -> TopologyDelta:
    """Remove a creature from its graph.  Returns the resulting delta.

    May split the graph if removing the creature breaks connectivity.
    May leave the graph empty (the empty graph is dropped).
    """
    g = state.graph_of(creature_id)
    g.creature_ids.discard(creature_id)
    g.listen_edges.pop(creature_id, None)
    g.send_edges.pop(creature_id, None)
    state.creature_to_graph.pop(creature_id, None)

    if not g.creature_ids:
        del state.graphs[g.graph_id]
        return TopologyDelta(
            kind="nothing",
            old_graph_ids=[g.graph_id],
            affected_creatures={creature_id},
        )

    return _normalize_components(state, g, affected={creature_id})


def add_channel(
    state: TopologyState,
    graph_id: str,
    name: str,
    *,
    description: str = "",
) -> ChannelInfo:
    """Declare a channel inside a graph.  Channel names are
    graph-unique; declaring a channel that already exists is an error.
    """
    g = state.graphs.get(graph_id)
    if g is None:
        raise KeyError(f"graph {graph_id!r} does not exist")
    if name in g.channels:
        raise ValueError(f"channel {name!r} already declared in graph {graph_id!r}")
    info = ChannelInfo(name=name, description=description)
    g.channels[name] = info
    return info


def remove_channel(state: TopologyState, graph_id: str, name: str) -> TopologyDelta:
    """Remove a channel from a graph.

    Drops all listen/send edges that referenced the channel. May split
    the graph if removing it breaks connectivity.
    """
    g = state.graphs.get(graph_id)
    if g is None:
        raise KeyError(f"graph {graph_id!r} does not exist")
    if name not in g.channels:
        raise KeyError(f"channel {name!r} not in graph {graph_id!r}")
    affected: set[str] = set()
    for cid, listens in g.listen_edges.items():
        if name in listens:
            listens.discard(name)
            affected.add(cid)
    for cid, sends in g.send_edges.items():
        if name in sends:
            sends.discard(name)
            affected.add(cid)
    g.channels.pop(name)
    return _normalize_components(state, g, affected=affected)


def set_listen(
    state: TopologyState,
    creature_id: str,
    channel: str,
    *,
    listening: bool,
) -> None:
    """Toggle whether a creature listens to a channel in its graph.

    The channel must already exist in the creature's graph.
    """
    g = state.graph_of(creature_id)
    if channel not in g.channels:
        raise KeyError(f"channel {channel!r} not in graph {g.graph_id!r}")
    edges = g.listen_edges.setdefault(creature_id, set())
    if listening:
        edges.add(channel)
    else:
        edges.discard(channel)


def set_send(
    state: TopologyState,
    creature_id: str,
    channel: str,
    *,
    sending: bool,
) -> None:
    """Toggle whether a creature sends on a channel in its graph."""
    g = state.graph_of(creature_id)
    if channel not in g.channels:
        raise KeyError(f"channel {channel!r} not in graph {g.graph_id!r}")
    edges = g.send_edges.setdefault(creature_id, set())
    if sending:
        edges.add(channel)
    else:
        edges.discard(channel)


def connect(
    state: TopologyState,
    sender_id: str,
    receiver_id: str,
    *,
    channel: str | None = None,
) -> tuple[str, TopologyDelta]:
    """Connect two creatures via a channel.

    If they live in different graphs the graphs are merged first; the
    channel is then declared inside the merged graph and the
    sender/receiver edges are wired.  When ``channel`` is None an
    auto-named channel is created.

    Returns ``(channel_name, delta)``.
    """
    if sender_id not in state.creature_to_graph:
        raise KeyError(f"creature {sender_id!r} not in any graph")
    if receiver_id not in state.creature_to_graph:
        raise KeyError(f"creature {receiver_id!r} not in any graph")

    delta = _ensure_same_graph(state, sender_id, receiver_id)
    g = state.graph_of(sender_id)
    name = channel or f"{sender_id}__{receiver_id}__{uuid4().hex[:8]}"
    if name not in g.channels:
        add_channel(state, g.graph_id, name)
    g.send_edges.setdefault(sender_id, set()).add(name)
    g.listen_edges.setdefault(receiver_id, set()).add(name)
    if not delta.affected_creatures:
        delta.affected_creatures = {sender_id, receiver_id}
    return name, delta


def disconnect(
    state: TopologyState,
    sender_id: str,
    receiver_id: str,
    *,
    channel: str | None = None,
) -> TopologyDelta:
    """Drop the channel-mediated link between two creatures.

    If ``channel`` is given, only that channel's send/listen pair is
    removed.  If ``None``, every channel where ``sender_id`` sends and
    ``receiver_id`` listens is unwired.  May split the graph.
    """
    g = state.graph_of(sender_id)
    if state.creature_to_graph.get(receiver_id) != g.graph_id:
        # already disconnected (different graphs)
        return TopologyDelta(kind="nothing")

    affected: set[str] = {sender_id, receiver_id}
    targets = (
        [channel]
        if channel is not None
        else sorted(
            g.send_edges.get(sender_id, set()) & g.listen_edges.get(receiver_id, set())
        )
    )
    for ch in targets:
        if ch in g.channels:
            g.send_edges.get(sender_id, set()).discard(ch)
            g.listen_edges.get(receiver_id, set()).discard(ch)

    return _normalize_components(state, g, affected=affected)


# ---------------------------------------------------------------------------
# component computation
# ---------------------------------------------------------------------------


def find_components(g: GraphTopology) -> list[set[str]]:
    """Return the connected components of a graph as creature-id sets.

    Two creatures are connected when they share at least one channel
    (either side listening or sending).  Uses BFS over a bipartite
    adjacency.
    """
    if not g.creature_ids:
        return []

    # build creature-to-channels and channel-to-creatures maps
    c2ch: dict[str, set[str]] = {cid: set() for cid in g.creature_ids}
    ch2c: dict[str, set[str]] = {name: set() for name in g.channels}
    for cid, listens in g.listen_edges.items():
        for ch in listens:
            if ch in ch2c:
                c2ch[cid].add(ch)
                ch2c[ch].add(cid)
    for cid, sends in g.send_edges.items():
        for ch in sends:
            if ch in ch2c:
                c2ch[cid].add(ch)
                ch2c[ch].add(cid)

    visited: set[str] = set()
    components: list[set[str]] = []
    for start in g.creature_ids:
        if start in visited:
            continue
        comp: set[str] = set()
        q: deque[str] = deque([start])
        while q:
            cur = q.popleft()
            if cur in visited:
                continue
            visited.add(cur)
            comp.add(cur)
            for ch in c2ch.get(cur, ()):
                for nxt in ch2c.get(ch, ()):
                    if nxt not in visited:
                        q.append(nxt)
        components.append(comp)
    return components


# ---------------------------------------------------------------------------
# internal helpers
# ---------------------------------------------------------------------------


def _ensure_same_graph(state: TopologyState, a: str, b: str) -> TopologyDelta:
    """Merge a's and b's graphs if they differ.  No-op if already same."""
    ga = state.creature_to_graph[a]
    gb = state.creature_to_graph[b]
    if ga == gb:
        return TopologyDelta(kind="nothing")
    return _merge_graphs(state, ga, gb)


def _merge_graphs(state: TopologyState, a: str, b: str) -> TopologyDelta:
    """Merge graph ``b`` into graph ``a``.  Channel names must not
    collide between the two graphs (an engine-level invariant —
    callers prefix channel names with creature ids when in doubt).
    """
    ga = state.graphs[a]
    gb = state.graphs[b]
    overlap = set(ga.channels) & set(gb.channels)
    if overlap:
        raise ValueError(
            f"cannot merge graphs {a!r} and {b!r}: channel name(s) "
            f"collide: {sorted(overlap)}"
        )
    # All creatures in both graphs end up sharing a new session store
    # after a merge, so they all count as "affected" for session_coord.
    affected: set[str] = set(ga.creature_ids) | set(gb.creature_ids)
    ga.creature_ids.update(gb.creature_ids)
    ga.channels.update(gb.channels)
    for cid, listens in gb.listen_edges.items():
        ga.listen_edges.setdefault(cid, set()).update(listens)
    for cid, sends in gb.send_edges.items():
        ga.send_edges.setdefault(cid, set()).update(sends)
    for cid in gb.creature_ids:
        state.creature_to_graph[cid] = a
    del state.graphs[b]
    return TopologyDelta(
        kind="merge",
        old_graph_ids=[a, b],
        new_graph_ids=[a],
        affected_creatures=affected,
    )


def _normalize_components(
    state: TopologyState, g: GraphTopology, *, affected: set[str]
) -> TopologyDelta:
    """Re-check ``g``'s connectivity; if it has fragmented, split it
    into separate graphs and return a split delta.  Otherwise no-op.
    """
    components = find_components(g)
    if len(components) <= 1:
        return TopologyDelta(
            kind="nothing",
            old_graph_ids=[g.graph_id],
            new_graph_ids=[g.graph_id],
            affected_creatures=affected,
        )

    # snapshot everything we need BEFORE we mutate g
    old_channels = dict(g.channels)
    old_listen = {cid: set(edges) for cid, edges in g.listen_edges.items()}
    old_send = {cid: set(edges) for cid, edges in g.send_edges.items()}
    original_id = g.graph_id

    # biggest component keeps the original graph_id
    components.sort(key=len, reverse=True)
    keep = components[0]
    new_ids: list[str] = [original_id]

    # rewrite original graph to keep only the largest component
    g.creature_ids = set(keep)
    g.listen_edges = {cid: set(old_listen.get(cid, ())) for cid in keep}
    g.send_edges = {cid: set(old_send.get(cid, ())) for cid in keep}
    used = _channels_used_by(keep, old_listen, old_send)
    g.channels = {n: info for n, info in old_channels.items() if n in used}

    # spawn new graphs for the other components
    for comp in components[1:]:
        new_id = new_graph_id()
        comp_listen = {cid: set(old_listen.get(cid, ())) for cid in comp}
        comp_send = {cid: set(old_send.get(cid, ())) for cid in comp}
        comp_used = _channels_used_by(comp, comp_listen, comp_send)
        comp_channels = {n: info for n, info in old_channels.items() if n in comp_used}
        new_g = GraphTopology(
            graph_id=new_id,
            creature_ids=set(comp),
            channels=comp_channels,
            listen_edges=comp_listen,
            send_edges=comp_send,
        )
        state.graphs[new_id] = new_g
        for cid in comp:
            state.creature_to_graph[cid] = new_id
        new_ids.append(new_id)

    # On a split, every creature from the original graph ends up in a
    # new (or shrunk) graph with a freshly-copied session store, so
    # they're all "affected" for session_coord purposes.
    full_affected = set(affected) | set(old_listen.keys()) | set(old_send.keys())
    return TopologyDelta(
        kind="split",
        old_graph_ids=[original_id],
        new_graph_ids=new_ids,
        affected_creatures=full_affected,
    )


def _channels_used_by(
    creatures: set[str],
    listen: dict[str, set[str]],
    send: dict[str, set[str]],
) -> set[str]:
    used: set[str] = set()
    for cid in creatures:
        used.update(listen.get(cid, ()))
        used.update(send.get(cid, ()))
    return used
