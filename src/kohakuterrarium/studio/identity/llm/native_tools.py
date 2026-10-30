"""Provider-native tool inventory.

Exposes provider-native tool metadata for custom-backend configuration. This is
a read-only Studio wrapper over
:func:`kohakuterrarium.builtins.tool_catalog.list_provider_native_tools`.
"""

from typing import Any

from kohakuterrarium.builtins.tool_catalog import list_provider_native_tools


def list_native_tools() -> list[dict[str, Any]]:
    """Return provider-native tool metadata as Studio records."""
    return list_provider_native_tools()
