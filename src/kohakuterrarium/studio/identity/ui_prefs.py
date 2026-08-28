"""UI preferences — KV store for theme, zoom, and layout state."""

import json
from pathlib import Path
from typing import Any

from kohakuterrarium.utils.config_dir import config_dir

# Retain the legacy display constants; live reads and writes resolve through
# :func:`ui_prefs_path` so configuration-directory changes remain visible.
KT_DIR = Path.home() / ".kohakuterrarium"
UI_PREFS_PATH = KT_DIR / "ui_prefs.json"

DEFAULTS: dict[str, Any] = {
    "theme": "system",
    # These values must match the frontend ``DEFAULT_*_ZOOM`` constants because
    # backend preferences take precedence during first-launch initialization.
    "kt-desktop-zoom": 1.0,
    "kt-mobile-zoom": 1.0,
    "nav-expanded": True,
    "kt-force-desktop": False,
    "kt.presets.user": {},
    "kt.layout.activePreset": None,
    "kt.layout.trees": {},
    "kt.layout.instances": {},
    "kt.splitPane": {},
    "kt.attention.dynamicTitle": True,
    "kt.attention.completionBadge": True,
    "kt.attention.inputRequiredBadge": True,
}


def ui_prefs_path(user_id: int | None = None) -> Path:
    """Return the current shared or per-user UI preferences path.

    ``user_id`` selects ``users/<id>/ui_prefs.json``; ``None`` selects the
    legacy shared file. Resolving the config directory per call preserves test
    isolation and runtime changes to ``KT_CONFIG_DIR``.
    """
    if user_id is None:
        return config_dir() / "ui_prefs.json"
    return config_dir() / "users" / str(int(user_id)) / "ui_prefs.json"


def load_prefs(user_id: int | None = None) -> dict[str, Any]:
    """Load shared or per-user UI preferences merged over defaults.

    Missing, malformed, and non-object files degrade to a fresh copy of the
    defaults.
    """
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
    """Merge and persist shared or per-user UI preferences.

    ``None`` removes a key, allowing defaults to reappear without storing null
    placeholders. The returned mapping is the persisted merged view.
    """
    merged = {**load_prefs(user_id), **(values or {})}
    merged = {k: v for k, v in merged.items() if v is not None}
    path = ui_prefs_path(user_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=2, sort_keys=True)
    return merged
