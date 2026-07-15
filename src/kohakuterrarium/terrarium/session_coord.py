"""Session merge/split coordinator for the Terrarium engine.

When the engine's topology changes such that the membership of a graph
shifts (two graphs merging into one, or one graph fragmenting into
several), the session stores attached to those graphs must follow the
same shape — otherwise the user would lose history.  The rule:

- **Merge** (two graphs become one): create a new store, copy events
  from both old stores into it, attach to the merged graph.
- **Split** (one graph becomes two): copy the old store into each new
  graph, so each child carries the full pre-split history.
- Topology changes that don't affect membership reuse the existing
  store.

Only graphs that already had a session attached are coordinated; an
unattached graph stays unattached on the other side.

The coordinator is only called from inside ``terrarium.channels``;
external callers should go through ``Terrarium.connect`` /
``Terrarium.disconnect``.
"""

import time
from pathlib import Path
from typing import TYPE_CHECKING

from kohakuterrarium.errors import SessionNotResumableError
from kohakuterrarium.session.store import SessionStore
import kohakuterrarium.terrarium.drive.topology as _drive_topology
import kohakuterrarium.terrarium.topology_leftovers as _topo_leftovers
from kohakuterrarium.utils.logging import get_logger

if TYPE_CHECKING:
    from kohakuterrarium.terrarium.engine import Terrarium
    from kohakuterrarium.terrarium.topology import TopologyDelta

logger = get_logger(__name__)


def copy_events_into(src: SessionStore, dst: SessionStore) -> int:
    """Copy every event in ``src`` into ``dst`` preserving agent
    namespaces.  Returns the number of events copied.

    The destination is appended to via the public ``append_event`` API
    so its monotonic counters stay consistent.  Original ``ts`` and
    other payload fields are preserved; ``event_id`` is re-stamped by
    ``dst`` (a re-stamp is fine — order is what callers care about).
    """
    # Cache-enabled stores might still have pending writes; force a
    # flush so ``discover_agents_from_events`` sees everything.
    try:
        src.events.flush_cache()
    except Exception:
        pass
    n = 0
    for agent in src.discover_agents_from_events():
        for raw in src.get_events(agent):
            data = dict(raw)
            event_type = data.pop("type", "event")
            # ``append_event`` sets event_id itself; clear any stale id.
            data.pop("event_id", None)
            kwargs = {}
            for fld in ("turn_index", "spawned_in_turn", "branch_id"):
                if fld in data:
                    kwargs[fld] = data.pop(fld)
            if "parent_branch_path" in data:
                pbp = data.pop("parent_branch_path")
                if isinstance(pbp, list):
                    kwargs["parent_branch_path"] = [tuple(p) for p in pbp]
            dst.append_event(agent, event_type, data, **kwargs)
            n += 1
    return n


# Resumable-config meta keys to inherit on split/merge. Without these
# (especially ``config_path`` and ``config_snapshot``), the new child
# stores carry only ``agents`` / ``status`` and a resume request 502s
# with "Session has no config_path or config_snapshot in metadata".
_RESUMABLE_META_KEYS: tuple[str, ...] = (
    "config_type",
    "config_path",
    "config_snapshot",
    "pwd",
    "created_at",
    "hostname",
    "python_version",
    "terrarium_name",
    "terrarium_channels",
    "terrarium_creatures",
    "viewer_default_agent",
)


def _inherit_resumable_meta(src_meta: dict, dst_store: SessionStore) -> None:
    """Copy the resumable-config subset of ``src_meta`` into ``dst_store``.

    Best-effort: per-key writes that fail are logged at debug and the
    rest still propagate.
    """
    for key in _RESUMABLE_META_KEYS:
        if key not in src_meta:
            continue
        value = src_meta[key]
        if value in (None, "", {}, []):
            continue
        try:
            dst_store.meta[key] = value
        except Exception:
            logger.warning("split/merge: meta key %r write failed", key, exc_info=True)


