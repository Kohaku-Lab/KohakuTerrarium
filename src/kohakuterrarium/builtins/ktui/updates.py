"""A newer release, said once at start: for an install the launcher manages (the one-line
installers, the desktop app), unless its update mode is ``manual``. The feed is read through
the launcher's cache (``check_cache_hours``), off the UI thread, and nothing is installed:
``kt self-update`` does that."""

import asyncio
from typing import Any

from kohakuterrarium.launcher import settings as launcher_settings
from kohakuterrarium.launcher.feeds import FeedError, resolve_feed
from kohakuterrarium.launcher.migration import is_launcher_install
from kohakuterrarium.launcher.tree_ops import read_active_pointer
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


def newer_release() -> str | None:
    """The newest release's version when it is not the one running, else None."""
    if not is_launcher_install():
        return None
    cfg = launcher_settings.load()
    if cfg.update.mode == "manual":
        return None
    try:
        target = resolve_feed(cfg)
    except (FeedError, OSError) as exc:
        logger.debug("update check failed", error=str(exc))
        return None
    active = read_active_pointer()
    if active is None or target.version == active.version:
        return None
    return target.version


async def notice(app: Any) -> None:
    """Tells ``app``'s user about a newer release, if there is one."""
    version = await asyncio.to_thread(newer_release)
    if version:
        app.notify(f"KohakuTerrarium {version} is out · kt self-update", "info")


__all__ = ["newer_release", "notice"]
