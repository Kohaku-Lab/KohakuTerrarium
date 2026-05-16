"""Per-creature model: switch + native_tool_options change set.

Replaces ``KohakuManager.agent_switch_model / creature_switch_model``
and the matching legacy routes.
"""

from typing import Any

from kohakuterrarium.studio.sessions.lifecycle import find_creature
from kohakuterrarium.terrarium import TerrariumService
from kohakuterrarium.studio._runtime import as_engine


def switch_model(
    service: "TerrariumService", session_id: str, creature_id: str, profile_name: str
) -> str:
    """Switch a creature's LLM model.  Returns the new model name."""
    engine = as_engine(service)
    creature = find_creature(engine, session_id, creature_id)
    return creature.agent.switch_model(profile_name)


def set_native_tool_options(
    service: "TerrariumService",
    session_id: str,
    creature_id: str,
    tool: str,
    values: dict[str, Any],
) -> dict:
    """Replace the option override dict for one provider-native tool."""
    engine = as_engine(service)
    agent = find_creature(engine, session_id, creature_id).agent
    helper = getattr(agent, "native_tool_options", None)
    if helper is None:
        raise ValueError(f"Creature {creature_id!r} has no native_tool_options helper")
    return helper.set(tool, values or {})
