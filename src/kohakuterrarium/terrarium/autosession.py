"""Manage engine-owned session stores and their metadata.

The engine owns session-store creation and metadata initialization:

- ``Terrarium(session_dir=...)`` → autosession: every new graph gets
  ``<session_dir>/<graph_id>.kohakutr`` automatically.
- ``add_creature(..., session=path | True | SessionStore)`` →
  per-creature control (exact file / force-default-dir / custom store).
- ``attach_session(graph, path_or_store)`` → mint-mode: a path mints a
  store with validated meta.
- ``engine.shutdown()`` closes every store the engine minted.

Meta is always written BEFORE the store lands in
``engine._session_stores`` — the Lab worker installs an observing dict
there that snapshots ``load_meta()`` synchronously on assignment.
"""

import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

from kohakuterrarium.core.config import AgentConfig
from kohakuterrarium.core.config_serde import pack_agent_config
from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.utils.config_dir import config_dir
from kohakuterrarium.utils.logging import get_logger

if TYPE_CHECKING:
    from kohakuterrarium.terrarium.creature_host import Creature
    from kohakuterrarium.terrarium.engine import Terrarium

logger = get_logger(__name__)

SessionArg = Any  # None | bool | str | Path | SessionStore


def default_session_dir(engine: "Terrarium") -> Path:
    """The directory autosession files land in.

    ``Terrarium(session_dir=...)`` wins; otherwise ``KT_SESSION_DIR``;
    otherwise ``<config_dir>/sessions`` (the same default the Studio /
    CLI listing reads).
    """
    base = getattr(engine, "_session_dir", None)
    if base:
        return Path(base).expanduser()
    env = os.environ.get("KT_SESSION_DIR")
    if env:
        return Path(env).expanduser()
    return config_dir() / "sessions"


def mint_store(
    engine: "Terrarium",
    graph_id: str,
    *,
    path: "str | Path | None" = None,
    config_type: str = "agent",
    config_path: str = "",
    config_snapshot: dict | None = None,
    agents: list[str] | None = None,
    session_id: str | None = None,
    pwd: str | None = None,
) -> SessionStore:
    """Create a :class:`SessionStore` with validated meta for ``graph_id``.

    A fresh file gets full ``init_meta``; an existing session file is
    reopened as-is (resume-style reattach) with the agent list merged.
    The caller attaches the returned store via ``engine.attach_session``
    — meta is fully written here, before that assignment.

    ``session_id`` / ``pwd`` default to the graph id and the engine's
    working dir; Studio overrides them to keep its historical meta
    shape.
    """
    if path is None:
        directory = default_session_dir(engine)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{graph_id}.kohakutr"
    else:
        path = Path(path).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)

    store = SessionStore(path, writer_lock=True)
    existing = store.load_meta()
    if not existing.get("session_id"):
        store.init_meta(
            session_id=session_id or graph_id,
            config_type=config_type,
            config_path=config_path,
            pwd=pwd or str(getattr(engine, "_pwd", None) or Path.cwd()),
            agents=list(agents or []),
            config_snapshot=config_snapshot,
        )
    elif agents:
        register_agents_in_meta(store, agents)
    logger.info(
        "Session store minted",
        graph_id=graph_id,
        path=str(path),
        fresh=not existing.get("session_id"),
    )
    return store


def register_agents_in_meta(store: SessionStore, names: list[str]) -> None:
    """Merge ``names`` into ``meta["agents"]``; promote to terrarium.

    Mirrors the Studio attach behavior: a second agent on one store
    flips ``config_type`` to ``"terrarium"`` so resume rebuilds the
    multi-creature path.
    """
    current = list(store.meta.get("agents") or [])
    changed = False
    for name in names:
        if name not in current:
            current.append(name)
            changed = True
    if not changed:
        return
    store.meta["agents"] = current
    if len(current) > 1 and store.meta.get("config_type") == "agent":
        store.meta["config_type"] = "terrarium"


