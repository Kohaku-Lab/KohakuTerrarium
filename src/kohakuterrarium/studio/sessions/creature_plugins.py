"""Per-creature plugins: list + toggle.

Replaces ``routes/agents.py:list_plugins / toggle_plugin`` and
``routes/terrariums.py:terrarium_plugins / terrarium_toggle_plugin``.
"""

from kohakuterrarium.studio.sessions.lifecycle import find_creature
from kohakuterrarium.terrarium.engine import Terrarium


def list_plugins(engine: Terrarium, session_id: str, creature_id: str) -> list[dict]:
    """Return plugins with enabled / disabled status.  Empty list when
    the creature has no plugin manager."""
    agent = find_creature(engine, session_id, creature_id).agent
    if not agent.plugins:
        return []
    return agent.plugins.list_plugins()


async def toggle_plugin(
    engine: Terrarium, session_id: str, creature_id: str, plugin_name: str
) -> dict:
    """Flip a plugin's enabled state.  Returns ``{name, enabled}``."""
    agent = find_creature(engine, session_id, creature_id).agent
    if not agent.plugins:
        raise ValueError("No plugins loaded")
    mgr = agent.plugins
    if mgr.is_enabled(plugin_name):
        mgr.disable(plugin_name)
        return {"name": plugin_name, "enabled": False}
    mgr.enable(plugin_name)
    await mgr.load_pending()
    return {"name": plugin_name, "enabled": True}
