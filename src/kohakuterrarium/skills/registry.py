"""Store procedural skills and persist their enabled state per session.

Skills use last-wins registration, while scratchpad state preserves runtime
enable and disable choices across restarts.
"""

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from kohakuterrarium.utils.logging import get_logger

if TYPE_CHECKING:
    from kohakuterrarium.core.scratchpad import Scratchpad

logger = get_logger(__name__)


SCRATCHPAD_ENABLED_KEY = "skills.enabled"


@dataclass
class Skill:
    """Represent one skill body, metadata, origin, and runtime state."""

    name: str
    description: str
    body: str
    frontmatter: dict[str, Any] = field(default_factory=dict)
    base_dir: Path | None = None
    origin: str = "user"
    disable_model_invocation: bool = False
    enabled: bool = True
    paths: list[str] = field(default_factory=list)
    allowed_tools: list[str] = field(default_factory=list)

    @property
    def invocation_blocked(self) -> bool:
        """Return whether automatic model discovery should hide this skill.

        Explicit ``info`` and ``skill`` calls remain allowed.
        """
        return self.disable_model_invocation


class SkillRegistry:
    """Manage per-agent skills with last-wins registration and persisted toggles."""

    def __init__(self, scratchpad: "Scratchpad | None" = None) -> None:
        self._skills: dict[str, Skill] = {}
        self._scratchpad = scratchpad
        self._restored: set[str] = set()

    def add(self, skill: Skill) -> None:
        """Register a skill, replacing any earlier skill with the same name."""
        if skill.name in self._skills:
            prior = self._skills[skill.name]
            logger.debug(
                "Skill overridden (last-wins)",
                skill_name=skill.name,
                prior_origin=prior.origin,
                new_origin=skill.origin,
            )
        self._apply_persisted_state(skill)
        self._skills[skill.name] = skill

    def add_many(self, skills: list[Skill]) -> None:
        for skill in skills:
            self.add(skill)

    def get(self, name: str) -> Skill | None:
        return self._skills.get(name)

    def list_enabled(self) -> list[Skill]:
        """Return enabled skills, including those hidden from model discovery."""
        return [s for s in self.all() if s.enabled]

    def all(self) -> list[Skill]:
        """Return all registered skills sorted by name."""
        return [self._skills[n] for n in sorted(self._skills)]

    def names(self) -> list[str]:
        return sorted(self._skills)

    def __contains__(self, name: str) -> bool:
        return name in self._skills

    def __len__(self) -> int:
        return len(self._skills)

    def enable(self, name: str) -> bool:
        skill = self._skills.get(name)
        if skill is None:
            return False
        skill.enabled = True
        self._persist_state()
        logger.info("Skill enabled", skill_name=name)
        return True

    def disable(self, name: str) -> bool:
        skill = self._skills.get(name)
        if skill is None:
            return False
        skill.enabled = False
        self._persist_state()
        logger.info("Skill disabled", skill_name=name)
        return True

    def set_scratchpad(self, scratchpad: "Scratchpad | None") -> None:
        """Bind persistence storage and replay overrides onto registered skills."""
        self._scratchpad = scratchpad
        for skill in self._skills.values():
            self._apply_persisted_state(skill)

    def _apply_persisted_state(self, skill: Skill) -> None:
        """Apply a persisted enabled override when one exists."""
        if self._scratchpad is None:
            return
        raw = self._scratchpad.get(SCRATCHPAD_ENABLED_KEY)
        if not raw:
            return
        try:
            persisted = json.loads(raw)
        except (TypeError, ValueError):
            logger.warning(
                "Corrupt skills-enabled scratchpad payload; ignoring",
                key=SCRATCHPAD_ENABLED_KEY,
            )
            return
        if not isinstance(persisted, dict):
            return
        if skill.name in persisted:
            skill.enabled = bool(persisted[skill.name])

    def _persist_state(self) -> None:
        if self._scratchpad is None:
            return
        payload = {name: s.enabled for name, s in self._skills.items()}
        self._scratchpad.set(SCRATCHPAD_ENABLED_KEY, json.dumps(payload))
