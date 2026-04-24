"""Runtime registry of procedural skills for an agent session.

The :class:`SkillRegistry` is created by the :mod:`bootstrap.agent_init`
step, populated by :mod:`kohakuterrarium.skills.discovery`, and exposed
on ``Agent.skills`` (and mirrored at ``Session.extra['skills_registry']``
so plugins / controller commands can reach it without the agent
reference).

Runtime enable/disable state (Qa) is kept in-memory on each
:class:`Skill` and mirrored into the session scratchpad under the key
``skills.enabled``. The scratchpad value is a JSON map of
``name -> bool``; restarts therefore preserve user toggles.
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
    """One procedural skill bundle.

    ``body`` holds the SKILL.md content after frontmatter stripping.
    ``frontmatter`` holds the raw YAML mapping (post-parse). ``base_dir``
    points at the folder containing ``SKILL.md`` so the model can
    reference sibling ``scripts/`` / ``references/`` / ``assets/`` via
    bash without framework magic.
    """

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
        """Whether the auto-invoke skill index should skip this skill.

        Aligns with Claude Code's ``disable-model-invocation`` semantics
        (spec 4.4): hidden from the system-prompt index but still
        reachable via explicit ``info(name=...)`` / ``skill(name=...)``
        tool calls.
        """
        return self.disable_model_invocation


class SkillRegistry:
    """Per-agent registry of procedural skills."""

    def __init__(self, scratchpad: "Scratchpad | None" = None) -> None:
        self._skills: dict[str, Skill] = {}
        self._scratchpad = scratchpad
        self._restored: set[str] = set()

    # ------------------------------------------------------------------
    # Population
    # ------------------------------------------------------------------

    def add(self, skill: Skill) -> None:
        """Register ``skill``. Later calls with the same name replace
        the previous entry (spec 1.1 exception: skills are last-wins)."""
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

    # ------------------------------------------------------------------
    # Lookup
    # ------------------------------------------------------------------

    def get(self, name: str) -> Skill | None:
        return self._skills.get(name)

    def list_enabled(self) -> list[Skill]:
        """Enabled skills only (still includes ``invocation_blocked``)."""
        return [s for s in self.all() if s.enabled]

    def all(self) -> list[Skill]:
        """All registered skills, sorted by name."""
        return [self._skills[n] for n in sorted(self._skills)]

    def names(self) -> list[str]:
        return sorted(self._skills)

    def __contains__(self, name: str) -> bool:
        return name in self._skills

    def __len__(self) -> int:
        return len(self._skills)

    # ------------------------------------------------------------------
    # Runtime toggles (Qa)
    # ------------------------------------------------------------------

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
        """(Re-)bind the scratchpad used for persistence."""
        self._scratchpad = scratchpad
        # Replay persisted overrides onto anything already registered.
        for skill in self._skills.values():
            self._apply_persisted_state(skill)

    # ------------------------------------------------------------------
    # Persistence helpers
    # ------------------------------------------------------------------

    def _apply_persisted_state(self, skill: Skill) -> None:
        """Override the default ``enabled`` flag with any persisted value."""
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
