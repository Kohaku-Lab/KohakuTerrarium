"""Define slash-command layers, results, contexts, and serializable UI payloads."""

from abc import abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, ClassVar, Protocol, runtime_checkable


class CommandLayer(Enum):
    """Where the command executes."""

    INPUT = "input"
    AGENT = "agent"



def ui_text(message: str) -> dict[str, Any]:
    """Plain text block."""
    return {"type": "text", "message": message}


def ui_notify(message: str, *, level: str = "info") -> dict[str, Any]:
    """Build a toast or banner notification payload."""
    return {"type": "notify", "message": message, "level": level}


def ui_confirm(
    message: str,
    *,
    action: str,
    action_args: str = "",
) -> dict[str, Any]:
    """Build a confirmation payload with its follow-up command action."""
    return {
        "type": "confirm",
        "message": message,
        "action": action,
        "action_args": action_args,
    }


def ui_select(
    title: str,
    options: list[dict[str, Any]],
    *,
    current: str = "",
    action: str = "",
) -> dict[str, Any]:
    """Build a selector payload whose chosen value feeds a command action."""
    return {
        "type": "select",
        "title": title,
        "current": current,
        "options": options,
        "action": action,
    }


def ui_info_panel(
    title: str,
    fields: list[dict[str, str]],
) -> dict[str, Any]:
    """Build a key/value information-card payload."""
    return {"type": "info_panel", "title": title, "fields": fields}


def ui_list(
    title: str,
    items: list[dict[str, str]],
) -> dict[str, Any]:
    """Build a styled list payload."""
    return {"type": "list", "title": title, "items": items}



@dataclass
class UserCommandResult:
    """Represent command text, consumption state, errors, and optional UI data."""

    output: str = ""
    consumed: bool = True
    error: str | None = None
    data: dict[str, Any] | None = None

    @property
    def success(self) -> bool:
        return self.error is None



@dataclass
class UserCommandContext:
    """Provide agent, session, input, output, and extension state to commands."""

    agent: Any | None = None
    session: Any | None = None
    input_module: Any | None = None
    output_fn: Callable[[str], None] | None = None
    extra: dict[str, Any] = field(default_factory=dict)



@runtime_checkable
class UserCommand(Protocol):
    """Protocol for user commands."""

    @property
    def name(self) -> str: ...

    @property
    def aliases(self) -> list[str]: ...

    @property
    def description(self) -> str: ...

    @property
    def layer(self) -> CommandLayer: ...

    async def execute(
        self, args: str, context: UserCommandContext
    ) -> UserCommandResult: ...


class BaseUserCommand:
    """Base class with error handling."""

    aliases: ClassVar[list[str]] = []

    async def execute(
        self, args: str, context: UserCommandContext
    ) -> UserCommandResult:
        try:
            return await self._execute(args, context)
        except Exception as e:
            return UserCommandResult(error=str(e))

    @abstractmethod
    async def _execute(
        self, args: str, context: UserCommandContext
    ) -> UserCommandResult: ...



def parse_slash_command(text: str) -> tuple[str, str]:
    """Split slash-command text into a lowercase name and remaining arguments."""
    text = text.lstrip("/")
    parts = text.split(None, 1)
    name = parts[0].lower() if parts else ""
    args = parts[1] if len(parts) > 1 else ""
    return name, args
