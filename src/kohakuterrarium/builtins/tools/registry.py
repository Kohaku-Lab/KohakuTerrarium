"""Preserve the legacy built-in tool registry import path.

Registry behavior lives in ``builtins.tool_catalog``; these re-exports keep
existing tool modules and third-party imports compatible.
"""

from kohakuterrarium.builtins.tool_catalog import (
    get_builtin_tool,
    is_builtin_tool,
    list_builtin_tools,
    register_builtin,
)

__all__ = [
    "register_builtin",
    "get_builtin_tool",
    "list_builtin_tools",
    "is_builtin_tool",
]
