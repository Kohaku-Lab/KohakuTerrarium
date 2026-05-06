"""Engine-backed session lifecycle.

Replaces ``KohakuManager.agent_create / agent_list / agent_status /
agent_stop`` plus the ``terrarium_create / list / status / stop`` and
``creature_add / list / remove`` clusters.

A *session* is a Terrarium engine *graph*.  ``start_creature`` mints a
fresh 1-creature graph; ``start_terrarium`` applies a recipe into one
graph holding every creature.  Per-creature operations live in
``creature_*.py`` siblings and accept ``(session_id, creature_id)``.
"""

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from kohakuterrarium.packages.resolve import is_package_ref, resolve_package_path
from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.studio.sessions.handles import Session, SessionListing
from kohakuterrarium.terrarium.channels import register_merge_listener
from kohakuterrarium.terrarium.config import (
    CreatureConfig,
    TerrariumConfig,
    load_terrarium_config,
)
from kohakuterrarium.terrarium.engine import Terrarium
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


# Per-session metadata captured at start time.  Engine doesn't store
# config_path / pwd / created_at — those are studio-tier concerns.
_meta: dict[str, dict[str, Any]] = {}
# Per-session attached SessionStore (keyed by session_id == graph_id).
_session_stores: dict[str, SessionStore] = {}


def _on_graph_merge(session_id: str) -> None:
    """React to a cross-graph merge by promoting the surviving
    session's kind from ``"creature"`` to ``"terrarium"`` once it has
    gained extra creatures.  Without this the v2 rail keeps showing the
    merged graph as a single-creature listing under agentAPI.list and
    only the first creature is visible — that's the 1↔2 flicker we
    used to see right after a wire."""
    meta = _meta.get(session_id)
    if meta and meta.get("kind") == "creature":
        meta["kind"] = "terrarium"


# Registration is idempotent on the listener identity so module
# reloads don't multiply the callback.
register_merge_listener(_on_graph_merge)


def _normalize_pwd(pwd: str | None) -> str | None:
    if pwd is None:
        return None
    resolved = str(Path(pwd).expanduser().resolve())
    p = Path(resolved)
    if not p.exists():
        raise ValueError(f"Working directory does not exist: {pwd}")
    if not p.is_dir():
        raise ValueError(f"Working directory is not a directory: {pwd}")
    return resolved


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _session_dir() -> str:
    default = str(Path.home() / ".kohakuterrarium" / "sessions")
    return os.environ.get("KT_SESSION_DIR", default)


# ---------------------------------------------------------------------------
# start_creature — mint a fresh 1-creature graph
# ---------------------------------------------------------------------------


async def start_creature(
    engine: Terrarium,
    *,
    config_path: str | None = None,
    config=None,
    llm_override: str | None = None,
    pwd: str | None = None,
    name: str | None = None,
) -> Session:
    """Create and start a standalone creature.  Returns a Session handle.

    ``config_path`` may be a path or a ``@pkg/...`` reference; ``config``
    is an already-loaded :class:`AgentConfig` for tests / programmatic
    callers.  Exactly one must be provided.

    ``name`` is the optional display name for the new creature; when
    omitted we keep the agent config's own name.  We override it after
    construction so the creature_id (a stable internal handle) is
    derived from the original config name and remains unique even if
    several creatures share the same display name.
    """
    pwd = _normalize_pwd(pwd)
    if config_path:
        if is_package_ref(config_path):
            config_path = str(resolve_package_path(config_path))
        creature = await engine.add_creature(
            config_path,
            llm_override=llm_override,
            pwd=pwd,
            is_privileged=True,
        )
    elif config is not None:
        creature = await engine.add_creature(
            config,
            llm_override=llm_override,
            pwd=pwd,
            is_privileged=True,
        )
    else:
        raise ValueError("Must provide config_path or config")

    if name and name.strip():
        clean = name.strip()
        _apply_creature_name(creature, clean)

    sid = creature.graph_id
    cid = creature.creature_id

    attach_session_store_for_creature(
        engine,
        creature,
        config_path=config_path or "",
    )

    _meta[sid] = {
        "kind": "creature",
        "name": creature.name,
        "config_path": config_path or "",
        "pwd": pwd or os.getcwd(),
        "created_at": _now_iso(),
    }
    logger.info("Creature session started", session_id=sid, creature_id=cid)
    return _build_session_handle(engine, sid)


