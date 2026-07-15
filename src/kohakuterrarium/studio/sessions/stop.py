"""Stop sessions and preserve cluster membership for saved mirrors.

Callers pass runtime-scoped metadata and store registries by reference, avoiding
a lifecycle import cycle while keeping local and remote teardown consistent.
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
    index_hooks: dict[str, Any] | None = None,
) -> None:
    """Stop every creature in the session and drop the graph + metadata.

    Routes through the service for remote-hosted sessions: a graph that
    lives on a worker isn't visible in the host engine, but the service
    Protocol's ``remove_creature`` proxies the call to the creature's
    home node via the multi-node home registry.

    ``meta`` and ``session_stores`` are the runtime's per-instance
    registries (``registry.meta_for`` / ``registry.stores_for``) passed
    by reference so this function mutates the same state callers
    observe through the lifecycle accessors.
    """
    # Cluster membership must be mirrored before the live service links disappear.
    persist_cluster_members_to_mirror(service, session_id, mirror_dir)
    # Standalone sessions may be local; lab-host sessions are always remote.
    engine = host_engine_or_none(service)
    graph = None
    if engine is not None:
        for g in engine.list_graphs():
            if g.graph_id == session_id:
                graph = g
                break

    if graph is not None:
        # Removing the last local creature also removes the graph.
        for cid in list(graph.creature_ids):
            try:
                await engine.remove_creature(cid)
            except KeyError:
                pass
    else:
        # Remote removal uses the worker identity cached at spawn time.
        meta_entry = meta.get(session_id)
        if meta_entry is None or not meta_entry.get("on_node"):
            raise KeyError(f"session {session_id!r} not found")
        cid = meta_entry.get("creature_id")
        if cid and hasattr(service, "remove_creature"):
            try:
                await service.remove_creature(cid)
            except KeyError:
                # Missing worker state must not leave stale host metadata.
                pass

    meta.pop(session_id, None)
    # Remove the store from every registry and close it explicitly. Lingering SQLite
    # handles block deletion on Windows, and closed stores must not remain resumable.
    store = session_stores.pop(session_id, None)
    engine_stores = getattr(engine, "_session_stores", None) if engine else None
    if isinstance(engine_stores, dict):
        store = engine_stores.pop(session_id, None) or store
    # Flush and detach indexing while the store is still usable; detach is best-effort.
    if index_hooks is not None:
        hook = index_hooks.pop(session_id, None)
        if hook is not None:
            try:
                hook.flush()
                hook.detach()
            except Exception as e:
                logger.warning(
                    "Failed to detach session-index hook on stop",
                    session_id=session_id,
                    error=str(e),
                    exc_info=True,
                )
    if store is not None and hasattr(store, "close"):
        try:
            store.close()
        except Exception as e:
            logger.warning(
                "Failed to close session store on stop",
                session_id=session_id,
                error=str(e),
                exc_info=True,
            )
    logger.info("Session stopped", session_id=session_id)
