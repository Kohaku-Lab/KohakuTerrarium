"""Inject path-activated procedural skill hints into controller context."""

from pathlib import Path
from typing import TYPE_CHECKING

from kohakuterrarium.utils.logging import get_logger

if TYPE_CHECKING:
    from kohakuterrarium.core.agent import Agent

logger = get_logger(__name__)


def inject_skill_path_hint(agent: "Agent") -> None:
    """Snapshot path-matched guidance for the next user turn's LLM rounds.

    Tool continuations retain the snapshot, so their history prefix stays
    unchanged. A new user turn replaces it, including clearing stale matches.
    """
    registry = getattr(agent, "skills", None)
    scanner = getattr(agent, "skill_path_scanner", None)
    controller = getattr(agent, "controller", None)
    if controller is None:
        return
    controller._skill_path_hint = None
    if registry is None or scanner is None:
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
    controller._skill_path_hint = hint
