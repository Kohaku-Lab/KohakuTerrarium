"""Resolve creatures and apply consistent display-name changes.

Studio accepts engine IDs and user-facing names, while several nested runtime
objects cache the display name. These helpers centralize both behaviors without
introducing a lifecycle import cycle.
"""

from typing import TYPE_CHECKING

from kohakuterrarium.studio._runtime import as_engine
from kohakuterrarium.terrarium.creature_host import (
    apply_creature_name as _engine_apply_creature_name,
)

if TYPE_CHECKING:
    from kohakuterrarium.terrarium import TerrariumService


def apply_creature_name(creature, name: str) -> None:
    """Push a display-name change onto every nested object that caches it.

    Delegates to the engine's canonical implementation
    (:func:`kohakuterrarium.terrarium.creature_host.apply_creature_name`)
    so studio renames and engine spawn-time renames stay byte-identical.
    This used to be a hand-maintained duplicate; it drifted when the
    canonical copy learned to retarget the live ``SessionOutput`` event
    recorder — a studio rename then kept recording events under the OLD
    name while history reads used the new display name.
    """
    _engine_apply_creature_name(creature, name)


def find_creature(service: "TerrariumService", session_id: str, name_or_id: str):
    """Resolve a creature by either its ``creature_id`` *or* its display name.

    The engine's namespace is creature_id (``alice_abc12345``), but the
    frontend often sends display names (``alice``, ``root``) because
    those are what users + tab labels see.  This helper tries the
    engine's exact-id lookup first, then falls back to matching
    ``creature.name`` within the given session, and finally — when the
    caller asks for the literal string ``"root"`` — falls back to the
    creature flagged ``is_privileged=True`` in the target session.

    ``session_id == "_"`` means "any session" — the resolver scans every
    creature in the engine.  Used by the standalone-agent WS path
    (``/ws/sessions/_/creatures/{cid}/chat``) where the frontend
    doesn't track a session_id.

    Raises :class:`KeyError` if no creature matches.
    """
    engine = as_engine(service)
    try:
        c = engine.get_creature(name_or_id)
    except KeyError:
        c = None
    if c is not None and (
        session_id == "_" or getattr(c, "graph_id", session_id) == session_id
    ):
        return c

    if session_id == "_":
        list_all = getattr(engine, "list_creatures", None)
        candidates = [cc.creature_id for cc in list_all()] if callable(list_all) else []
    else:
        candidates = []
        list_graphs = getattr(engine, "list_graphs", None)
        if callable(list_graphs):
            for graph in list_graphs():
                if graph.graph_id == session_id:
                    candidates = list(graph.creature_ids)
                    break
    for cid in candidates:
        try:
            cand = engine.get_creature(cid)
        except KeyError:
            continue
        if cand.name == name_or_id:
            return cand

    # The UI uses ``root`` as a stable alias. If several privileged creatures
    # share a graph, prefer the conventional ID, then the display name, then ID order.
    if name_or_id == "root":
        privileged: list = []
        for cid in candidates:
            try:
                cand = engine.get_creature(cid)
            except KeyError:
                continue
            if getattr(cand, "is_privileged", False):
                privileged.append(cand)
        for cand in privileged:
            if getattr(cand, "creature_id", "") == "root":
                return cand
        for cand in privileged:
            if getattr(cand, "name", "") == "root":
                return cand
        if privileged:
            return sorted(privileged, key=lambda c: c.creature_id)[0]

    raise KeyError(f"creature {name_or_id!r} not found in session {session_id!r}")
