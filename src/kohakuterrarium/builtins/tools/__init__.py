"""Expose built-in tool classes lazily and preserve the public catalog API."""

import importlib

from kohakuterrarium.builtins.tool_manifest import TOOL_MODULES
from kohakuterrarium.builtins.tool_catalog import (
    get_builtin_tool,
    is_builtin_tool,
    list_builtin_tools,
    register_builtin,
)

_EXPORTS = {exported: module for module, exported in TOOL_MODULES.values()}
__all__ = [
    "register_builtin",
    "get_builtin_tool",
    "list_builtin_tools",
    "is_builtin_tool",
    *_EXPORTS,
]


def __getattr__(name: str):
    module = _EXPORTS.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(importlib.import_module("kohakuterrarium." + module), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | _EXPORTS.keys())
