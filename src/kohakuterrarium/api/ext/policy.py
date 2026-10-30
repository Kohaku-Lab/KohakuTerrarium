"""Which package UI is live on this host, and where its entry loads from."""

import os

from kohakuterrarium.api.auth.config import AuthConfig
from kohakuterrarium.packages.slots.ui import UISlot
from kohakuterrarium.studio.identity import ui_extensions

UI_DEV_ENV = "KT_UI_DEV"


def default_enabled(auth_config: AuthConfig) -> bool:
    """Packages contribute UI by default only on single-user hosts."""
    return not auth_config.multi_user_enabled


def slot_enabled(slot: UISlot, auth_config: AuthConfig) -> bool:
    """True when ``slot`` is valid and allowed to contribute UI."""
    if slot.error:
        return False
    return ui_extensions.is_enabled(slot.package, default=default_enabled(auth_config))


def dev_overrides() -> dict[str, str]:
    """Parse ``KT_UI_DEV=<pkg>=<url>[,<pkg>=<url>]`` into ``{pkg: url}``."""
    raw = os.environ.get(UI_DEV_ENV, "")
    out: dict[str, str] = {}
    for item in raw.split(","):
        name, sep, url = item.partition("=")
        if sep and name.strip() and url.strip():
            out[name.strip()] = url.strip()
    return out
