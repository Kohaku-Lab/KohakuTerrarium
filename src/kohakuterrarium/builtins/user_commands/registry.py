"""Register and resolve built-in slash commands without package import cycles."""

from kohakuterrarium.modules.user_command.base import BaseUserCommand

_BUILTIN_COMMANDS: dict[str, type[BaseUserCommand]] = {}
_ALIAS_MAP: dict[str, str] = {}


def register_user_command(name: str):
    """Return a decorator that registers a command class and its aliases."""

    def decorator(cls: type[BaseUserCommand]):
        _BUILTIN_COMMANDS[name] = cls
        for alias in getattr(cls, "aliases", []):
            _ALIAS_MAP[alias] = name
        return cls

    return decorator


def get_builtin_user_command(name: str) -> BaseUserCommand | None:
    """Instantiate the built-in command matching a canonical name or alias."""
    canonical = _ALIAS_MAP.get(name, name)
    cls = _BUILTIN_COMMANDS.get(canonical)
    return cls() if cls else None


def list_builtin_user_commands() -> list[str]:
    """Return registered canonical command names in sorted order."""
    return sorted(_BUILTIN_COMMANDS.keys())
