"""Cluster-graph folding for :mod:`multi_node_service`.

``MultiNodeTerrariumService.runtime_graph_snapshot`` collects one engine
graph per worker, then folds engine graphs that have been linked by a
cross-node channel wire into a single *cluster graph* so the UI sees
ONE graph spanning workers.  This module owns the pure fold algorithm
(union-find over the cluster-link set) so the service file stays under
the 1000-line hard cap.

The function is intentionally side-effect-free and takes its inputs as
arguments — it is purely a transformation from
``(engine_graphs, cluster_links)`` to a list of cluster-graph dicts.
"""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from kohakuterrarium.terrarium import TerrariumService


def cluster_groups(service: "TerrariumService") -> dict[str, set[str]]:
    """Return cluster groups keyed by primary sid (lex-smallest graph_id).

    Reads ``service._cluster_links`` — a set of ``frozenset({(node,
    graph), (node, graph)})`` pairs recorded by ``cross_node_connect``
    / ``ensure_channel_replicated``. Builds connected components via
    union-find, then for each component returns the set of member
    graph_ids. Returns an empty dict when the service has no cluster-
    link surface (standalone mode) or no links recorded.

    Cluster IDs use the graph_id (sid) directly — the ``node`` part of
    each pair is dropped because the studio's ``_meta`` is keyed by sid
    and the multi-node journey's B1 invariant addresses the cluster as
    the lex-smallest sid (matching :func:`fold_clusters` above).

    Lives here in the terrarium tier (not studio) so that the multi-
    node channel routing code in ``terrarium.multi_node_channels`` can
    consume it without importing higher-tier studio code. Studio's
    ``sessions.cluster_fold`` module re-exports it for the session-
    lifecycle callers that have always read it from there.
    """
    links = getattr(service, "_cluster_links", None)
    if not links:
        return {}
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        while parent.get(x, x) != x:
            parent[x] = parent.get(parent[x], parent[x])
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra == rb:
            return
        root, other = sorted([ra, rb])
        parent[other] = root

    for pair in links:
        pair_list = list(pair)
        if len(pair_list) < 2:
            continue
        sids = [gid for (_node, gid) in pair_list]
        for sid in sids:
            parent.setdefault(sid, sid)
        # Pair is exactly two endpoints (self-loops fold to a no-op).
        union(sids[0], sids[1])

    groups: dict[str, set[str]] = {}
    for sid in parent:
        groups.setdefault(find(sid), set()).add(sid)
    return groups


def fold_clusters(
    engine_graphs: list[dict[str, Any]],
    cluster_links: set[frozenset[tuple[str, str]]],
) -> list[dict[str, Any]]:
    """Union-find over ``cluster_links``; produce one entry per cluster
    (or pass-through for un-linked engine graphs).

    Each engine-graph entry carries its ``node_id`` (set by the remote
    service) and ``graph_id``.  We build the connected component each
    ``(node, graph)`` belongs to, then for each component emit one dict
    that unions creature_ids + channels (channels dedup by name) across
    the member engine-graphs.
    """
    # Map (node_id, graph_id) → engine graph dict.
    index: dict[tuple[str, str], dict[str, Any]] = {}
    for g in engine_graphs:
        key = (g.get("node_id", "_host") or "_host", g.get("graph_id", ""))
        index[key] = g

    parent: dict[tuple[str, str], tuple[str, str]] = {k: k for k in index}

    def find(x: tuple[str, str]) -> tuple[str, str]:
        while parent.get(x, x) != x:
            parent[x] = parent.get(parent[x], parent[x])
            x = parent[x]
        return x

    def union(a: tuple[str, str], b: tuple[str, str]) -> None:
        ra, rb = find(a), find(b)
        if ra == rb:
            return
        # Lexicographically smaller wins as the cluster root, so the
        # cluster id is deterministic across snapshots.
        root, other = sorted([ra, rb])
        parent[other] = root

    # Initialize parent for any (node, graph) referenced by links but
    # not yet seen in the engine_graphs index (the remote may have
    # been silenced this snapshot — keep the link bookkeeping working
    # anyway).
    for pair in cluster_links:
        for endpoint in pair:
            parent.setdefault(endpoint, endpoint)
        a, b = tuple(pair)  # frozenset → arbitrary order, but union is symmetric
        union(a, b)

    # Group by root.
    groups: dict[tuple[str, str], list[tuple[str, str]]] = {}
    for key in parent:
        groups.setdefault(find(key), []).append(key)

    out: list[dict[str, Any]] = []
    for root, members in groups.items():
        present = [m for m in members if m in index]
        if not present:
            continue
        if len(present) == 1:
            # No cross-link — surface the engine graph as-is.
            out.append(index[present[0]])
            continue
        out.append(_build_cluster_entry(present, index))
    # Sort by graph_id for stable rendering order.
    out.sort(key=lambda g: (g.get("graph_id") or "", g.get("node_id") or ""))
    return out