def attach_session_store_for_creature(
    engine: Terrarium,
    creature,
    *,
    config_path: str = "",
    config_type: str = "agent",
) -> None:
    """Attach a fresh ``.kohakutr`` session store to ``creature``.

    Shared between :func:`start_creature` (called directly) and the
    terrarium-layer ``group_add_node`` tool (called via the
    ``terrarium.group_hooks`` indirection so the layer rule holds).
    Records the store on the module-level ``_session_stores`` map so
    :func:`stop_session` can close it later. Failures are logged but
    not propagated — session persistence is best-effort.
    """
    try:
        sess_dir = _session_dir()
        Path(sess_dir).mkdir(parents=True, exist_ok=True)
        cid = creature.creature_id
        sid = creature.graph_id
        store = SessionStore(Path(sess_dir) / f"{cid}.kohakutr")
        store.init_meta(
            session_id=cid,
            config_type=config_type,
            config_path=config_path,
            pwd=str(
                getattr(getattr(creature.agent, "executor", None), "_working_dir", "")
            ),
            agents=[creature.agent.config.name],
        )
        creature.agent.attach_session_store(store)
        _session_stores[sid] = store
    except Exception as e:  # pragma: no cover - defensive
        logger.warning("Session store creation failed", error=str(e))


# ---------------------------------------------------------------------------
# start_terrarium — apply a recipe into a single graph
# ---------------------------------------------------------------------------


async def start_terrarium(
    engine: Terrarium,
    *,
    config_path: str | None = None,
    config: TerrariumConfig | None = None,
    pwd: str | None = None,
    name: str | None = None,
) -> Session:
    """Apply a terrarium recipe into a fresh graph and start every
    creature.  Returns a Session handle (session_id == graph_id)."""
    pwd = _normalize_pwd(pwd)
    if config_path:
        if is_package_ref(config_path):
            config_path = str(resolve_package_path(config_path))
        cfg = load_terrarium_config(config_path)
    elif config is not None:
        cfg = config
    else:
        raise ValueError("Must provide config_path or config")

    graph = await engine.apply_recipe(cfg, pwd=pwd)
    sid = graph.graph_id

    # Session-store auto-attach.
    try:
        sess_dir = _session_dir()
        Path(sess_dir).mkdir(parents=True, exist_ok=True)
        store = SessionStore(Path(sess_dir) / f"{sid}.kohakutr")
        store.init_meta(
            session_id=sid,
            config_type="terrarium",
            config_path=config_path or "",
            pwd=pwd or os.getcwd(),
            agents=[c.name for c in cfg.creatures] + (["root"] if cfg.root else []),
            terrarium_name=cfg.name,
            terrarium_channels=[
                {
                    "name": ch.name,
                    "type": ch.channel_type,
                    "description": ch.description,
                }
                for ch in cfg.channels
            ],
            terrarium_creatures=[
                {
                    "name": c.name,
                    "listen": c.listen_channels,
                    "send": c.send_channels,
                }
                for c in cfg.creatures
            ],
        )
        await engine.attach_session(sid, store)
        _session_stores[sid] = store
    except Exception as e:  # pragma: no cover - defensive
        logger.warning("Session store creation failed", error=str(e))

    _meta[sid] = {
        "kind": "terrarium",
        "name": (name.strip() if name and name.strip() else cfg.name),
        "config_path": config_path or "",
        "pwd": pwd or os.getcwd(),
        "created_at": _now_iso(),
        "has_root": cfg.root is not None,
    }
    logger.info("Terrarium session started", session_id=sid)
    return _build_session_handle(engine, sid)


# ---------------------------------------------------------------------------
# query / stop
# ---------------------------------------------------------------------------


def list_sessions(engine: Terrarium) -> list[SessionListing]:
    """List every active session (one per graph)."""
    out: list[SessionListing] = []
    for graph in engine.list_graphs():
        meta = _meta.get(graph.graph_id, {})
        out.append(
            SessionListing(
                session_id=graph.graph_id,
                kind=meta.get("kind", "creature"),
                name=meta.get("name", graph.graph_id),
                running=True,
                creatures=len(graph.creature_ids),
            )
        )
    return out


def get_session(engine: Terrarium, session_id: str) -> Session:
    """Return a full :class:`Session` handle for a graph_id.

    Raises :class:`KeyError` if the session does not exist.
    """
    if session_id not in {g.graph_id for g in engine.list_graphs()}:
        raise KeyError(f"session {session_id!r} not found")
    return _build_session_handle(engine, session_id)


def _apply_creature_name(creature, name: str) -> None:
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


def rename_session(engine: Terrarium, session_id: str, name: str) -> Session:
    """Update the display name of a session (and its single creature
    when the session is creature-kind).  Returns the refreshed handle.
    """
    name = (name or "").strip()
    if not name:
        raise ValueError("name must not be empty")
    if session_id not in {g.graph_id for g in engine.list_graphs()}:
        raise KeyError(f"session {session_id!r} not found")
    meta = _meta.setdefault(session_id, {})
    meta["name"] = name
    if meta.get("kind") == "creature":
        graph = next(g for g in engine.list_graphs() if g.graph_id == session_id)
        for cid in graph.creature_ids:
            try:
                creature = engine.get_creature(cid)
            except KeyError:
                continue
            _apply_creature_name(creature, name)
            break
    return _build_session_handle(engine, session_id)


