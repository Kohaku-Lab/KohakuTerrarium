"""Session and creature display names, kept in the session file too.

``persist_session_name`` writes a user-chosen name into ``meta["name"]`` so
history and resume show it; ``rename_creature`` renames a live creature and,
when it is alone in its session, the session as well.
"""

from typing import TYPE_CHECKING

from kohakuterrarium.studio._runtime import host_engine_or_none
from kohakuterrarium.studio.sessions.find import apply_creature_name
from kohakuterrarium.studio.sessions.registry import meta_for, stores_for
from kohakuterrarium.terrarium.graph_identity import ensure_graph_name_available
from kohakuterrarium.utils.logging import get_logger

if TYPE_CHECKING:
    from kohakuterrarium.terrarium import TerrariumService

logger = get_logger(__name__)


def persist_session_name(
    service: "TerrariumService", session_id: str, name: str
) -> None:
    """Write a user-chosen display name into the session file's ``meta["name"]``."""
    store = stores_for(service).get(session_id)
    if store is None:
        engine = host_engine_or_none(service)
        store = getattr(engine, "_session_stores", {}).get(session_id)
    if store is None or getattr(store, "_readonly", False):
        return
    try:
        store.meta["name"] = name
    except Exception as exc:  # noqa: BLE001 - the live name still applies
        logger.warning("session name persist failed", error=str(exc))


def rename_creature(service: "TerrariumService", creature_id: str, name: str) -> dict:
    """Rename a creature; a solo creature's session takes the name too.

    Lab-host path: the creature lives on a worker and no Protocol-level
    rename verb exists, so only the host-side session ``_meta["name"]`` (the
    rail label) changes and a synthesised status dict is returned; the
    worker-side agent keeps its config name.
    """
    name = (name or "").strip()
    if not name:
        raise ValueError("name must not be empty")
    engine = host_engine_or_none(service)
    meta_registry = meta_for(service)
    if engine is not None:
        creature = engine.get_creature(creature_id)
        topology = getattr(engine, "_topology", None)
        graph_id = (
            topology.creature_to_graph.get(creature.creature_id)
            if topology is not None
            else None
        )
        if graph_id is not None:
            ensure_graph_name_available(
                engine._topology,
                engine._creatures,
                graph_id=graph_id,
                name=name,
                exclude_id=creature.creature_id,
            )
        apply_creature_name(creature, name)
        sid = creature.graph_id
        graph = next(
            (g for g in engine.list_graphs() if g.graph_id == sid),
            None,
        )
        if graph is not None and len(graph.creature_ids) == 1:
            meta = meta_registry.get(sid)
            if meta is not None:
                meta["name"] = name
            persist_session_name(service, sid, name)
        return creature.get_status()
    home_lookup = getattr(service, "_home", None)
    if not isinstance(home_lookup, dict) or creature_id not in home_lookup:
        raise KeyError(f"creature {creature_id!r} not found")
    sid = None
    for candidate_sid, meta in meta_registry.items():
        if meta.get("creature_id") == creature_id:
            sid = candidate_sid
            break
    if sid is not None:
        meta_registry[sid]["name"] = name
    return {
        "creature_id": creature_id,
        "name": name,
        "graph_id": sid or "",
        "home_node": home_lookup.get(creature_id, ""),
    }
