"""
Built-in user commands (slash commands).

Registration follows the same pattern as tool_catalog.py:
@register_user_command("name") on a class triggers registration
at import time.
"""

from kohakuterrarium.modules.user_command.base import BaseUserCommand

_BUILTIN_COMMANDS: dict[str, type[BaseUserCommand]] = {}
_ALIAS_MAP: dict[str, str] = {}  # alias → canonical name


def register_user_command(name: str):
    """Decorator to register a builtin user command."""

    def decorator(cls: type[BaseUserCommand]):
        _BUILTIN_COMMANDS[name] = cls
        # Register aliases
        for alias in getattr(cls, "aliases", []):
            _ALIAS_MAP[alias] = name
        return cls

    return decorator


def get_builtin_user_command(name: str) -> BaseUserCommand | None:
    """Get an instance of a builtin command by name or alias."""
    canonical = _ALIAS_MAP.get(name, name)
    cls = _BUILTIN_COMMANDS.get(canonical)
    return cls() if cls else None


def list_builtin_user_commands() -> list[str]:
    """List all registered builtin command names."""
    return sorted(_BUILTIN_COMMANDS.keys())


# Import commands to trigger @register_user_command decorators
from kohakuterrarium.builtins.user_commands.clear import ClearCommand
from kohakuterrarium.builtins.user_commands.compact import CompactCommand
from kohakuterrarium.builtins.user_commands.exit import ExitCommand
from kohakuterrarium.builtins.user_commands.help import HelpCommand
from kohakuterrarium.builtins.user_commands.model import ModelCommand
from kohakuterrarium.builtins.user_commands.plugin import PluginCommand
from kohakuterrarium.builtins.user_commands.regen import RegenCommand
from kohakuterrarium.builtins.user_commands.settings import SettingsCommand
from kohakuterrarium.builtins.user_commands.status import StatusCommand

__all__ = [
    "register_user_command",
    "get_builtin_user_command",
    "list_builtin_user_commands",
    "ClearCommand",
    "CompactCommand",
    "ExitCommand",
    "HelpCommand",
    "ModelCommand",
    "PluginCommand",
    "RegenCommand",
    "SettingsCommand",
    "StatusCommand",
]
