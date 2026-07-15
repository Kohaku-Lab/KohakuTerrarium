"""Low-level plugin-method dispatch primitives.

Hook dispatch skips inherited no-ops and accepts synchronous or asynchronous methods.
"""

import inspect
from typing import Any

from kohakuterrarium.modules.plugin.base import BasePlugin


def has_override(plugin: BasePlugin, method_name: str) -> bool:
    """Return whether a plugin replaces the base no-op implementation."""
    method = getattr(type(plugin), method_name, None)
    base_method = getattr(BasePlugin, method_name, None)
    return method is not None and method is not base_method


async def call_method(
    plugin: BasePlugin, method_name: str, *args: Any, **kwargs: Any
) -> Any:
    """Call a plugin method, handling both sync and async implementations."""
    method = getattr(plugin, method_name, None)
    if method is None:
        return None
    if inspect.iscoroutinefunction(method):
        return await method(*args, **kwargs)
    return method(*args, **kwargs)


__all__ = ["call_method", "has_override"]
