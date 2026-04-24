"""
Explicit terrarium tool registration.

On import, registers a deferred loader with the tool catalog so that
terrarium tools (terrarium_create, terrarium_status, etc.) are loaded
on first demand. The actual tool module is only imported when the
catalog encounters a miss and calls the loader.
"""

from kohakuterrarium.builtins.tool_catalog import register_deferred_loader

_REGISTERED = False


def ensure_terrarium_tools_registered() -> None:
    """Import terrarium_tools to trigger decorator registration.

    Safe to call multiple times; only the first call does real work.
    """
    global _REGISTERED
    if _REGISTERED:
        return
    _REGISTERED = True
    import kohakuterrarium.builtins.tools.terrarium_creature  # noqa: F401
    import kohakuterrarium.builtins.tools.terrarium_lifecycle  # noqa: F401
    import kohakuterrarium.builtins.tools.terrarium_messaging  # noqa: F401


# Auto-register so any get_builtin_tool("terrarium_*") call will
# trigger loading without the caller needing to know about us.
register_deferred_loader(ensure_terrarium_tools_registered)
