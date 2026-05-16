"""Routing + cache-management helpers for :mod:`multi_node_service`.

``MultiNodeTerrariumService`` keeps four caches that have to stay in
lock-step with worker membership:

- ``_home``: ``creature_id → node_id``.
- ``_creature_name_cache``: ``name → (node_id, creature_id)``.
- ``_cluster_links``: ``frozenset[(node_id, graph_id)]`` pairs.
- ``_cross_subs``: per-subscription refcount keyed by ``(my_node,
  peer_node, graph_id, channel)``.

This module owns the bulk of the routing logic — list-fan-out cache
rebuild, per-node cache purge on disconnect, per-creature ``home``
resolution with stale-route retry, and the streaming subscribe fan-out
that merges every worker's event stream into one queue.

Helpers take the service as the first argument so they can mutate the
service's caches.  Splitting these out keeps the service class under
the 1000-line hard cap without changing public behavior.
"""

import asyncio
from typing import TYPE_CHECKING, Any

from kohakuterrarium.terrarium.remote_service import CreatureNotHostedHere
from kohakuterrarium.terrarium.service import CreatureInfo
from kohakuterrarium.utils.logging import get_logger

if TYPE_CHECKING:
    from kohakuterrarium.terrarium.multi_node_service import (
        MultiNodeTerrariumService,
    )

logger = get_logger(__name__)


def purge_node_caches(service: "MultiNodeTerrariumService", node_id: str) -> None:
    """Remove a worker and purge every per-node cache.

    Called by ``MultiNodeTerrariumService.drop_remote`` when a client
    disconnects so a departed worker leaves no stale routing state
    behind: ``_home`` (route lookup), ``_creature_name_cache`` (the
    sync output-wire resolver), ``_cluster_links`` (cluster fold in
    runtime_graph_snapshot), and ``_cross_subs`` (cross-node
    subscription bookkeeping).
    """
    service._remotes.pop(node_id, None)
    # Drop home_node entries pointing at this client.
    for cid in [c for c, n in service._home.items() if n == node_id]:
        service._home.pop(cid, None)
    # Drop name-cache entries whose value targets the dead node.
    for key in [k for k, v in service._creature_name_cache.items() if v[0] == node_id]:
        service._creature_name_cache.pop(key, None)
    # Drop cluster links touching the dead node on either endpoint.
    for link in [
        link
        for link in service._cluster_links
        if any(endpoint[0] == node_id for endpoint in link)
    ]:
        service._cluster_links.discard(link)
    # Drop cross-sub bookkeeping referencing the dead node as either
    # ``my_node`` or ``peer_node``.
    for key in [k for k in service._cross_subs if k[0] == node_id or k[1] == node_id]:
        service._cross_subs.pop(key, None)


async def list_creatures_fanout(
    service: "MultiNodeTerrariumService",
) -> tuple[CreatureInfo, ...]:
    """Fan out across workers in parallel; merge prior cache on failure.

    Two properties this method must hold (both were broken before):

    - **Parallel fan-out.**  Each worker's RPC runs concurrently via
      ``asyncio.gather`` so a single slow worker can't block the UI's
      "list all creatures" call (the user observed ~12s stalls when
      one worker's RPC stalled — that is the sequential fan-out cost).
    - **Cache merge, not replace.**  When a worker's RPC raises, its
      previously-cached name → ``(node_id, creature_id)`` entries must
      SURVIVE.  Replacing the cache wholesale wipes the failing
      worker's creatures, the cross-node output-wiring resolver then
      returns ``None`` for every lookup against them, and the UI flips
      them to "offline" — the cascade the user observed.  We rebuild
      the cache by starting from the prior cache, dropping entries for
      nodes that **did** report (so their state is authoritative), and
      then merging in the fresh results.
    """
    nodes = list(service._remotes.items())
    if not nodes:
        return ()
    node_ids = [n for n, _ in nodes]
    responses = await asyncio.gather(
        *(svc.list_creatures() for _, svc in nodes),
        return_exceptions=True,
    )

    # Start from the prior cache so failing workers' entries survive.
    merged_cache: dict[str, tuple[str, str]] = dict(service._creature_name_cache)
    # For workers that successfully reported, their list IS
    # authoritative — drop any stale entries we may have for them
    # before re-adding what they reported now.
    results: list[CreatureInfo] = []
    for node_id, resp in zip(node_ids, responses):
        if isinstance(resp, BaseException):
            logger.exception("list_creatures failed on %s", node_id, exc_info=resp)
            continue
        # Authoritative refresh for this node: drop any cache rows that
        # point at this node so creature removals propagate.
        for key, val in list(merged_cache.items()):
            if val[0] == node_id:
                merged_cache.pop(key, None)
        for c in resp:
            results.append(c)
            service._home[c.creature_id] = node_id
            merged_cache[c.name] = (node_id, c.creature_id)
            merged_cache[c.creature_id] = (node_id, c.creature_id)
    # Atomic swap so concurrent reads of the cache never see a
    # half-built dict.  The resolver is sync and reads this attribute
    # without a lock.
    service._creature_name_cache = merged_cache
    return tuple(results)


