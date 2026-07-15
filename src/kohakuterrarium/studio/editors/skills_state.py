"""Persisted skill toggle state.

Skill manifests provide default enabled states. This module persists user
overrides in ``skill_state.json`` so Studio can manage them independently of any
running agent.
"""

import json
from pathlib import Path

from kohakuterrarium.skills import Skill
from kohakuterrarium.utils.config_dir import config_dir


def _state_file() -> Path:
    """Return the current skill-state path.

    Resolving the config directory per call preserves test isolation and runtime
    changes to ``KT_CONFIG_DIR``.
    """
    return config_dir() / "skill_state.json"


# Retain the legacy display constant; live persistence uses ``_state_file``.
_STATE_FILE = Path.home() / ".kohakuterrarium" / "skill_state.json"


def load_state() -> dict[str, bool]:
    """Return the persisted ``{skill_name: enabled}`` map (empty when missing)."""
    path = _state_file()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k): bool(v) for k, v in data.items()}


def save_state(state: dict[str, bool]) -> None:
    """Persist skill overrides, creating the parent directory as needed."""
    path = _state_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, sort_keys=True), "utf-8")


def serialize(skill: Skill, state: dict[str, bool]) -> dict:
    """Serialize one skill with any persisted enabled-state override."""
    enabled = state.get(skill.name, skill.enabled)
    return {
        "name": skill.name,
        "description": skill.description,
        "origin": skill.origin,
        "enabled": bool(enabled),
        "disable_model_invocation": skill.disable_model_invocation,
        "paths": list(skill.paths),
        "allowed_tools": list(skill.allowed_tools),
        "base_dir": str(skill.base_dir) if skill.base_dir else None,
    }