def merge_session_stores(
    old_stores: list[SessionStore],
    new_path: str | Path,
) -> SessionStore:
    """Build a new store at ``new_path`` containing every event from
    every ``old_stores`` entry.  Returns the new store.

    Meta carries ``parent_session_ids`` so a future viewer can render
    a fork/merge history.
    """
    new_store = SessionStore(new_path)
    parents: list[str] = []
    inherited_meta: dict = {}
    for old in old_stores:
        try:
            old_meta = old.load_meta()
            sid = old_meta.get("session_id")
            if sid:
                parents.append(str(sid))
            # First old store wins for the resumable subset; this keeps
            # the merged store rebuildable from its original config.
            if not inherited_meta:
                inherited_meta = old_meta
        except Exception:
            logger.warning("merge: load_meta failed", exc_info=True)
        copy_events_into(old, new_store)
    try:
        new_store.meta["session_id"] = new_store.session_id
        new_store.meta["parent_session_ids"] = parents
        new_store.meta["merged_at"] = time.time()
        _inherit_resumable_meta(inherited_meta, new_store)
    except Exception:
        logger.warning("merge: meta write failed", exc_info=True)
    logger.info(
        "Merged session stores",
        parents=parents,
        new_path=str(new_path),
    )
    return new_store


def split_session_store(
    old_store: SessionStore,
    new_paths: list[str | Path],
) -> list[SessionStore]:
    """Duplicate ``old_store`` into one new store per ``new_paths``."""
    new_stores: list[SessionStore] = []
    try:
        old_meta = old_store.load_meta()
        parent_id = old_meta.get("session_id", "")
    except Exception:
        parent_id = ""
    try:
        full_old_meta = old_store.load_meta()
    except Exception:
        full_old_meta = {}
        logger.warning("split: load_meta failed for inheritance", exc_info=True)
    for path in new_paths:
        new_store = SessionStore(path)
        copy_events_into(old_store, new_store)
        try:
            new_store.meta["session_id"] = new_store.session_id
            new_store.meta["parent_session_ids"] = [str(parent_id)] if parent_id else []
            new_store.meta["split_at"] = time.time()
            _inherit_resumable_meta(full_old_meta, new_store)
        except Exception:
            logger.warning("split: meta write failed", exc_info=True)
        new_stores.append(new_store)
    logger.info(
        "Split session store",
        parent=parent_id,
        new_paths=[str(p) for p in new_paths],
    )
    return new_stores


def _stamp_split_meta(store: SessionStore) -> None:
    """Stamp split lineage onto a store reused in place for a split child.

    Mirrors what :func:`split_session_store` writes on a fresh duplicate so the
    reused store carries the same ``parent_session_ids`` / ``split_at`` lineage
    (the child that keeps the parent's graph_id descends from the same session).
    """
    try:
        meta = store.load_meta()
    except Exception:
        meta = {}
        logger.warning("split: reused-store load_meta failed", exc_info=True)
    parent_id = meta.get("session_id", "")
    try:
        store.meta["parent_session_ids"] = [str(parent_id)] if parent_id else []
        store.meta["split_at"] = time.time()
        _inherit_resumable_meta(meta, store)
    except Exception:
        logger.warning("split: reused-store meta write failed", exc_info=True)


# ---------------------------------------------------------------------------
# engine-level hooks — called from channels.connect_creatures /
# channels.disconnect_creatures.
# ---------------------------------------------------------------------------


def _store_path_for(engine: "Terrarium", graph_id: str) -> Path | None:
    """Where should a new store for ``graph_id`` live?

    Today we put each graph's session under
    ``<engine.session_dir>/<graph_id>.kohakutr`` when a session_dir is
    configured; otherwise sessions are file-backed wherever the user
    pointed them.  When the user never attached one, returns None.
    """
    base = getattr(engine, "_session_dir", None)
    if base is None:
        return None
    return Path(base) / f"{graph_id}.kohakutr"


def _close_superseded(store: SessionStore) -> None:
    """Close a store that merge/split has replaced.

    ``update_status=False``: the history moved to the survivor, so the
    superseded file must not flip to ``paused`` and reappear as a
    resumable ghost.
    """
    try:
        store.close(update_status=False)
    except Exception:
        logger.warning(
            "session merge/split: superseded store close failed", exc_info=True
        )


