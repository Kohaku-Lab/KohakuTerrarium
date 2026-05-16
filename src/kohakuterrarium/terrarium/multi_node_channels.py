"""Cluster-aware channel read paths for :class:`MultiNodeTerrariumService`.

Extracted from ``multi_node_service.py`` (CF-4 cluster fan-out
helpers). Kept as free functions so ``MultiNodeTerrariumService`` can
keep thin delegators while this file owns the verbose cluster-walk
comments and merge logic.

Three pieces live here:

- :func:`cluster_members_for` — resolve a graph_id to its cluster's
  ``(node_id, member_sid)`` pairs via the shared ``cluster_fold``
  algorithm; returns ``[]`` for non-cluster graphs.
- :func:`list_channels` — union channels across every cluster member's
  worker; falls back to "first hit wins" for non-cluster graphs.
- :func:`channel_history` — union message history across every cluster
  member, dedup by ``message_id``, sort by ``timestamp``, slice to
  ``limit``; falls back to a direct read for non-cluster graphs.
"""

from typing import Any

from kohakuterrarium.studio.sessions.cluster_fold import cluster_groups
from kohakuterrarium.terrarium.topology import ChannelInfo
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


def cluster_members_for(service, graph_id: str) -> list[tuple[str, str]]:
    """CF-4: list ``(node_id, member_sid)`` pairs for ``graph_id``.

    Reads ``service._cluster_links`` (via the shared
    :func:`cluster_fold.cluster_groups` algorithm) to discover all sids
    that belong to the same cluster as ``graph_id``.  Each sid is paired
    with the node currently hosting it (looked up via the
    ``(node_id, gid)`` endpoints stored in ``_cluster_links``), so the
    caller can fan out to the worker that actually holds that member's
    session store.

    Returns ``[]`` when ``graph_id`` is not part of any cluster
    (single-worker / not-yet-known) — the caller falls back to the
    first-hit scan path that handled that case before.
    """
    groups = cluster_groups(service)
    if not groups:
        return []
    for primary, member_sids in groups.items():
        if graph_id != primary and graph_id not in member_sids:
            continue
        sid_to_node: dict[str, str] = {}
        for pair in service._cluster_links:
            for node_id, sid in pair:
                if sid in member_sids:
                    sid_to_node[sid] = node_id
        return [
            (sid_to_node[sid], sid) for sid in sorted(member_sids) if sid in sid_to_node
        ]
    return []


async def list_channels(service, graph_id: str) -> tuple[ChannelInfo, ...]:
    """CF-4: cluster channels are replicated on every member worker's
    engine.  Returning the first hit only would lose channels that exist
    on a peer member (e.g. the user wired a channel cross-node from
    worker-B's side — the replica is registered on worker-A's engine but
    per-member channel state can diverge in principle).  Fan out across
    all cluster members and union by channel name; for a non-cluster
    graph this degenerates to the single-worker case.
    """
    members = cluster_members_for(service, graph_id)
    if members:
        seen: set[str] = set()
        out: list[ChannelInfo] = []
        for node_id, member_sid in members:
            try:
                svc = service.service_for(node_id)
            except KeyError:
                continue
            try:
                chans = await svc.list_channels(member_sid)
            except Exception:
                logger.exception(
                    "list_channels failed on %s for %s", node_id, member_sid
                )
                continue
            for ch in chans:
                if ch.name in seen:
                    continue
                seen.add(ch.name)
                out.append(ch)
        return tuple(out)
    # No cluster grouping known yet — fall back to the "ask each worker,
    # first hit wins" behaviour.  Keeps an unknown graph_id (no engine
    # hosts it) returning ``()`` rather than raising.
    for svc in service._remotes.values():
        r = await svc.list_channels(graph_id)
        if r:
            return r
    return ()


async def channel_history(
    service,
    graph_id: str,
    name: str,
    *,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """CF-4: cluster channels record their messages independently on
    each member's session store (a send on worker-A's side fires the
    broadcast cross-sub to worker-B, but each side records the message
    into its own engine's channel object).  Asking only the cluster
    primary's home returns half the conversation.  Walk every cluster
    member, merge by message timestamp, dedup by ``message_id`` when
    present.
    """
    members = cluster_members_for(service, graph_id)
    if not members:
        node_id = await service._resolve_graph_home(graph_id)
        return await service.service_for(node_id).channel_history(
            graph_id, name, limit=limit
        )
    merged: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for node_id, member_sid in members:
        try:
            svc = service.service_for(node_id)
        except KeyError:
            continue
        try:
            hist = await svc.channel_history(member_sid, name, limit=limit)
        except KeyError:
            continue
        except Exception:
            logger.exception(
                "channel_history failed on %s for %s/%s",
                node_id,
                member_sid,
                name,
            )
            continue
        for msg in hist:
            mid = msg.get("message_id") if isinstance(msg, dict) else None
            if mid:
                if mid in seen_ids:
                    continue
                seen_ids.add(mid)
            merged.append(msg)
    merged.sort(
        key=lambda m: (
            m.get("timestamp")
            if isinstance(m, dict) and m.get("timestamp") is not None
            else 0
        )
    )
    if limit is not None and limit >= 0:
        return merged[-limit:] if limit else []
    return merged
