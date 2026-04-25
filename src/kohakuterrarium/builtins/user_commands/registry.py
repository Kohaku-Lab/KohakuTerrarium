"""Built-in slash-command registry.

Command modules import only this lightweight registry so the package
``__init__`` can import command modules without creating a package-level
cycle. Public callers may import from either this module or the package.
"""

from kohakuterrarium.modules.user_command.base import BaseUserCommand

_BUILTIN_COMMANDS: dict[str, type[BaseUserCommand]] = {}
_ALIAS_MAP: dict[str, str] = {}


def register_user_command(name: str):
    """Decorator to register a built-in slash command class."""

    def decorator(cls: type[BaseUserCommand]):
        _BUILTIN_COMMANDS[name] = cls
        for alias in getattr(cls, "aliases", []):
            _ALIAS_MAP[alias] = name
        return cls

    return decorator


def get_builtin_user_command(name: str) -> BaseUserCommand | None:
    """Get an instance of a built-in command by name or alias."""
    canonical = _ALIAS_MAP.get(name, name)
    cls = _BUILTIN_COMMANDS.get(canonical)
    return cls() if cls else None


def list_builtin_user_commands() -> list[str]:
    """List all registered built-in command names."""
    return sorted(_BUILTIN_COMMANDS.keys())