def apply_merge(
    engine: "Terrarium",
    delta: "TopologyDelta",
) -> None:
    """Coordinate session-store side of a graph merge."""
    if delta.kind != "merge" or not delta.new_graph_ids:
        return
    keep_gid = delta.new_graph_ids[0]
    drop_gids = [g for g in delta.old_graph_ids if g != keep_gid]
    keep_graph = engine._topology.graphs.get(keep_gid)
    if keep_graph is not None and any(
        graph_id in engine._session_stores for graph_id in delta.old_graph_ids
    ):
        merged_names = [
            creature.name
            for cid in keep_graph.creature_ids
            if (creature := engine._creatures.get(cid)) is not None
            and hasattr(creature, "name")
        ]
        duplicates = sorted(
            {name for name in merged_names if merged_names.count(name) > 1}
        )
        if duplicates:
            raise SessionNotResumableError(
                "Cannot merge persisted graphs with duplicate creature names: "
                + ", ".join(duplicates)
            )
    # Capture every source graph's Drive rows synchronously before any store
    # closes, so the async topology drain can move them into the survivor's
    # repository. This is a no-op on a Drive-disabled engine.
    _drive_topology.stash_merge(engine, keep_gid, list(delta.old_graph_ids))
    source_gids = [keep_gid, *drop_gids]
    # Snapshot each source graph's store + ownership BEFORE remapping —
    # the survivor is reused, but every other owned store is superseded
    # and must be closed (else its handle/writer-lock leaks forever).
    source_stores: dict[str, SessionStore] = {}
    for gid in source_gids:
        s = engine._session_stores.get(gid)
        if s is not None:
            source_stores[gid] = s
    if not source_stores:
        return
    old_stores = list(source_stores.values())
    owned = getattr(engine, "_owned_sessions", None)
    owned_source_gids = {
        gid for gid in source_gids if owned is not None and gid in owned
    }
    new_path = _store_path_for(engine, keep_gid)
    if new_path is None:
        # No persistence configured — keep the first store as the
        # "merged" one; later writes simply land in it.  Drop the others'
        # references from the engine.
        kept = old_stores[0]
        first_source_gid = next(iter(source_stores))
        kept_owned = first_source_gid in owned_source_gids
        _merge_into_existing_store(kept, old_stores[1:])
    else:
        kept_store = engine._session_stores.get(keep_gid)
        kept_path = (
            Path(getattr(kept_store, "_path", "")).resolve()
            if kept_store is not None
            else None
        )
        if kept_store is not None and kept_path == Path(new_path).resolve():
            # The merge target is the kept graph's existing file —
            # opening a second SessionStore at that path and copying
            # the kept store back into itself would duplicate every
            # row.  Instead reuse the live kept store and only copy
            # events from the OTHER (dropped) old stores into it.
            kept = kept_store
            for old in old_stores:
                if old is kept_store:
                    continue
                copy_events_into(old, kept)
            # Stamp the merge lineage on the kept store's meta so
            # downstream viewers still see ``parent_session_ids`` /
            # ``merged_at`` like the multi-store branch produces.
            parents: list[str] = []
            for old in old_stores:
                try:
                    sid = old.load_meta().get("session_id")
                    if sid:
                        parents.append(str(sid))
                except Exception:
                    logger.warning("merge: load_meta failed", exc_info=True)
            try:
                kept.meta["parent_session_ids"] = parents
                kept.meta["merged_at"] = time.time()
            except Exception:
                logger.warning("merge: meta write failed", exc_info=True)
            kept_owned = keep_gid in owned_source_gids
        else:
            # A brand-new file is minted here; the engine now holds its
            # handle, so it is engine-owned regardless of the sources.
            kept = merge_session_stores(old_stores, new_path)
            kept_owned = True
    engine._session_stores[keep_gid] = kept
    for gid in drop_gids:
        engine._session_stores.pop(gid, None)
    # Close every superseded store the engine owned; the survivor stays
    # open (it now backs the merged graph).
    for gid, store in source_stores.items():
        if store is kept:
            continue
        if gid in owned_source_gids:
            _close_superseded(store)
    if owned is not None:
        for gid in source_gids:
            owned.discard(gid)
        if kept_owned:
            owned.add(keep_gid)
    # Unresolved replay remnants follow the surviving graph — the
    # post-merge snapshot would otherwise erase them.
    _topo_leftovers.transfer_leftovers(engine, source_gids, keep_gid)
    _refresh_graph_meta(engine, keep_gid, kept)
    _attach_store_to_graph(engine, keep_gid, kept)