def _build_cluster_entry(
    present: list[tuple[str, str]],
    index: dict[tuple[str, str], dict[str, Any]],
) -> dict[str, Any]:
    """Build the cluster graph entry from its member engine-graphs.

    ``graph_id`` is the lexicographically-smallest member's id so the
    same cluster keeps a stable identity across snapshots.
    """
    present.sort()
    primary_node, primary_gid = present[0]
    seen_creatures: set[str] = set()
    creature_ids: list[str] = []
    creature_dicts: list[dict[str, Any]] = []
    seen_channels: set[str] = set()
    channels: list[dict[str, Any]] = []
    seen_edges: set[Any] = set()
    output_edges: list[dict[str, Any]] = []
    members_payload: list[dict[str, str]] = []
    for node_id, gid in present:
        g = index[(node_id, gid)]
        members_payload.append({"node_id": node_id, "graph_id": gid})
        # Accept both shapes: the bare ``creature_ids`` (DTO form from
        # the wire) and the dict-form ``creatures`` (full status
        # snapshot the studio runtime-graph route builds).  Dedup by
        # id either way.
        for cid in g.get("creature_ids", []) or []:
            if cid not in seen_creatures:
                seen_creatures.add(cid)
                creature_ids.append(cid)
        for cdict in g.get("creatures", []) or []:
            if not isinstance(cdict, dict):
                continue
            cid = cdict.get("creature_id") or cdict.get("agent_id") or ""
            if cid and cid not in seen_creatures:
                seen_creatures.add(cid)
                creature_ids.append(cid)
                # Rewrite the per-creature ``graph_id`` to the
                # cluster's primary id (set below).  Frontend
                # ``runtimeGraphModel.js::addChannelEdges`` keys
                # channel nodes by the cluster ``graph.graph_id`` —
                # leaving the per-creature graph_id as the
                # worker-local engine graph breaks the
                # creature↔channel edge render (#150).
                cdict = dict(cdict)
                cdict["graph_id"] = primary_gid
                creature_dicts.append(cdict)
        for ch in g.get("channels", []) or []:
            name = ch.get("name") if isinstance(ch, dict) else str(ch)
            if name and name not in seen_channels:
                seen_channels.add(name)
                channels.append(ch if isinstance(ch, dict) else {"name": name})
        for ed in g.get("output_edges", []) or []:
            if not isinstance(ed, dict):
                continue
            edge_id = ed.get("edge_id") or ed.get("id")
            if edge_id:
                key: Any = ("id", edge_id)
            else:
                key = (
                    "tuple",
                    ed.get("from", ""),
                    ed.get("to_creature_id") or ed.get("to", ""),
                    ed.get("graph_id", ""),
                )
            if key in seen_edges:
                continue
            seen_edges.add(key)
            output_edges.append(ed)
    cluster_entry: dict[str, Any] = {
        "graph_id": primary_gid,
        "node_id": primary_node,
        "creature_ids": creature_ids,
        "channels": channels,
        "output_edges": output_edges,
        "is_cluster": True,
        "members": members_payload,
    }
    if creature_dicts:
        cluster_entry["creatures"] = creature_dicts
    # Carry forward any other meta keys present on the primary.
    primary = index[present[0]]
    for key, val in primary.items():
        if key not in cluster_entry:
            cluster_entry[key] = val
    return cluster_entry


__all__ = ["cluster_groups", "fold_clusters"]
