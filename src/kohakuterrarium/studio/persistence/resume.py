"""Saved-session resume — Studio wiring layer.

Engine primitives reconstruct creatures and attach saved stores. This Studio
layer adds lifecycle metadata, returns a ``Session`` handle, and provides a
shared migration announcement for CLI and HTTP callers.
"""

import os
from pathlib import Path

from kohakuterrarium.errors import SessionNotFoundError
from kohakuterrarium.session.migrations import (
    MAX_SUPPORTED_VERSION,
    discover_versions,
    path_for_version,
)
from kohakuterrarium.session.resume import _open_store_with_migration
from kohakuterrarium.studio.sessions.handles import Session
from kohakuterrarium.studio.sessions.lifecycle import (
    _build_session_handle,
    now_iso as _now_iso,
)
from kohakuterrarium.studio.sessions.registry import meta_for, stores_for
from kohakuterrarium.utils.logging import get_logger
from kohakuterrarium.terrarium import TerrariumService
from kohakuterrarium.studio._runtime import as_engine

logger = get_logger(__name__)


def announce_migration_if_needed(path: Path) -> None:
    """Announce when resume will create a migrated session file.

    Migration remains the responsibility of the session resume layer; this
    function only makes the additional versioned file visible to the user.
    """
    candidates = discover_versions(path)
    if not candidates:
        return
    best_version, best_path = candidates[0]
    if best_version >= MAX_SUPPORTED_VERSION:
        return
    target = path_for_version(best_path, MAX_SUPPORTED_VERSION)
    logger.info(
        "Upgrading session format",
        source=str(best_path),
        source_version=best_version,
        target=str(target),
        target_version=MAX_SUPPORTED_VERSION,
    )
    print(
        f"[session.migration] upgrading {best_path.name} -> {target.name}",
    )


async def resume_session(
    service: "TerrariumService",
    path: Path | str,
    *,
    pwd_override: str | None = None,
    llm: str | None = None,
) -> Session:
    """Adopt a saved session and register it with Studio lifecycle state.

    The returned handle is indistinguishable from a freshly started session to
    Studio listing and lookup APIs.
    """
    engine = as_engine(service)
    path = Path(path)
    # Reject missing paths before the resume layer can create an empty SQLite
    # file as a side effect of opening it.
    if not path.exists() and not discover_versions(path):
        raise SessionNotFoundError(f"Session not found: {path}")
    sid = await engine.adopt_session(path, pwd=pwd_override, llm=llm)

    # Lifecycle registries must contain resumed graphs so listing and lookup
    # treat them like newly started sessions.
    store = engine._session_stores.get(sid)
    meta = store.load_meta() if store is not None else {}
    kind = _resolve_session_kind(meta)
    meta_for(service)[sid] = {
        "kind": kind,
        "name": meta.get("terrarium_name") or _first_agent_name(meta) or sid,
        "config_path": meta.get("config_path", ""),
        "pwd": meta.get("pwd", os.getcwd()),
        "created_at": _now_iso(),
        "has_root": kind == "terrarium" and bool(meta.get("terrarium_creatures")),
        "resumed_from": str(path),
    }
    if store is not None:
        stores_for(service)[sid] = store

    logger.info(
        "Resumed session registered with studio",
        session_id=sid,
        kind=kind,
        path=str(path),
    )
    return _build_session_handle(engine, sid, meta_for(service))


def _first_agent_name(meta: dict) -> str | None:
    agents = meta.get("agents")
    if agents and isinstance(agents, list):
        return agents[0]
    return None


def _resolve_session_kind(meta: dict) -> str:
    """Classify the resumed graph from its final recorded shape.

    Non-terrarium configs are creatures. A terrarium that ended with at most
    one agent is also surfaced as a creature so UI routing and hot-plug behavior
    match the graph being restored.
    """
    if meta.get("config_type") != "terrarium":
        return "creature"
    agents = meta.get("agents") or []
    if isinstance(agents, list) and len(agents) <= 1:
        # Final graph shape takes precedence over the original config type.
        return "creature"
    return "terrarium"


def open_store(path: Path | str):
    """Open a saved-session store with automatic format migration."""
    return _open_store_with_migration(path)