def describe_build_input(config: Any) -> tuple[str, dict | None]:
    """Derive ``(config_path, config_snapshot)`` meta from a build input.

    - str / Path → the spec verbatim (``@pkg/...`` refs stay portable
      across machines — resume resolves them locally).
    - ``AgentConfig`` → no path; a packed snapshot so resume works for
      inline configs.
    - anything else → empty.
    """
    if isinstance(config, (str, Path)):
        return str(config), None
    if isinstance(config, AgentConfig):
        try:
            return "", pack_agent_config(config)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("config_snapshot pack failed", error=str(exc))
            return "", None
    return "", None


async def attach_for_new_creature(
    engine: "Terrarium",
    creature: "Creature",
    *,
    config: Any,
    session: SessionArg,
) -> SessionStore | None:
    """Resolve ``add_creature(session=...)`` for a freshly added creature.

    Returns the store that ended up attached (or None).  Semantics:

    - ``False`` → never persist (even with autosession configured).
    - ``SessionStore`` → attach the caller's store as-is (meta merged).
    - ``str`` / ``Path`` → mint at exactly that file.
    - ``True`` → mint in the default session dir.
    - ``None`` → autosession only when the engine has ``session_dir``;
      if the graph already carries a store, join it.
    """
    gid = creature.graph_id
    existing = engine._session_stores.get(gid)

    if session is False:
        return existing
    if isinstance(session, SessionStore):
        register_agents_in_meta(session, [creature.name])
        await engine.attach_session(gid, session)
        return session

    if session is None and existing is not None:
        # Joining a graph that already persists — fold this creature in.
        register_agents_in_meta(existing, [creature.name])
        if hasattr(creature.agent, "attach_session_store"):
            creature.agent.attach_session_store(existing)
        return existing

    path: "str | Path | None"
    if isinstance(session, (str, Path)):
        path = session
    elif session is True:
        path = None
    elif session is None:
        if getattr(engine, "_session_dir", None) is None:
            return None
        path = None
    else:
        raise TypeError(
            f"session= accepts a path, True/False, or a SessionStore — "
            f"got {type(session).__name__}"
        )

    config_path, snapshot = describe_build_input(config)
    if path is None:
        # Default file name carries the creature id (``alice_3f2a...``)
        # — the saved-session list shows the stem, and ``alice_...`` is
        # what a human (and the session viewer) recognizes; bare graph
        # ids are reserved for merge/split children.
        directory = default_session_dir(engine)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{creature.creature_id}.kohakutr"
    store = mint_store(
        engine,
        gid,
        path=path,
        config_type="agent",
        config_path=config_path,
        config_snapshot=snapshot,
        agents=[creature.name],
        session_id=creature.creature_id,
    )
    engine._owned_sessions.add(gid)
    await engine.attach_session(gid, store)
    return store


async def attach_for_recipe(
    engine: "Terrarium",
    graph_id: str,
    *,
    recipe: Any,
    session: SessionArg = None,
) -> SessionStore | None:
    """Autosession for ``apply_recipe`` — one terrarium-typed store."""
    if session is False:
        return engine._session_stores.get(graph_id)
    if isinstance(session, SessionStore):
        await engine.attach_session(graph_id, session)
        return session
    if session is None and engine._session_stores.get(graph_id) is not None:
        return engine._session_stores[graph_id]

    if isinstance(session, (str, Path)):
        path: "str | Path | None" = session
    elif session is True:
        path = None
    else:  # None
        if getattr(engine, "_session_dir", None) is None:
            return None
        path = None

    names = [
        engine.get_creature(cid).name
        for cid in sorted(engine.get_graph(graph_id).creature_ids)
        if cid in engine._creatures
    ]
    store = mint_store(
        engine,
        graph_id,
        path=path,
        config_type="terrarium",
        config_path=str(recipe) if isinstance(recipe, (str, Path)) else "",
        agents=names,
    )
    engine._owned_sessions.add(graph_id)
    await engine.attach_session(graph_id, store)
    return store


def close_owned_stores(engine: "Terrarium") -> None:
    """Close every store the engine minted.  Called from ``shutdown``."""
    for gid in list(engine._owned_sessions):
        store = engine._session_stores.get(gid)
        if store is None:
            continue
        try:
            store.close()
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning(
                "owned session store close failed",
                graph_id=gid,
                error=str(exc),
            )
    engine._owned_sessions.clear()
    engine._session_stores.clear()
