"""
Protocol and base types for legacy text-format controller commands.
"""

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable


@dataclass
class CommandResult:
    """Command content, failure text, and optional structured metadata."""

    content: str = ""
    error: str | None = None
    metadata: dict[str, Any] | None = None

    @property
    def success(self) -> bool:
        return self.error is None


@runtime_checkable
class Command(Protocol):
    """Asynchronous command that returns content for controller context."""

    @property
    def command_name(self) -> str:
        """Return the parser-visible command name."""
        ...

    @property
    def description(self) -> str:
        """Return a concise command description."""
        ...

    async def execute(self, args: str, context: Any) -> CommandResult:
        """Execute parsed arguments against a controller context."""
        ...


class BaseCommand:
    """Base class for commands."""

    @property
    def command_name(self) -> str:
        raise NotImplementedError

    @property
    def description(self) -> str:
        raise NotImplementedError

    async def execute(self, args: str, context: Any) -> CommandResult:
        """Convert command exceptions into a failed result."""
        try:
            return await self._execute(args, context)
        except Exception as e:
            return CommandResult(error=str(e))

    async def _execute(self, args: str, context: Any) -> CommandResult:
        """Implement command behavior in a subclass."""
        raise NotImplementedError


def parse_command_args(args: str) -> tuple[str, dict[str, str]]:
    """Parse one positional value and short or long option flags."""
    parts = args.strip().split()
    if not parts:
        return "", {}

    positional = ""
    kwargs: dict[str, str] = {}

    i = 0
    while i < len(parts):
        part = parts[i]
        if part.startswith("--"):
            key = part[2:]
            if i + 1 < len(parts) and not parts[i + 1].startswith("--"):
                kwargs[key] = parts[i + 1]
                i += 2
            else:
                kwargs[key] = "true"
                i += 1
        elif part.startswith("-"):
            key = part[1:]
            if i + 1 < len(parts) and not parts[i + 1].startswith("-"):
                kwargs[key] = parts[i + 1]
                i += 2
            else:
                kwargs[key] = "true"
                i += 1
        else:
            if not positional:
                positional = part
            i += 1

    return positional, kwargs