def apply_split(
    engine: "Terrarium",
    delta: "TopologyDelta",
) -> None:
    """Coordinate session-store side of a graph split."""
    if delta.kind != "split" or not delta.old_graph_ids:
        return
    parent_gid = delta.old_graph_ids[0]
    # Capture the parent graph's Drive rows synchronously before its store
    # closes, so the async drain can redistribute them according to the child
    # graph placement policy. This is a no-op on a Drive-disabled engine.
    _drive_topology.stash_split(engine, parent_gid, list(delta.new_graph_ids))
    parent = engine._session_stores.get(parent_gid)
    if parent is None:
        return
    owned = getattr(engine, "_owned_sessions", None)
    parent_owned = owned is not None and parent_gid in owned
    new_paths = [_store_path_for(engine, gid) for gid in delta.new_graph_ids]
    if any(p is None for p in new_paths):
        # No session_dir — keep the parent on the largest new graph
        # (the kept one, which by topology convention is
        # ``new_graph_ids[0]``) and copy nothing onto the others.
        keep_gid = delta.new_graph_ids[0]
        engine._session_stores[keep_gid] = parent
        _refresh_meta_for_split_graph(engine, keep_gid, parent)
        if owned is not None:
            for gid in delta.old_graph_ids:
                owned.discard(gid)
            if parent_owned:
                owned.add(keep_gid)
        _topo_leftovers.distribute_leftovers(engine, parent_gid, [keep_gid])
        return
    # The largest child keeps the original graph_id, so when the parent's own
    # file IS ``<parent_gid>.kohakutr`` one child's target path equals the
    # parent's still-open path. Opening a second ``SessionStore`` there would
    # duplicate every event and race the parent's live connection (surfacing as
    # ``SQLite error: disk I/O error`` under WAL); reuse the live parent store
    # for that child instead. Only the OTHER children get a fresh duplicate.
    # Mirrors the same-path reuse ``apply_merge`` already performs.
    parent_path = Path(getattr(parent, "_path", "")).resolve()
    reuse_gid = next(
        (
            gid
            for gid, p in zip(delta.new_graph_ids, new_paths)
            if Path(p).resolve() == parent_path
        ),
        None,
    )
    fresh = [
        (gid, p) for gid, p in zip(delta.new_graph_ids, new_paths) if gid != reuse_gid
    ]
    new_stores = split_session_store(parent, [p for _, p in fresh]) if fresh else []
    for (gid, _), store in zip(fresh, new_stores):
        engine._session_stores[gid] = store
        _attach_store_to_graph(engine, gid, store)
        _refresh_meta_for_split_graph(engine, gid, store)
    if reuse_gid is not None:
        _stamp_split_meta(parent)
        engine._session_stores[reuse_gid] = parent
        _attach_store_to_graph(engine, reuse_gid, parent)
        _refresh_meta_for_split_graph(engine, reuse_gid, parent)
    elif parent_gid not in delta.new_graph_ids:
        # The parent's graph_id is not among the children (defensive — the
        # largest child normally keeps it); drop its stale map entry.
        engine._session_stores.pop(parent_gid, None)
    # The parent is superseded ONLY when no child reused its live store; the
    # fresh children each got their own duplicate. Hand ownership to the
    # children (the reused store keeps the parent's ownership state).
    if reuse_gid is None and parent_owned:
        _close_superseded(parent)
    if owned is not None:
        for gid in delta.old_graph_ids:
            owned.discard(gid)
        for gid, _ in fresh:
            owned.add(gid)
        if reuse_gid is not None and parent_owned:
            owned.add(reuse_gid)
    _topo_leftovers.distribute_leftovers(engine, parent_gid, list(delta.new_graph_ids))


