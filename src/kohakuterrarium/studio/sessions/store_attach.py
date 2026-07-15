"""Attach session stores and channel persistence to managed creatures.

Creatures reuse their graph store when available; otherwise the engine's
autosession layer mints one. The lifecycle module re-exports the public attach
function for callers that share this behavior.
"""

import os
from pathlib import Path

import kohakuterrarium.terrarium.autosession as _autosession
import kohakuterrarium.terrarium.channels as channel_module
from kohakuterrarium.studio._runtime import as_engine
from kohakuterrarium.studio.sessions import index_hooks as _index_hooks
from kohakuterrarium.studio.sessions.registry import stores_for
from kohakuterrarium.terrarium import TerrariumService
from kohakuterrarium.terrarium.engine import Terrarium
from kohakuterrarium.utils.config_dir import config_dir
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


def session_dir() -> str:
    """Return ``KT_SESSION_DIR`` or the config-local session directory."""
    # Deriving the fallback from config_dir keeps isolated config roots self-contained.
    return os.environ.get("KT_SESSION_DIR") or str(config_dir() / "sessions")


def attach_session_store_for_creature(
    service: "TerrariumService",
    creature,
    *,
    config_path: str = "",
    config_type: str = "agent",
) -> None:
    """Attach a session store to ``creature``. Reuses the graph-level
    store when present, else mints ``<cid>.kohakutr``."""
    engine = as_engine(service)
    session_stores = stores_for(service)
    try:
        sid = creature.graph_id
        existing = session_stores.get(sid) or getattr(
            engine, "_session_stores", {}
        ).get(sid)
        if existing is not None:
            creature.agent.attach_session_store(existing)
            session_stores[sid] = existing
            engine._session_stores[sid] = existing
            try:
                _autosession.register_agents_in_meta(
                    existing, [creature.agent.config.name]
                )
            except Exception:
                logger.warning("meta agent-list update skipped", exc_info=True)
            # Autosession stores also need the Studio index hook so saved-session
            # listings update without an explicit reconciliation.
            _index_hooks.attach(sid, existing, session_dir())
            _retro_install_channel_persistence(engine, sid)
            return

        # Engine minting preserves validated metadata and write-before-publish;
        # Studio supplies its own path and metadata overrides.
        sess_dir = session_dir()
        cid = creature.creature_id
        store = _autosession.mint_store(
            engine,
            sid,
            path=Path(sess_dir) / f"{cid}.kohakutr",
            config_type=config_type,
            config_path=config_path,
            agents=[creature.agent.config.name],
            session_id=cid,
            pwd=str(
                getattr(getattr(creature.agent, "executor", None), "_working_dir", "")
            ),
        )
        creature.agent.attach_session_store(store)
        session_stores[sid] = store
        # Channel persistence resolves stores through the engine-owned map.
        engine._session_stores[sid] = store
        _index_hooks.attach(sid, store, sess_dir)
        _retro_install_channel_persistence(engine, sid)
    except Exception as e:  # pragma: no cover - defensive
        logger.warning("Session store creation failed", error=str(e))


def _retro_install_channel_persistence(engine: Terrarium, sid: str) -> None:
    """Install persistence callback on every channel already in env."""
    env = engine._environments.get(sid)
    if env is None:
        return
    for channel in env.shared_channels._channels.values():
        channel_module._ensure_channel_persistence(channel, engine, sid)


__all__ = ["attach_session_store_for_creature", "session_dir"]
