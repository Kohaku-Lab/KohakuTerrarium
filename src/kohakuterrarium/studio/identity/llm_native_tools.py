"""Provider-native tool inventory.

Surfaces the metadata for every provider-native built-in tool so the
frontend can render them as a checkbox list inside the custom-backend
form. Pure read-side wrapper over
:func:`kohakuterrarium.builtins.tool_catalog.list_provider_native_tools`.
"""

from typing import Any

from kohakuterrarium.builtins.tool_catalog import list_provider_native_tools


def list_native_tools() -> list[dict[str, Any]]:
    """Return native-tool metadata as a list of plain dicts."""
    return list_provider_native_tools()
