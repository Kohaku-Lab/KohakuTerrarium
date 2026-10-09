"""Restore the sessions that were running when the server last went down.

Runs once per app start (standalone mode) as a background task, so startup
never waits on it. Each live-session row maps to the engine that hosted it:
the shared engine, or under user isolation the owning user's pool engine.
``KT_AUTO_RESUME=0`` (``kt serve --no-resume``) turns it off; on by default.
"""

import os
from functools import partial
from typing import Any

from kohakuterrarium.terrarium import LocalTerrariumService
from kohakuterrarium.api.deps import _session_dir, get_service_legacy
from kohakuterrarium.utils.logging import get_logger
from kohakuterrarium.api.auth.config import AuthConfig, load_auth_config
from kohakuterrarium.api.auth.engine_pool import EnginePool, user_id_for_session_dir
from kohakuterrarium.studio.sessions.live import path_key
from kohakuterrarium.studio.sessions.registry import get_session_meta
from kohakuterrarium.studio.sessions.live.restore import restore_live_sessions

logger = get_logger(__name__)

_OFF = {"0", "false", "off", "no"}


def auto_resume_enabled() -> bool:
    return os.environ.get("KT_AUTO_RESUME", "1").strip().lower() not in _OFF


def service_resolver(app: Any):
    """Map a row's session directory to the service that restores it (None: not here)."""
    pool: EnginePool | None = getattr(app.state, "engine_pool", None)
    auth = getattr(app.state, "auth_config", None)
    if not isinstance(auth, AuthConfig):
        auth = load_auth_config()
    isolated = pool is not None and auth.multi_user_enabled
    shared = path_key(_session_dir())

    def resolve(session_dir: str):
        if not session_dir:
            return None
        if isolated:
            matched, user_id = user_id_for_session_dir(session_dir)
            if not matched:
                return None
            service = LocalTerrariumService(pool.get_or_create(user_id))
            service.set_runtime_graph_meta_lookup(partial(get_session_meta, service))
            return service
        if path_key(session_dir) == shared:
            return get_service_legacy()
        return None

    return resolve


async def restore_on_boot(app: Any) -> list[dict]:
    """Restore every live-session row an earlier boot left; outcomes go on ``app.state``."""
    try:
        outcomes = await restore_live_sessions(service_resolver(app))
    except Exception:  # noqa: BLE001 - boot restore must never take the server down
        logger.exception("boot session restore failed")
        outcomes = []
    app.state.restore_outcomes = outcomes
    restored = [o for o in outcomes if o.get("status") == "restored"]
    failed = [o for o in outcomes if o.get("status") == "failed"]
    if outcomes:
        logger.info(
            "Boot session restore done", restored=len(restored), failed=len(failed)
        )
    return outcomes
