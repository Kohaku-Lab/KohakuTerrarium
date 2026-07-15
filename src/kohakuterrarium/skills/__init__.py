"""Discover and invoke model-readable procedural skill bundles.

Skills provide Markdown instructions through path hints, the ``skill`` tool,
or user slash commands. They are distinct from built-in tool references and do
not execute bundled scripts automatically.
"""

from kohakuterrarium.skills.command import SkillCommand
from kohakuterrarium.skills.discovery import (
    PROJECT_SKILL_ROOTS,
    USER_SKILL_ROOTS,
    discover_skills,
    load_skill_from_path,
)
from kohakuterrarium.skills.index import (
    DEFAULT_SKILL_INDEX_BUDGET_BYTES,
    build_skill_index,
)
from kohakuterrarium.skills.paths import SkillPathScanner
from kohakuterrarium.skills.registry import SCRATCHPAD_ENABLED_KEY, Skill, SkillRegistry
from kohakuterrarium.skills.user_slash import build_user_skill_turn

__all__ = (
    "DEFAULT_SKILL_INDEX_BUDGET_BYTES",
    "PROJECT_SKILL_ROOTS",
    "SCRATCHPAD_ENABLED_KEY",
    "Skill",
    "SkillCommand",
    "SkillPathScanner",
    "SkillRegistry",
    "USER_SKILL_ROOTS",
    "build_skill_index",
    "build_user_skill_turn",
    "discover_skills",
    "load_skill_from_path",
)
