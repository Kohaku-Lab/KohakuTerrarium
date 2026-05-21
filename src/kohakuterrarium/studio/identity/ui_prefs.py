"""UI preferences — KV store for theme, zoom, and layout state."""

import json
from pathlib import Path
from typing import Any

from kohakuterrarium.utils.config_dir import config_dir

# Import-time default — back-compat for display callers.  The live
# read / write path goes through :func:`ui_prefs_path`.
KT_DIR = Path.home() / ".kohakuterrarium"
UI_PREFS_PATH = KT_DIR / "ui_prefs.json"

DEFAULTS: dict[str, Any] = {
    "theme": "system",
    "kt-desktop-zoom": 1.15,
    "kt-mobile-zoom": 1.25,
    "nav-expanded": True,
    "kt-force-desktop": False,
    "kt.presets.user": {},
    "kt.layout.activePreset": None,
    "kt.layout.trees": {},
    "kt.layout.instances": {},
    "kt.splitPane": {},
}


def ui_prefs_path(user_id: int | None = None) -> Path:
    """The ``ui_prefs.json`` path, honouring ``KT_CONFIG_DIR``.

    With ``user_id`` set, resolves to
    ``<config_dir>/users/<user_id>/ui_prefs.json`` — per-user prefs
    when L4 multi-user mode is on.  ``None`` falls back to the
    shared ``<config_dir>/ui_prefs.json`` (legacy / L4-off path).

    Resolved fresh each call so test isolation / operator re-homing
    works — a module constant computed once at import would not.
    """
    if user_id is None:
        return config_dir() / "ui_prefs.json"
    return config_dir() / "users" / str(int(user_id)) / "ui_prefs.json"


def load_prefs(user_id: int | None = None) -> dict[str, Any]:
    """Load UI prefs merged over the defaults.  Tolerant to
    missing / malformed files.  ``user_id`` selects per-user
    (L4) vs shared (legacy) storage."""
    path = ui_prefs_path(user_id)
    if not path.exists():
        return dict(DEFAULTS)
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return dict(DEFAULTS)
        return {**DEFAULTS, **data}
    except Exception:
        return dict(DEFAULTS)


def save_prefs(values: dict[str, Any], *, user_id: int | None = None) -> dict[str, Any]:
    """Merge ``values`` over existing prefs and persist.  Returns
    the merged view.  ``user_id`` selects per-user vs shared
    storage; the API route binds this from
    ``Depends(get_optional_user)`` when L4 is on."""
    merged = {**load_prefs(user_id), **(values or {})}
    path = ui_prefs_path(user_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=2, sort_keys=True)
    return merged
