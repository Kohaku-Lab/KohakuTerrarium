"""Session-store attach for studio-managed creatures.

Extracted from :mod:`studio.sessions.lifecycle` (file-size cap): owns
the per-creature store attach (graph-store reuse OR fresh mint via the
engine's autosession layer) plus the retroactive channel-persistence
install.  ``lifecycle`` re-exports :func:`attach_session_store_for_creature`
so existing callers (group hooks, the worker session attacher's mirror
of this logic) keep one import path.
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
    """The studio session dir — ``KT_SESSION_DIR`` else config-dir default."""
    # KT_SESSION_DIR overrides; else config_dir() / "sessions" so KT_CONFIG_DIR
    # alone isolates test runs from the operator's real config.
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
            _retro_install_channel_persistence(engine, sid)
            return

        # Dogfood the engine's E2 minting (validated meta, write-before-
        # publish); Studio keeps its file naming + meta via overrides.
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
        # Mirror to engine map so channel-persistence callback finds it.
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
