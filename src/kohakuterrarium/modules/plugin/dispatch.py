"""Low-level plugin-method dispatch primitives.

Split from :mod:`manager` so that file stays under the size cap. Two helpers the
hook wrappers rely on: :func:`has_override` skips a plugin that has not
overridden a hook (leaving the ``BasePlugin`` no-op in place), and
:func:`call_method` invokes a plugin method whether it is sync or async.
"""

import inspect
from typing import Any

from kohakuterrarium.modules.plugin.base import BasePlugin


def has_override(plugin: BasePlugin, method_name: str) -> bool:
    """Whether a plugin overrides a method (not the default BasePlugin no-op)."""
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
