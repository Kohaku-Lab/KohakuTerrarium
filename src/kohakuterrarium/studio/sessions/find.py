"""Creature resolution + rename helpers for the session lifecycle.

The studio's lifecycle layer needs to (a) resolve a creature by either
``creature_id`` or display name (the frontend sends names while the
engine namespace is ids), and (b) push a display-name change onto every
nested object that caches it (executor, trigger manager, compact
manager). Both helpers live here so :mod:`lifecycle` stays under the
1000-line hard cap mandated by ``tests/unit/test_file_sizes.py``.

The helpers are pure functions over a ``TerrariumService`` handle; they
import from :mod:`studio._runtime` and not from :mod:`lifecycle` to
avoid a cycle.
"""

from typing import TYPE_CHECKING

from kohakuterrarium.studio._runtime import as_engine

if TYPE_CHECKING:
    from kohakuterrarium.terrarium import TerrariumService


def apply_creature_name(creature, name: str) -> None:
    """Push a display-name change onto every nested object that caches
    it. Without this the executor (and its ToolContexts) keep emitting
    channel messages with the original config name, the trigger manager
    logs with the old name, etc. — even after we set ``creature.name``
    and ``agent.config.name``.
    """
    creature.name = name
    agent = getattr(creature, "agent", None)
    if agent is None:
        if creature.config is not None:
            creature.config.name = name
        return
    if getattr(agent, "config", None) is not None:
        agent.config.name = name
    if creature.config is not None:
        creature.config.name = name
    executor = getattr(agent, "executor", None)
    if executor is not None and hasattr(executor, "_agent_name"):
        executor._agent_name = name
    trigger_manager = getattr(agent, "trigger_manager", None)
    if trigger_manager is not None and hasattr(trigger_manager, "_agent_name"):
        trigger_manager._agent_name = name
    compact_manager = getattr(agent, "compact_manager", None)
    if compact_manager is not None and hasattr(compact_manager, "_agent_name"):
        compact_manager._agent_name = name


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

    # The frontend sends the literal string "root" as the tab key for
    # terrariums that declare a root agent (see
    # ``stores/chat.js:1116, 1286``).  The engine identifies the root via
    # the privileged flag set by ``Terrarium.assign_root``; resolve the
    # alias here so every per-creature HTTP/WS endpoint accepts it.
    #
    # Disambiguation order when multiple privileged creatures share a
    # graph (e.g. user merged two solo sessions):
    #   1. creature with ``creature_id == "root"`` (recipe convention)
    #   2. creature with ``name == "root"``
    #   3. first-by-sorted-id privileged creature
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
