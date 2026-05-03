"""Engine-level resume — adopt a saved session into a live engine.

Body of :meth:`Terrarium.resume` and :meth:`Terrarium.adopt_session`,
kept in a sibling module so ``engine.py`` stays under the file-size
cap.  Exactly the same pattern as ``terrarium/root.py`` for
``assign_root``.

Resume is an engine concern: rebuild creatures from saved config,
inject the saved conversation / scratchpad / triggers / events, wrap
each agent in a :class:`Creature`, attach the :class:`SessionStore`
at the graph level, and start everything.  The Studio tier sits on
top of this and only adds metadata bookkeeping (``_meta`` /
``_session_stores`` in :mod:`studio.sessions.lifecycle`) plus the
HTTP / CLI orchestration.
"""

import os
from pathlib import Path
from typing import TYPE_CHECKING

from kohakuterrarium.session.resume import (
    _open_store_with_migration,
    detect_session_type,
    inject_saved_state,
    resume_agent,
)
from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.terrarium.config import load_terrarium_config
from kohakuterrarium.terrarium.creature_host import Creature, _safe_creature_id
from kohakuterrarium.utils.logging import get_logger

if TYPE_CHECKING:
    from kohakuterrarium.terrarium.engine import Terrarium

logger = get_logger(__name__)


async def resume_into_engine(
    engine: "Terrarium",
    store: SessionStore | str | Path,
    *,
    pwd: str | None = None,
    llm_override: str | None = None,
) -> str:
    """Adopt a saved session into ``engine``.  Returns the graph_id.

    Reads the saved store's metadata to dispatch between the agent
    and terrarium rebuild paths, wraps the rebuilt agent(s) in
    :class:`Creature` objects, adopts them via ``engine.add_creature``,
    and attaches the ``SessionStore`` at the graph level.

    ``store`` may be a path-like to a ``.kohakutr`` file or an
    already-open :class:`SessionStore` instance (the path is then
    pulled off ``store.path``).
    """
    path = _resolve_store_path(store)
    session_type = detect_session_type(path)

    if session_type == "agent":
        return await _resume_agent_into_engine(
            engine, path, pwd=pwd, llm_override=llm_override
        )
    if session_type == "terrarium":
        return await _resume_terrarium_into_engine(engine, path, pwd=pwd)
    raise ValueError(f"Unknown saved-session type: {session_type!r}")


def _resolve_store_path(store: SessionStore | str | Path) -> Path:
    if isinstance(store, SessionStore):
        # SessionStore exposes ``path`` (set in __init__) — fall back
        # to ``str(store)`` only if the attribute is missing on a future
        # store implementation.
        return Path(getattr(store, "path", str(store)))
    return Path(str(store))


async def _resume_agent_into_engine(
    engine: "Terrarium",
    path: Path,
    *,
    pwd: str | None,
    llm_override: str | None,
) -> str:
    """Standalone-agent resume: rebuild Agent, wrap, adopt, attach."""
    # session.resume.resume_agent does the heavy lifting: opens store
    # with migration, rebuilds Agent from the saved config, injects
    # every state slot, and calls agent.attach_session_store(store).
    agent, store = resume_agent(
        path,
        pwd_override=pwd,
        io_mode=None,
        llm_override=llm_override,
    )
    creature_obj = Creature(
        creature_id=_safe_creature_id(agent.config.name),
        name=agent.config.name,
        agent=agent,
        config=agent.config,
    )
    creature = await engine.add_creature(creature_obj, start=True)

    # Attach at graph level. ``Agent.attach_session_store`` is
    # idempotent for the same store, so this updates graph bookkeeping
    # without adding a duplicate SessionOutput sink.
    await engine.attach_session(creature.graph_id, store)

    logger.info(
        "Agent session resumed into engine",
        session_id=creature.graph_id,
        creature_id=creature.creature_id,
        path=str(path),
    )
    return creature.graph_id


async def _resume_terrarium_into_engine(
    engine: "Terrarium",
    path: Path,
    *,
    pwd: str | None,
) -> str:
    """Multi-creature recipe resume: rebuild graph, inject per-creature."""
    store = _open_store_with_migration(path)
    meta = store.load_meta()
    config_path = meta.get("config_path", "")
    if not config_path:
        raise ValueError("Saved terrarium has no config_path in metadata")

    pwd = pwd or meta.get("pwd", ".")
    if pwd and os.path.isdir(pwd):
        os.chdir(pwd)

    config = load_terrarium_config(config_path)

    # Build the topology via the engine — creates every creature,
    # wires channels, assigns root, and starts the agents.  Each agent
    # begins with an empty conversation; we inject the saved state
    # below.  Since input hasn't started flowing yet, injection lands
    # before any new turn begins.
    graph = await engine.apply_recipe(config, pwd=pwd)
    sid = graph.graph_id

    # Per-creature state injection.
    #
    # Resolve each rebuilt creature to its *saved* runtime name. Saved
    # events are keyed by ``<saved_name>:e:<id>``, but ``apply_recipe``
    # may have produced a creature whose current ``config.name`` does
    # not match that key (the fresh build can re-roll random suffixes,
    # or the saved recipe stored an explicit per-creature name that the
    # rebuild collapsed to the config default). Match in this order:
    #
    #   1. fresh name already exists in the saved agents list;
    #   2. otherwise consume the next unused saved name positionally.
    #
    # Without this we'd inject under the fresh name and recover zero
    # events. Same root cause as the creature-resume bug fixed in
    # ``session.resume.align_agent_name``.
    saved_agents = list(meta.get("agents") or [])
    saved_set = set(saved_agents)
    consumed: set[str] = set()
    for cid in graph.creature_ids:
        try:
            creature = engine.get_creature(cid)
        except KeyError:
            continue
        fresh = creature.agent.config.name
        if fresh in saved_set and fresh not in consumed:
            agent_name = fresh
        else:
            # Pull the next saved name we haven't consumed yet.
            agent_name = next(
                (n for n in saved_agents if n not in consumed),
                fresh,
            )
        consumed.add(agent_name)
        inject_saved_state(creature.agent, store, agent_name)
        # ``inject_saved_state`` aligns ``agent.config.name``; mirror
        # the saved name onto the Creature wrapper too so chat-history
        # lookups (which key off ``creature.name``) hit the same
        # namespace as the events we just injected.
        creature.name = agent_name
        if creature.config is not None:
            creature.config.name = agent_name
        creature.agent.attach_session_store(store)

    # Attach at graph level. Each creature was already attached just
    # above, but ``Agent.attach_session_store`` is idempotent for the
    # same store so this preserves graph/session bookkeeping safely.
    await engine.attach_session(sid, store)
    store.update_status("running")

    logger.info(
        "Terrarium session resumed into engine",
        session_id=sid,
        path=str(path),
        creatures=len(graph.creature_ids),
    )
    return sid
