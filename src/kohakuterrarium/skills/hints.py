"""Inject path-activated procedural skill hints into controller context."""

from pathlib import Path
from typing import TYPE_CHECKING

from kohakuterrarium.utils.logging import get_logger

if TYPE_CHECKING:
    from kohakuterrarium.core.agent import Agent

logger = get_logger(__name__)


def inject_skill_path_hint(agent: "Agent") -> None:
    """Queue hints for enabled skills whose path globs match the working tree.

    Missing components and no-match scans are no-ops; the scanner owns caching.
    """
    registry = getattr(agent, "skills", None)
    scanner = getattr(agent, "skill_path_scanner", None)
    controller = getattr(agent, "controller", None)
    if registry is None or scanner is None or controller is None:
        return
    if len(registry) == 0:
        return
    cwd = Path(agent.executor._working_dir) if agent.executor else Path.cwd()
    try:
        matched = scanner.matching_skills(registry, cwd)
    except Exception as exc:
        logger.debug("Skill path scan failed", error=str(exc), exc_info=True)
        return
    if not matched:
        return
    hint = scanner.format_hint(matched)
    if not hint:
        return
    controller._pending_injections.append({"role": "user", "content": hint})
