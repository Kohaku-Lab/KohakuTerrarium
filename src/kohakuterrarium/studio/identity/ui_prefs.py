"""UI preferences — KV store for theme, zoom, and layout state."""

import json
from pathlib import Path
from typing import Any

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


def ui_prefs_path() -> Path:
    return UI_PREFS_PATH


def load_prefs() -> dict[str, Any]:
    """Load UI prefs merged over the defaults. Tolerant to missing/malformed files."""
    path = ui_prefs_path()
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


def save_prefs(values: dict[str, Any]) -> dict[str, Any]:
    """Merge ``values`` over existing prefs and persist. Returns the merged view."""
    merged = {**load_prefs(), **(values or {})}
    path = ui_prefs_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=2, sort_keys=True)
    return merged
