"""
Framework command protocol and base classes.

Commands are special actions the legacy/custom text-format controller
path can invoke.
"""

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable


@dataclass
class CommandResult:
    """
    Result from command execution.

    Attributes:
        content: Result content to inject into context
        error: Error message if failed
        metadata: Additional result data
    """

    content: str = ""
    error: str | None = None
    metadata: dict[str, Any] | None = None

    @property
    def success(self) -> bool:
        return self.error is None


@runtime_checkable
class Command(Protocol):
    """
    Protocol for framework commands.

    Commands are synchronous operations that return content
    to be injected into the controller's context.
    """

    @property
    def command_name(self) -> str:
        """Command name (e.g., 'read', 'info')."""
        ...

    @property
    def description(self) -> str:
        """One-line description."""
        ...

    async def execute(self, args: str, context: Any) -> CommandResult:
        """
        Execute the command.

        Args:
            args: Arguments string from the parsed controller command
            context: Controller context for accessing jobs, tools, etc.

        Returns:
            CommandResult with content to inject
        """
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
        """Execute with error handling."""
        try:
            return await self._execute(args, context)
        except Exception as e:
            return CommandResult(error=str(e))

    async def _execute(self, args: str, context: Any) -> CommandResult:
        """Internal execution - override in subclass."""
        raise NotImplementedError


def parse_command_args(args: str) -> tuple[str, dict[str, str]]:
    """
    Parse command arguments.

    Handles formats like:
    - "job_123" -> ("job_123", {})
    - "job_123 --lines 50" -> ("job_123", {"lines": "50"})
    - "job_123 --lines 50 --offset 10" -> ("job_123", {"lines": "50", "offset": "10"})

    Returns:
        (positional_arg, kwargs_dict)
    """
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
