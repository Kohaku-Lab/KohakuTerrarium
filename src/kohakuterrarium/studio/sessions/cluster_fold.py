"""B1 — multi-node cluster fold helpers for the session lifecycle.

After a cross-node ``connect()`` records a ``_cluster_links`` entry on
the ``MultiNodeTerrariumService`` (see
:mod:`kohakuterrarium.terrarium.multi_node_replication`), the studio
sessions layer MUST collapse the two per-spawn ``_meta`` listings into
ONE cluster listing addressed by the lex-smallest sid. The same fold
must apply to ``get_session`` so a single Session handle exposes every
creature across the cluster — that's how standalone mode looks after a
``session_coord.apply_merge``, and the multi-node code path must
preserve that "one graph = one session" UX invariant.

The helpers here are pure transformations: they take a
``TerrariumService`` (read its ``_cluster_links`` set) and the studio's
``_meta`` registry, and produce folded listings / creature lists. They
live in their own module so :mod:`lifecycle` stays under the 1000-line
hard cap mandated by ``tests/unit/test_file_sizes.py``.
"""

from pathlib import Path
from typing import TYPE_CHECKING, Any

from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.studio.sessions.handles import SessionListing
from kohakuterrarium.utils.logging import get_logger

if TYPE_CHECKING:
    from kohakuterrarium.terrarium import TerrariumService

logger = get_logger(__name__)


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
    the lex-smallest sid (matching ``multi_node_cluster.fold_clusters``).
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


def sid_to_primary(service: "TerrariumService") -> dict[str, str]:
    """Map every cluster member sid → its primary (lex-smallest) sid."""
    mapping: dict[str, str] = {}
    for primary, members in cluster_groups(service).items():
        for sid in members:
            mapping[sid] = primary
    return mapping


def fold_session_listings(
    meta_listings: list[SessionListing], service: "TerrariumService"
) -> list[SessionListing]:
    """Fold per-meta listings into one entry per cluster.

    For each cluster recorded in ``service._cluster_links``: keep the
    primary (lex-smallest sid) listing, drop the non-primary listings,
    and sum the per-member ``creatures`` counts onto the primary. The
    primary's ``node_id`` is preserved (home of the primary creature) —
    cluster members keep their distinct ``home_node`` per-creature in
    the folded :func:`fold_session_creatures` view, not here.

    Listings whose sid is not part of any cluster pass through
    unchanged.
    """
    mapping = sid_to_primary(service)
    if not mapping:
        return meta_listings
    primaries: dict[str, SessionListing] = {}
    primary_counts: dict[str, int] = {}
    passthrough: list[SessionListing] = []
    for listing in meta_listings:
        primary = mapping.get(listing.session_id)
        if primary is None:
            passthrough.append(listing)
            continue
        primary_counts[primary] = primary_counts.get(primary, 0) + listing.creatures
        if listing.session_id == primary:
            primaries[primary] = listing
    folded: list[SessionListing] = []
    for primary, count in primary_counts.items():
        proto = primaries.get(primary)
        if proto is None:
            # Primary's _meta isn't in the input list (worker offline /
            # purged). Skip — without the primary's name + node we
            # cannot render a meaningful entry.
            continue
        folded.append(
            SessionListing(
                session_id=proto.session_id,
                name=proto.name,
                running=proto.running,
                creatures=count,
                node_id=proto.node_id,
            )
        )
    return passthrough + folded


def fold_session_creatures(
    service: "TerrariumService",
    primary_sid: str,
    meta_registry: dict[str, dict[str, Any]],
    *,
    live_creatures: list[dict] | None = None,
) -> list[dict] | None:
    """For a primary cluster sid, gather creatures from every member.

    Returns ``None`` if ``primary_sid`` is not the primary of any
    cluster recorded in ``service._cluster_links``. Otherwise walks the
    cluster's member sids (sorted, primary first) and unions each
    member's ``_meta`` creature info; ``home_node`` is preserved per-
    creature so the frontend renders the correct site chip for each.

    ``meta_registry`` is the lifecycle module's ``_meta`` dict — passed
    in rather than imported to keep this module side-effect-free.

    CF-12: ``live_creatures`` is an optional list of per-creature dicts
    (as returned by ``service.list_creatures`` fan-out) that takes
    precedence over ``_meta`` lookups. Newly spawned cluster peers may
    not yet have their ``_meta`` row populated by the host's lifecycle
    layer, so reading ``_meta`` alone misses them; folding the live
    roster ensures every reachable creature surfaces immediately.
    """
    groups = cluster_groups(service)
    members = groups.get(primary_sid)
    if not members or len(members) < 2:
        return None
    creatures: list[dict] = []
    seen: set[str] = set()
    # CF-12: prefer the caller-supplied live roster — these entries
    # reflect the workers' current view, not the host's _meta cache.
    if live_creatures:
        for entry in live_creatures:
            cid = entry.get("creature_id") or entry.get("agent_id")
            if not cid or cid in seen:
                continue
            seen.add(cid)
            creatures.append(
                {
                    "creature_id": cid,
                    "agent_id": cid,
                    "name": entry.get("name", ""),
                    "home_node": entry.get("home_node", "_host") or "_host",
                    "running": bool(entry.get("running", True)),
                    "is_privileged": bool(entry.get("is_privileged", False)),
                    "model": str(entry.get("model", "") or ""),
                    "llm_name": str(entry.get("llm_name", "") or ""),
                }
            )
    for sid in sorted(members):
        meta = meta_registry.get(sid)
        if meta is None or not meta.get("on_node"):
            continue
        cid = meta.get("creature_id") or sid
        if cid in seen:
            continue
        seen.add(cid)
        creatures.append(
            {
                "creature_id": cid,
                "agent_id": cid,
                "name": meta.get("name", ""),
                "home_node": meta.get("on_node", "_host") or "_host",
                "running": bool(meta.get("running", True)),
                "is_privileged": bool(meta.get("is_privileged", False)),
                # B3/B4: surface the cached model + llm_name so the
                # ModelSwitcher renders the chip without a follow-up
                # status fetch on every cluster-member entry.
                "model": str(meta.get("model", "") or ""),
                "llm_name": str(meta.get("llm_name", "") or ""),
            }
        )
    return creatures or None


