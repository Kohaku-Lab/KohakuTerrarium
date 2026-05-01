"""Per-creature plugins: list + toggle + option mutation.

Replaces ``routes/agents.py:list_plugins / toggle_plugin`` and
``routes/terrariums.py:terrarium_plugins / terrarium_toggle_plugin``.
Mirrors :mod:`creature_state` (native_tool_options) for the runtime
options surface — the plugin schema + current values + apply path.
"""

from typing import Any

from kohakuterrarium.studio.sessions.lifecycle import find_creature
from kohakuterrarium.terrarium.engine import Terrarium


def list_plugins(engine: Terrarium, session_id: str, creature_id: str) -> list[dict]:
    """Return plugins with enabled / disabled status.  Empty list when
    the creature has no plugin manager."""
    agent = find_creature(engine, session_id, creature_id).agent
    if not agent.plugins:
        return []
    return agent.plugins.list_plugins()


def plugin_inventory(
    engine: Terrarium, session_id: str, creature_id: str
) -> list[dict]:
    """Return plugins enriched with option schema + current values.

    This is the runtime equivalent of ``native_tool_inventory`` — drives
    the schema-aware options editor in the frontend Plugins tab.
    """
    agent = find_creature(engine, session_id, creature_id).agent
    if not agent.plugins:
        return []
    return agent.plugins.list_plugins_with_options()


def get_plugin_options(
    engine: Terrarium, session_id: str, creature_id: str, plugin_name: str
) -> dict[str, Any]:
    """Return the current options dict of a single plugin.

    Raises ``KeyError`` if the plugin isn't registered.
    """
    agent = find_creature(engine, session_id, creature_id).agent
    if not agent.plugins:
        raise KeyError(plugin_name)
    plugin = agent.plugins.get_plugin(plugin_name)
    if plugin is None:
        raise KeyError(plugin_name)
    schema_fn = getattr(type(plugin), "option_schema", None)
    schema: dict[str, dict[str, Any]] = {}
    if callable(schema_fn):
        try:
            schema = schema_fn() or {}
        except Exception:
            schema = {}
    values = plugin.get_options() if hasattr(plugin, "get_options") else {}
    return {
        "name": plugin_name,
        "schema": schema,
        "options": values,
    }


def set_plugin_options(
    engine: Terrarium,
    session_id: str,
    creature_id: str,
    plugin_name: str,
    values: dict[str, Any],
) -> dict[str, Any]:
    """Validate + apply runtime option overrides on a plugin.

    Persists to the agent's session store via
    :class:`PluginOptions`. Returns the post-merge options dict.
    Raises ``KeyError`` if no such plugin, ``ValueError`` (which is
    a ``PluginOptionError``) on validation failure.
    """
    agent = find_creature(engine, session_id, creature_id).agent
    helper = getattr(agent, "plugin_options", None)
    if helper is None:
        raise ValueError(f"Creature {creature_id!r} has no plugin_options helper")
    return helper.set(plugin_name, values or {})


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