async def resolve_home(
    service: "MultiNodeTerrariumService", creature_id: str
) -> str | None:
    node_id = service._home.get(creature_id)
    # ``_home`` only ever maps to connected workers — the host runs no
    # agents.  A stale entry pointing at a departed worker is refreshed
    # below.
    if node_id is not None and node_id in service._remotes:
        return node_id
    # Cache miss or stale — refresh via list_creatures fan-out.
    await service.list_creatures()
    return service._home.get(creature_id)


async def resolve_graph_home(
    service: "MultiNodeTerrariumService", graph_id: str
) -> str:
    for node_id, svc in list(service._remotes.items()):
        g = await svc.get_graph(graph_id)
        if g is not None:
            return node_id
    raise KeyError(f"graph {graph_id!r} not found on any connected worker")


async def route_per_creature(
    service: "MultiNodeTerrariumService", creature_id: str, fn
):
    """Resolve home, call ``fn(service)``, retry once on stale routing.

    Worker-side mutations that bypass the multi-node service (an
    LLM-driven ``group_remove_node`` tool call, or a direct
    ``engine.remove_creature(...)`` on a worker) leave ``_home`` stale.
    Routed ops then hit a worker that no longer hosts the creature and
    the adapter signals :class:`CreatureNotHostedHere`.  We catch THAT
    specifically — not generic :class:`KeyError`, which might surface
    from inside a successful op and would cause a destructive
    double-side-effect on retry.  After invalidating the cache and
    re-resolving we try once more; failure on the retry surfaces as
    :class:`KeyError` (the creature is genuinely gone).
    """
    node_id = await resolve_home(service, creature_id)
    if node_id is None:
        raise KeyError(creature_id)
    try:
        return await fn(service.service_for(node_id))
    except CreatureNotHostedHere:
        service._home.pop(creature_id, None)
        retry_node = await resolve_home(service, creature_id)
        if retry_node is None or retry_node == node_id:
            raise KeyError(creature_id) from None
        try:
            return await fn(service.service_for(retry_node))
        except CreatureNotHostedHere:
            raise KeyError(creature_id) from None


async def creature_graph_id(
    service: "MultiNodeTerrariumService", creature_id: str
) -> str | None:
    """Look up the actual engine ``graph_id`` of a creature on its
    home worker.  Returns ``None`` when the creature is unknown.

    Used to rewrite a route-layer ``graph_id`` (which may be a
    cluster's primary id, not the worker's own engine graph) to the id
    the worker's engine actually has — without this rewrite
    ``wire_creature`` on a peer-worker's creature 400s because the
    passed graph_id doesn't exist on that worker.
    """
    home = await resolve_home(service, creature_id)
    if home is None:
        return None
    try:
        info = await service.service_for(home).get_creature_info(creature_id)
    except (KeyError, Exception):
        return None
    return getattr(info, "graph_id", None) if info is not None else None


async def stream_subscribe(service: "MultiNodeTerrariumService", filter):
    """Fan-out: start a subscription on every worker, merge into one
    stream.  The host runs no agents, so there is nothing local to
    subscribe to."""
    queue: asyncio.Queue = asyncio.Queue()
    active = 0

    async def pump(svc):
        try:
            async for ev in svc.subscribe(filter):
                await queue.put(ev)
        except Exception:
            logger.exception("subscribe pump failed")
        finally:
            await queue.put(None)

    tasks = []
    for svc in service._remotes.values():
        tasks.append(asyncio.create_task(pump(svc)))
        active += 1
    if active == 0:
        # No workers connected — nothing to stream.
        return

    try:
        while active > 0:
            ev = await queue.get()
            if ev is None:
                active -= 1
                continue
            yield ev
    finally:
        for t in tasks:
            t.cancel()


async def runtime_graph_snapshot_fanout(
    service: "MultiNodeTerrariumService",
) -> tuple[list[dict[str, Any]], int]:
    """Collect each worker's runtime-graph snapshot.

    Returns ``(engine_graphs, version)`` where ``engine_graphs`` is the
    union of every worker's ``graphs`` list and ``version`` is the max
    reported version.  Errors on individual workers are logged and
    skipped.
    """
    snaps: list[dict[str, Any]] = []
    for node_id, svc in list(service._remotes.items()):
        try:
            snaps.append(await svc.runtime_graph_snapshot())
        except Exception:
            logger.exception("runtime_graph_snapshot failed on %s", node_id)
    engine_graphs: list[dict[str, Any]] = []
    version = 0
    for snap in snaps:
        engine_graphs.extend(snap.get("graphs", []))
        version = max(version, int(snap.get("version", 0)))
    return engine_graphs, version


__all__ = [
    "creature_graph_id",
    "list_creatures_fanout",
    "purge_node_caches",
    "resolve_graph_home",
    "resolve_home",
    "route_per_creature",
    "runtime_graph_snapshot_fanout",
    "stream_subscribe",
]