async def refresh_cluster_creatures_live(
    service: "TerrariumService", session_id: str
) -> list[dict] | None:
    """CF-12: pull a live cluster roster via ``service.list_creatures()``.

    Returns a list of per-creature dicts (in the shape that
    :func:`fold_session_creatures` accepts as ``live_creatures``) for
    every creature whose ``graph_id`` belongs to the cluster keyed by
    ``session_id``'s primary sid. ``None`` is returned when
    ``session_id`` is not part of any recorded cluster — callers fall
    through to the ``_meta``-only fold.

    This decouples ``get_session_async`` from the live-refresh logic
    (keeps lifecycle.py under the 1000-line hard cap) and lets unit
    tests probe the live-fold path directly.
    """
    primary_id = sid_to_primary(service).get(session_id, session_id)
    member_sids = cluster_groups(service).get(primary_id, set())
    if not member_sids or len(member_sids) < 2:
        return None
    try:
        live_infos = await service.list_creatures()
    except Exception:  # pragma: no cover - best-effort live refresh
        return None
    home_lookup = getattr(service, "_home", {}) or {}
    live_for_cluster: list[dict] = []
    for info in live_infos or ():
        graph_id = getattr(info, "graph_id", None) or (
            info.get("graph_id") if isinstance(info, dict) else None
        )
        if graph_id not in member_sids:
            continue
        cid = getattr(info, "creature_id", None) or (
            info.get("creature_id") if isinstance(info, dict) else None
        )
        if not cid:
            continue
        home = home_lookup.get(cid, "") or "_host"
        live_for_cluster.append(
            {
                "creature_id": cid,
                "agent_id": cid,
                "name": getattr(info, "name", "") or "",
                "home_node": home,
                "running": bool(getattr(info, "is_running", True)),
                "is_privileged": bool(getattr(info, "is_privileged", False)),
                "model": str(getattr(info, "model", "") or ""),
                "llm_name": str(getattr(info, "llm_name", "") or ""),
            }
        )
    return live_for_cluster or None


def persist_cluster_members_to_mirror(
    service: "TerrariumService", session_id: str, mirror_dir: Path
) -> None:
    """CF-6 — persist cluster membership to the host-side mirror meta.

    ``service._cluster_links`` lives only on the live
    ``MultiNodeTerrariumService`` instance — host restart wipes it,
    and ``stop_session`` (the typical caller) drops the engine graph
    and studio ``_meta`` entry. Without persistence, a subsequent
    resume can never know the closed session was part of a cluster
    and silently downgrades it to a singleton.

    Snapshot the cluster's ``(node, sid)`` pairs into each member's
    host-side mirror :class:`SessionStore` meta under the
    ``cluster_members`` key. The persistence resume route reads this
    on resume to drive a multi-worker push.

    Best-effort: any exception is swallowed (logged at debug) so a
    persistence failure cannot wedge ``stop_session``. The mirror
    writer holds the file in WAL mode; a short-lived second
    connection from here commits cleanly.
    """
    links = getattr(service, "_cluster_links", None)
    if not links:
        return
    # Walk the link graph from ``session_id`` to collect every member.
    # A 3-way cluster has the primary in two pairs; union-find by
    # membership over the (node, gid) pair set gives the full set.
    members: dict[str, str] = {}  # sid -> on_node
    queue: list[str] = [session_id]
    while queue:
        current = queue.pop()
        for pair in links:
            sids = list(pair)
            if current not in (gid for (_node, gid) in sids):
                continue
            for node, gid in sids:
                if gid not in members:
                    members[gid] = node
                    queue.append(gid)
    if len(members) < 2:
        return
    payload = [{"sid": sid, "on_node": node} for sid, node in sorted(members.items())]
    if not mirror_dir.is_dir():
        return
    for sid in members:
        path = mirror_dir / f"{sid}.kohakutr"
        if not path.exists():
            continue
        try:
            tmp_store = SessionStore(path)
            try:
                tmp_store.meta["cluster_members"] = payload
                if hasattr(tmp_store, "checkpoint"):
                    tmp_store.checkpoint()
            finally:
                tmp_store.close()
        except Exception as e:  # pragma: no cover - defensive
            logger.debug(
                "CF-6: failed to persist cluster_members",
                session_id=sid,
                error=str(e),
            )


__all__ = [
    "cluster_groups",
    "fold_session_creatures",
    "fold_session_listings",
    "persist_cluster_members_to_mirror",
    "refresh_cluster_creatures_live",
    "sid_to_primary",
]
