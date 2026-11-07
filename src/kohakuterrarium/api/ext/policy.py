"""Which package UI is live on this host, and where its entry loads from."""

import hashlib
import os

from kohakuterrarium.api.auth.config import AuthConfig
from kohakuterrarium.packages.slots.ui import UISlot
from kohakuterrarium.studio.identity import ui_extensions

UI_DEV_ENV = "KT_UI_DEV"
DESKTOP_PET_DIGEST = "c0530b3a96bed3c8ecc299759d97eefba5673edc8ac28d458b6aa7553d599aa9"


def desktop_pet(slot: UISlot) -> bool:
    """Identify the host-reviewed pet UI eligible for automatic desktop presentation."""
    if (
        slot.package != "virtual-pet"
        or slot.ui_root != "ui"
        or slot.entry != "index.js"
    ):
        return False
    try:
        paths = [
            slot.package_root / "kohaku.yaml",
            *sorted((slot.package_root / "ui").rglob("*")),
        ]
        digest = hashlib.sha256()
        for path in paths:
            if path.is_dir():
                continue
            if path.is_symlink() or path.stat().st_size > 512 * 1024:
                return False
            digest.update(path.relative_to(slot.package_root).as_posix().encode())
            digest.update(b"\0")
            digest.update(path.read_bytes().replace(b"\r\n", b"\n"))
            digest.update(b"\0")
        return digest.hexdigest() == DESKTOP_PET_DIGEST
    except OSError:
        return False


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
