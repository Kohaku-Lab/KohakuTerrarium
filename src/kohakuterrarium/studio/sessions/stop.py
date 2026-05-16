"""Session tear-down helpers.

Extracted from ``studio/sessions/lifecycle.py``.  Owns the
``stop_session`` body plus its cluster-member mirror snapshot.
Kept as free functions so ``lifecycle`` can keep thin delegators while
this module owns the verbose tear-down comments (local + remote paths,
SessionStore close + drop from engine registry, Windows WAL handle
note).

Module state — ``_meta`` and ``_session_stores`` — stays owned by
``lifecycle``.  Callers reach them through the delegator there; this
module receives them as parameters so it stays free of cycles back
into lifecycle.
"""

from pathlib import Path
from typing import Any

from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.studio.sessions import cluster_fold
from kohakuterrarium.studio._runtime import host_engine_or_none
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


def persist_cluster_members_to_mirror(
    service, session_id: str, mirror_dir: Path
) -> None:
    """Thin wrapper — forwards to ``cluster_fold.persist_cluster_members_to_mirror``.

    Kept here so ``stop_session`` doesn't reach across into the
    ``cluster_fold`` namespace directly; callers in ``lifecycle`` go
    through their own delegator and never import ``stop`` directly.
    """
    cluster_fold.persist_cluster_members_to_mirror(service, session_id, mirror_dir)


async def stop_session(
    service,
    session_id: str,
    *,
    meta: dict[str, dict[str, Any]],
    session_stores: dict[str, SessionStore],
    mirror_dir: Path,
) -> None:
    """Stop every creature in the session and drop the graph + metadata.

    Routes through the service for remote-hosted sessions: a graph that
    lives on a worker isn't visible in the host engine, but the service
    Protocol's ``remove_creature`` proxies the call to the creature's
    home node via the multi-node home registry.

    ``meta`` and ``session_stores`` are the lifecycle module's registries
    passed by reference so this function mutates the same state callers
    observe through ``lifecycle._meta`` / ``lifecycle._session_stores``.
    """
    # CF-6: snapshot cluster membership to the mirror BEFORE tear-down —
    # ``_cluster_links`` lives only on the live service instance.
    persist_cluster_members_to_mirror(service, session_id, mirror_dir)
    # Standalone walks its host engine; lab-host has none — every
    # session lives on a worker, reached via the remote branch below.
    engine = host_engine_or_none(service)
    graph = None
    if engine is not None:
        for g in engine.list_graphs():
            if g.graph_id == session_id:
                graph = g
                break

    if graph is not None:
        # Local path — stop every creature in the graph.  The engine
        # drops the graph automatically once the last creature leaves.
        for cid in list(graph.creature_ids):
            try:
                await engine.remove_creature(cid)
            except KeyError:
                pass
    else:
        # Remote path — the graph lives on a worker.  Look up the
        # creature_id we cached at spawn time and route the removal
        # through the service so it reaches the worker.
        meta_entry = meta.get(session_id)
        if meta_entry is None or not meta_entry.get("on_node"):
            raise KeyError(f"session {session_id!r} not found")
        cid = meta_entry.get("creature_id")
        if cid and hasattr(service, "remove_creature"):
            try:
                await service.remove_creature(cid)
            except KeyError:
                # Already gone on the worker — fall through to drop the
                # host-side meta so the UI doesn't get stuck.
                pass

    meta.pop(session_id, None)
    # Close the session store before dropping it from both registries.
    # The graph is gone (every creature was removed above), so nothing
    # else holds the store — but without an explicit close() the SQLite
    # / WAL file handle lingers until GC, which on Windows leaves the
    # `.kohakutr` file locked and makes a subsequent delete fail with
    # WinError 32. Drop it from the engine registry too so resume does
    # not hand back a closed store.  Lab-host has no host engine, so
    # there is no engine-side store registry to drop from.
    store = session_stores.pop(session_id, None)
    engine_stores = getattr(engine, "_session_stores", None) if engine else None
    if isinstance(engine_stores, dict):
        store = engine_stores.pop(session_id, None) or store
    if store is not None and hasattr(store, "close"):
        try:
            store.close()
        except Exception as e:
            logger.debug(
                "Failed to close session store on stop",
                session_id=session_id,
                error=str(e),
            )
    logger.info("Session stopped", session_id=session_id)