def _merge_into_existing_store(
    destination: SessionStore, sources: list[SessionStore]
) -> None:
    """Preserve source histories when a merge reuses an attached store."""
    destination_id = destination.meta.get("session_id", "")
    parent_ids = set(destination.meta.get("parent_session_ids", []))
    if destination_id:
        parent_ids.add(destination_id)
    for source in sources:
        source_id = source.meta.get("session_id", "")
        parent_ids.update(source.meta.get("parent_session_ids", []))
        if source_id:
            parent_ids.add(source_id)
        copy_events_into(source, destination)
    destination.meta["parent_session_ids"] = sorted(parent_ids)
    destination.meta["merged_at"] = time.time()
    destination.flush()


def _delete_meta(store: SessionStore, key: str) -> None:
    """Remove inherited reconstruction metadata from the store."""
    meta = store.meta
    if hasattr(meta, "exists") and hasattr(meta, "delete"):
        if meta.exists(key):
            meta.delete(key)
        return
    try:
        del meta[key]
    except (KeyError, TypeError):
        pass


def _refresh_graph_meta(
    engine: "Terrarium", graph_id: str, store: SessionStore
) -> None:
    """Update ``store.meta`` so it reflects the post-split graph membership.

    A split can leave a graph holding a single creature; the studio
    persistence layer (``studio.persistence.resume._resolve_session_kind``)
    keys on ``config_type`` + ``agents`` to decide whether to resume
    such a session as a creature or terrarium.  Without this refresh
    the saved meta would still claim the original (pre-split) creature
    list, so resume would build a multi-creature terrarium for what
    is now a solo creature.
    """
    g = engine._topology.graphs.get(graph_id)
    if g is None:
        return
    creatures = list(g.creature_ids)
    agents: list[str] = []
    for cid in creatures:
        c = engine._creatures.get(cid)
        if c is None:
            continue
        agents.append(getattr(c.agent.config, "name", cid))
    store.meta["agents"] = agents
    store.meta["config_type"] = "agent" if len(agents) <= 1 else "terrarium"
    store.meta["config_path"] = None
    _delete_meta(store, "config_snapshot")
    _delete_meta(store, "runtime_topology")
    store.flush()


def _refresh_meta_for_split_graph(
    engine: "Terrarium", graph_id: str, store: SessionStore
) -> None:
    """Compatibility wrapper for callers of the former split-only helper."""
    try:
        _refresh_graph_meta(engine, graph_id, store)
    except Exception:
        logger.warning("split: meta refresh failed", exc_info=True)


def _attach_store_to_graph(
    engine: "Terrarium", graph_id: str, store: SessionStore
) -> None:
    """Point every creature in ``graph_id`` at ``store``.

    Uses the same path as :meth:`Terrarium.attach_session` — calls
    ``Agent.attach_session_store`` so the ``SessionOutput`` sink, the
    trigger / sub-agent / compact managers, and any saved compact_count
    all get re-wired.  A direct ``c.agent.session_store = store`` would
    leave a dangling field with no sink, so events on the merged
    creature never reach the merged store.

    No ``session_store is not None`` gate — newly-added creatures
    coming from an unpersisted graph (the typical merge case) start
    with ``session_store = None`` and explicitly need the merged store
    attached, not skipped.
    """
    g = engine._topology.graphs.get(graph_id)
    if g is None:
        return
    for cid in g.creature_ids:
        c = engine._creatures.get(cid)
        if c is None:
            continue
        if hasattr(c.agent, "attach_session_store"):
            c.agent.attach_session_store(store)
        elif hasattr(c.agent, "session_store"):
            c.agent.session_store = store
