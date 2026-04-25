"""Shared helpers for terrarium management tools."""

from kohakuterrarium.modules.tool.base import ToolContext
from kohakuterrarium.terrarium.tool_manager import TERRARIUM_MANAGER_KEY
from kohakuterrarium.terrarium.tool_manager import TerrariumToolManager


def get_manager(context: ToolContext | None) -> TerrariumToolManager:
    """Extract the TerrariumToolManager from tool context."""
    if not context or not context.environment:
        raise RuntimeError(
            "Terrarium tools require an environment context. "
            "The root agent must be running inside a terrarium or "
            "have a TerrariumToolManager registered in its environment."
        )
    manager = context.environment.get(TERRARIUM_MANAGER_KEY)
    if manager is None:
        raise RuntimeError(
            f"No TerrariumToolManager found in environment. "
            f"Register one with environment.register('{TERRARIUM_MANAGER_KEY}', manager)."
        )
    return manager