def rename_creature(engine: Terrarium, creature_id: str, name: str) -> dict:
    """Rename a creature inside a session.  Returns the refreshed
    creature status dict."""
    name = (name or "").strip()
    if not name:
        raise ValueError("name must not be empty")
    creature = engine.get_creature(creature_id)
    _apply_creature_name(creature, name)
    # Mirror onto the session meta when the creature *is* the whole
    # creature-kind session — that's the only case where the rail
    # reads from meta.name rather than the creature directly.
    sid = creature.graph_id
    meta = _meta.get(sid)
    if meta and meta.get("kind") == "creature":
        meta["name"] = name
    return creature.get_status()


async def stop_session(engine: Terrarium, session_id: str) -> None:
    """Stop every creature in the session and drop the graph + metadata."""
    graph = None
    for g in engine.list_graphs():
        if g.graph_id == session_id:
            graph = g
            break
    if graph is None:
        raise KeyError(f"session {session_id!r} not found")

    # Stop and remove every creature in the graph.  The engine drops
    # the graph automatically once the last creature leaves.
    for cid in list(graph.creature_ids):
        try:
            await engine.remove_creature(cid)
        except KeyError:
            pass

    _meta.pop(session_id, None)
    _session_stores.pop(session_id, None)
    logger.info("Session stopped", session_id=session_id)


# ---------------------------------------------------------------------------
# creature add / remove inside a running session (hot-plug)
# ---------------------------------------------------------------------------


async def add_creature(
    engine: Terrarium, session_id: str, config: CreatureConfig
) -> str:
    """Hot-plug a creature into an existing session.  Returns creature_id."""
    if session_id not in {g.graph_id for g in engine.list_graphs()}:
        raise KeyError(f"session {session_id!r} not found")
    creature = await engine.add_creature(config, graph=session_id)
    return creature.creature_id


def list_creatures(engine: Terrarium, session_id: str) -> list[dict]:
    """List every creature currently in a session."""
    graph = None
    for g in engine.list_graphs():
        if g.graph_id == session_id:
            graph = g
            break
    if graph is None:
        raise KeyError(f"session {session_id!r} not found")
    out: list[dict] = []
    for cid in graph.creature_ids:
        try:
            c = engine.get_creature(cid)
        except KeyError:
            continue
        out.append(c.get_status())
    return out


async def remove_creature(engine: Terrarium, session_id: str, creature_id: str) -> bool:
    """Remove a creature from a running session."""
    if session_id not in {g.graph_id for g in engine.list_graphs()}:
        raise KeyError(f"session {session_id!r} not found")
    try:
        engine.get_creature(creature_id)
    except KeyError:
        return False
    await engine.remove_creature(creature_id)
    return True


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _build_session_handle(engine: Terrarium, session_id: str) -> Session:
    graph = None
    for g in engine.list_graphs():
        if g.graph_id == session_id:
            graph = g
            break
    if graph is None:
        raise KeyError(f"session {session_id!r} not found")

    meta = _meta.get(session_id, {})
    creatures: list[dict] = []
    for cid in graph.creature_ids:
        try:
            c = engine.get_creature(cid)
        except KeyError:
            continue
        creatures.append(c.get_status())

    channels: list[dict] = []
    env = engine._environments.get(session_id)
    if env is not None:
        channels = env.shared_channels.get_channel_info()

    return Session(
        session_id=session_id,
        kind=meta.get("kind", "creature"),
        name=meta.get("name", session_id),
        creatures=creatures,
        channels=channels,
        created_at=meta.get("created_at", ""),
        config_path=meta.get("config_path", ""),
        pwd=meta.get("pwd", ""),
        has_root=meta.get("has_root", False),
    )


def get_session_meta(session_id: str) -> dict[str, Any]:
    """Read-only access to session metadata (used by other studio modules)."""
    return dict(_meta.get(session_id, {}))


def get_session_store(session_id: str) -> SessionStore | None:
    """Return the SessionStore attached to ``session_id`` if any."""
    return _session_stores.get(session_id)


def list_session_stores() -> list[SessionStore]:
    """Return every live SessionStore the studio has attached."""
    return [s for s in _session_stores.values() if s is not None]


def find_session_for_creature(engine: Terrarium, creature_id: str) -> str | None:
    """Look up the session_id (graph_id) hosting a creature."""
    try:
        c = engine.get_creature(creature_id)
    except KeyError:
        return None
    return c.graph_id


def find_creature(engine: Terrarium, session_id: str, name_or_id: str):
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
