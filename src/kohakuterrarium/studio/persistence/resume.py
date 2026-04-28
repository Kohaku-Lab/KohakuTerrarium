"""Saved-session resume — Studio wiring layer.

Resume itself is an engine primitive: :meth:`Terrarium.resume`
(class-level: build engine + adopt) and :meth:`Terrarium.adopt_session`
(instance-level: adopt into running engine).  Their bodies live in
:mod:`terrarium.resume`.

This module is the *Studio* tier on top of those primitives.  It:

- Calls ``engine.adopt_session`` for HTTP / programmatic adoption.
- Registers studio-tier metadata (``_meta`` / ``_session_stores``) in
  :mod:`studio.sessions.lifecycle` so the resumed session lists like
  any freshly-started session.
- Hosts :func:`announce_migration_if_needed` (pure logging) so the
  CLI and HTTP surfaces share the same upgrade announcement.

Layer mapping:

- ``session.resume`` (low-tier): Agent rebuild from store.
- ``terrarium.resume`` + ``Terrarium.{resume, adopt_session}``:
  engine adopts saved creatures + attaches store.
- ``studio.persistence.resume`` (this module): adds studio metadata,
  returns a :class:`Session` handle.  Used by HTTP and CLI.
- ``api.routes.persistence.resume``: thin HTTP shell.
- ``cli.resume``: CLI shell with TTY handling.
"""

import os
from pathlib import Path

from kohakuterrarium.session.migrations import (
    MAX_SUPPORTED_VERSION,
    discover_versions,
    path_for_version,
)
from kohakuterrarium.session.resume import _open_store_with_migration
from kohakuterrarium.studio.sessions.handles import Session
from kohakuterrarium.studio.sessions.lifecycle import (
    _build_session_handle,
    _meta,
    _now_iso,
    _session_stores,
)
from kohakuterrarium.terrarium.engine import Terrarium
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


def announce_migration_if_needed(path: Path) -> None:
    """Log an informational line when resume will trigger a migration.

    Doesn't perform the migration itself — that's the job of
    :func:`session.migrations.ensure_latest_version` invoked by
    :func:`session.resume._open_store_with_migration`.  This just
    surfaces the "v1 → v2" transition on the terminal so the user
    isn't confused when a new file appears beside their original
    session.
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
    engine: Terrarium,
    path: Path | str,
    *,
    pwd_override: str | None = None,
    llm_override: str | None = None,
) -> Session:
    """Adopt a saved session into ``engine`` and register Studio metadata.

    Returns a :class:`Session` handle for the resulting graph.  The
    handle can be inspected by :func:`studio.sessions.lifecycle.list_sessions`
    just like a freshly-started session because the same studio
    metadata maps are populated.

    Example::

        from kohakuterrarium.studio.persistence.resume import resume_session

        async with Terrarium() as t:
            session = await resume_session(t, "alice.kohakutr")
            print(f"resumed {session.name} with {len(session.creatures)} creature(s)")
    """
    path = Path(path)
    sid = await engine.adopt_session(path, pwd=pwd_override, llm_override=llm_override)

    # Register studio-tier metadata so list_sessions / get_session
    # surface the resumed graph alongside fresh ones.  Pull config
    # path / pwd / kind from the just-attached session store.
    store = engine._session_stores.get(sid)
    meta = store.load_meta() if store is not None else {}
    kind = _resolve_session_kind(meta)
    _meta[sid] = {
        "kind": kind,
        "name": meta.get("terrarium_name") or _first_agent_name(meta) or sid,
        "config_path": meta.get("config_path", ""),
        "pwd": meta.get("pwd", os.getcwd()),
        "created_at": _now_iso(),
        "has_root": kind == "terrarium" and bool(meta.get("terrarium_creatures")),
        "resumed_from": str(path),
    }
    if store is not None:
        _session_stores[sid] = store

    logger.info(
        "Resumed session registered with studio",
        session_id=sid,
        kind=kind,
        path=str(path),
    )
    return _build_session_handle(engine, sid)


def _first_agent_name(meta: dict) -> str | None:
    agents = meta.get("agents")
    if agents and isinstance(agents, list):
        return agents[0]
    return None


def _resolve_session_kind(meta: dict) -> str:
    """Decide whether a saved session resumes as ``"creature"`` or
    ``"terrarium"`` based on its *final* recorded state — not just the
    original ``config_type``.

    A session that started as a terrarium can split down to a single
    creature; in that case the saved ``agents`` list is length 1 and
    we treat it as a creature on resume so the user-facing surface
    (frontend route, instance shape, hot-plug semantics) matches the
    actual graph it resumes into.

    A session whose ``config_type`` is ``"agent"`` is always a
    creature.  A ``"terrarium"`` session with two or more creatures or
    any live channel definitions stays a terrarium.
    """
    if meta.get("config_type") != "terrarium":
        return "creature"
    agents = meta.get("agents") or []
    if isinstance(agents, list) and len(agents) <= 1:
        # Even though config_type was terrarium, the session ended as a
        # single creature — surface it as one.  The terrarium-shape
        # bookkeeping (channels, has_root) is empty in that case anyway.
        return "creature"
    return "terrarium"


# ---------------------------------------------------------------------------
# Programmatic helpers — open + status reads (used by CLI for migration UI)
# ---------------------------------------------------------------------------


def open_store(path: Path | str):
    """Open a saved-session store with automatic format migration.

    Re-exported for callers (CLI, viewer routes) that want to read
    metadata without going through the full resume flow.
    """
    return _open_store_with_migration(path)
