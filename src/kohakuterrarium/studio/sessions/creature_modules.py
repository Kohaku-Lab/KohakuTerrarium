"""Per-creature configurable modules — unified dispatcher.

A *module* is anything the framework lets a user configure on a
running creature: plugins, provider-native tools, MCP servers, etc.
This module unifies the runtime configuration surface across types
under a single shape::

    {
        "type": "plugin" | "native_tool" | ...,
        "name": "permgate",
        "description": "...",
        "schema":  {<option_key>: {<spec>}},
        "options": {<option_key>: <value>},
        "enabled": True | False | None,
    }

Each module type implements a small adapter (``_inventory_*``,
``_get_options_*``, ``_set_options_*``, ``_toggle_*``) that pulls
data from the existing per-type backend (``creature_plugins``,
``creature_state``…). The dispatcher never duplicates business
logic — it just routes by ``type``.

Adding a new type takes one block in :data:`_TYPE_DISPATCH`.
"""

from typing import Any

from kohakuterrarium.studio.sessions import creature_plugins, creature_state
from kohakuterrarium.terrarium.engine import Terrarium

# ── Per-type adapters ────────────────────────────────────────────


def _inventory_plugins(
    engine: Terrarium, session_id: str, creature_id: str
) -> list[dict]:
    return [
        {
            "type": "plugin",
            "name": entry["name"],
            "description": entry.get("description", ""),
            "schema": entry.get("schema", {}),
            "options": entry.get("options", {}),
            "enabled": entry.get("enabled", True),
            "priority": entry.get("priority"),
        }
        for entry in creature_plugins.plugin_inventory(engine, session_id, creature_id)
    ]


def _inventory_native_tools(
    engine: Terrarium, session_id: str, creature_id: str
) -> list[dict]:
    return [
        {
            "type": "native_tool",
            "name": entry["name"],
            "description": entry.get("description", ""),
            "schema": entry.get("option_schema", {}),
            "options": entry.get("values", {}),
            "enabled": None,
        }
        for entry in creature_state.native_tool_inventory(
            engine, session_id, creature_id
        )
    ]


def _get_options_plugin(
    engine: Terrarium, session_id: str, creature_id: str, name: str
) -> dict:
    payload = creature_plugins.get_plugin_options(engine, session_id, creature_id, name)
    return {
        "type": "plugin",
        "name": payload["name"],
        "schema": payload.get("schema", {}),
        "options": payload.get("options", {}),
    }


def _get_options_native_tool(
    engine: Terrarium, session_id: str, creature_id: str, name: str
) -> dict:
    for entry in creature_state.native_tool_inventory(engine, session_id, creature_id):
        if entry["name"] == name:
            return {
                "type": "native_tool",
                "name": name,
                "schema": entry.get("option_schema", {}),
                "options": entry.get("values", {}),
            }
    raise KeyError(name)


def _set_options_plugin(
    engine: Terrarium,
    session_id: str,
    creature_id: str,
    name: str,
    values: dict[str, Any],
) -> dict[str, Any]:
    return creature_plugins.set_plugin_options(
        engine, session_id, creature_id, name, values or {}
    )


def _set_options_native_tool(
    engine: Terrarium,
    session_id: str,
    creature_id: str,
    name: str,
    values: dict[str, Any],
) -> dict[str, Any]:
    return creature_state.set_native_tool_options(
        engine, session_id, creature_id, name, values or {}
    )


async def _toggle_plugin(
    engine: Terrarium, session_id: str, creature_id: str, name: str
) -> dict:
    return await creature_plugins.toggle_plugin(engine, session_id, creature_id, name)


async def _toggle_unsupported(
    engine: Terrarium, session_id: str, creature_id: str, name: str
) -> dict:
    raise ValueError("Module type does not support toggle")


# ── Type dispatch table ──────────────────────────────────────────

_TYPE_DISPATCH: dict[str, dict[str, Any]] = {
    "plugin": {
        "inventory": _inventory_plugins,
        "get_options": _get_options_plugin,
        "set_options": _set_options_plugin,
        "toggle": _toggle_plugin,
    },
    "native_tool": {
        "inventory": _inventory_native_tools,
        "get_options": _get_options_native_tool,
        "set_options": _set_options_native_tool,
        "toggle": _toggle_unsupported,
    },
}


def supported_types() -> list[str]:
    return list(_TYPE_DISPATCH.keys())


def _adapter(module_type: str, op: str):
    type_entry = _TYPE_DISPATCH.get(module_type)
    if type_entry is None:
        raise ValueError(f"Unknown module type: {module_type!r}")
    fn = type_entry.get(op)
    if fn is None:
        raise ValueError(f"Module type {module_type!r} does not support {op!r}")
    return fn


# ── Public API ───────────────────────────────────────────────────


def list_modules(engine: Terrarium, session_id: str, creature_id: str) -> list[dict]:
    """Return every configurable module across all known types."""
    out: list[dict] = []
    for entry in _TYPE_DISPATCH.values():
        try:
            out.extend(entry["inventory"](engine, session_id, creature_id))
        except KeyError:
            raise
        except Exception:
            # Per-type failure shouldn't blank the whole inventory.
            continue
    return out


def get_module_options(
    engine: Terrarium,
    session_id: str,
    creature_id: str,
    module_type: str,
    name: str,
) -> dict:
    """Return ``{type, name, schema, options}`` for a single module."""
    fn = _adapter(module_type, "get_options")
    return fn(engine, session_id, creature_id, name)


def set_module_options(
    engine: Terrarium,
    session_id: str,
    creature_id: str,
    module_type: str,
    name: str,
    values: dict[str, Any],
) -> dict[str, Any]:
    """Apply runtime option overrides for a module of any supported type."""
    fn = _adapter(module_type, "set_options")
    return fn(engine, session_id, creature_id, name, values or {})


async def toggle_module(
    engine: Terrarium,
    session_id: str,
    creature_id: str,
    module_type: str,
    name: str,
) -> dict:
    """Flip a module's enabled state (only supported for some types)."""
    fn = _adapter(module_type, "toggle")
    return await fn(engine, session_id, creature_id, name)
