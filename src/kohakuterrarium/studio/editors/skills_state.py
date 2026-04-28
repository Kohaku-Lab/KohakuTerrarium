"""Persisted skill toggle state.

Discoverable skills carry a default ``enabled`` flag from their
manifest; this module persists user overrides to
``~/.kohakuterrarium/skill_state.json`` so the Studio can manage
them across runs even when no agent is active.
"""

import json
from pathlib import Path

from kohakuterrarium.skills import Skill

_STATE_FILE = Path.home() / ".kohakuterrarium" / "skill_state.json"


def load_state() -> dict[str, bool]:
    """Return the persisted ``{skill_name: enabled}`` map (empty when missing)."""
    if not _STATE_FILE.exists():
        return {}
    try:
        data = json.loads(_STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k): bool(v) for k, v in data.items()}


def save_state(state: dict[str, bool]) -> None:
    """Atomically persist *state* to disk (creates parent dir if needed)."""
    _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    _STATE_FILE.write_text(json.dumps(state, indent=2, sort_keys=True), "utf-8")


def serialize(skill: Skill, state: dict[str, bool]) -> dict:
    """Produce the JSON shape the frontend consumes for one skill."""
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
