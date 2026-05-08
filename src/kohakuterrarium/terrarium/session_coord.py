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

from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.utils.logging import get_logger

if TYPE_CHECKING:
    from kohakuterrarium.terrarium.engine import Terrarium
    from kohakuterrarium.terrarium.topology import TopologyDelta

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# low-level copy primitives (testable without the engine)
# ---------------------------------------------------------------------------


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
    for old in old_stores:
        try:
            old_meta = old.load_meta()
            sid = old_meta.get("session_id")
            if sid:
                parents.append(str(sid))
        except Exception:
            logger.debug("merge: load_meta failed", exc_info=True)
        copy_events_into(old, new_store)
    try:
        new_store.meta["session_id"] = new_store.session_id
        new_store.meta["parent_session_ids"] = parents
        new_store.meta["merged_at"] = time.time()
    except Exception:
        logger.debug("merge: meta write failed", exc_info=True)
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
    for path in new_paths:
        new_store = SessionStore(path)
        copy_events_into(old_store, new_store)
        try:
            new_store.meta["session_id"] = new_store.session_id
            new_store.meta["parent_session_ids"] = [str(parent_id)] if parent_id else []
            new_store.meta["split_at"] = time.time()
        except Exception:
            logger.debug("split: meta write failed", exc_info=True)
        new_stores.append(new_store)
    logger.info(
        "Split session store",
        parent=parent_id,
        new_paths=[str(p) for p in new_paths],
    )
    return new_stores


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


def apply_merge(
    engine: "Terrarium",
    delta: "TopologyDelta",
) -> None:
    """Coordinate session-store side of a graph merge."""
    if delta.kind != "merge" or not delta.new_graph_ids:
        return
    keep_gid = delta.new_graph_ids[0]
    drop_gids = [g for g in delta.old_graph_ids if g != keep_gid]
    old_stores: list[SessionStore] = []
    for gid in (keep_gid, *drop_gids):
        s = engine._session_stores.get(gid)
        if s is not None:
            old_stores.append(s)
    if not old_stores:
        return
    new_path = _store_path_for(engine, keep_gid)
    if new_path is None:
        # No persistence configured — keep the first store as the
        # "merged" one; later writes simply land in it.  Drop the others'
        # references from the engine.
        kept = old_stores[0]
    else:
        kept = merge_session_stores(old_stores, new_path)
    engine._session_stores[keep_gid] = kept
    for gid in drop_gids:
        engine._session_stores.pop(gid, None)
    _attach_store_to_graph(engine, keep_gid, kept)


def apply_split(
    engine: "Terrarium",
    delta: "TopologyDelta",
) -> None:
    """Coordinate session-store side of a graph split."""
    if delta.kind != "split" or not delta.old_graph_ids:
        return
    parent_gid = delta.old_graph_ids[0]
    parent = engine._session_stores.get(parent_gid)
    if parent is None:
        return
    new_paths = [_store_path_for(engine, gid) for gid in delta.new_graph_ids]
    if any(p is None for p in new_paths):
        # No session_dir — keep the parent on the largest new graph
        # (the kept one, which by topology convention is
        # ``new_graph_ids[0]``) and copy nothing onto the others.
        engine._session_stores[delta.new_graph_ids[0]] = parent
        _refresh_meta_for_split_graph(engine, delta.new_graph_ids[0], parent)
        return
    new_stores = split_session_store(parent, new_paths)
    for gid, store in zip(delta.new_graph_ids, new_stores):
        engine._session_stores[gid] = store
        _attach_store_to_graph(engine, gid, store)
        _refresh_meta_for_split_graph(engine, gid, store)


def _refresh_meta_for_split_graph(
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
    try:
        store.meta["agents"] = agents
        store.meta["config_type"] = "agent" if len(agents) <= 1 else "terrarium"
    except Exception:
        logger.debug("split: meta refresh failed", exc_info=True)


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
