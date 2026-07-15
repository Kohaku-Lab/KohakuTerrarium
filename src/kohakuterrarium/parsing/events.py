"""Define events emitted while parsing streamed LLM output."""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class TextEvent:
    """Represent text emitted outside a structured block."""

    text: str

    def __bool__(self) -> bool:
        return bool(self.text)


@dataclass
class ToolCallEvent:
    """Represent a parsed tool call and its original block content."""

    name: str
    args: dict[str, Any] = field(default_factory=dict)
    raw: str = ""

    def __repr__(self) -> str:
        return f"ToolCallEvent(name={self.name!r}, args={self.args})"


@dataclass
class SubAgentCallEvent:
    """Represent a parsed sub-agent call and its original block content."""

    name: str
    args: dict[str, Any] = field(default_factory=dict)
    raw: str = ""

    def __repr__(self) -> str:
        return f"SubAgentCallEvent(name={self.name!r}, args={self.args})"


@dataclass
class CommandEvent:
    """Represent a framework command parsed from the configured text format."""

    command: str
    args: str = ""
    raw: str = ""

    def __repr__(self) -> str:
        return f"CommandEvent(command={self.command!r}, args={self.args!r})"


@dataclass
class OutputCallEvent:
    """Route explicit block content to a named output target."""

    target: str
    content: str = ""
    raw: str = ""

    def __repr__(self) -> str:
        return (
            f"OutputCallEvent(target={self.target!r}, "
            f"content={self.content[:50]!r}...)"
        )


@dataclass
class BlockStartEvent:
    """Signal a block start so consumers can allocate resources early."""

    block_type: str  # One of "tool", "subagent", or "command".
    name: str | None = None


@dataclass
class BlockEndEvent:
    """Signal block completion and its parse outcome."""

    block_type: str
    success: bool = True
    error: str | None = None


@dataclass
class CommandResultEvent:
    """Carry an executed command result back into agent feedback context.

    The controller injects this event; it is not user-visible LLM output.
    """

    command: str
    content: str = ""
    error: str | None = None


@dataclass
class AssistantImageEvent:
    """Expose a persisted assistant image to live secondary outputs.

    The URL points to the final artifact, avoiding a save/resume round trip.
    """

    url: str
    detail: str = "auto"
    source_type: str | None = None
    source_name: str | None = None
    revised_prompt: str | None = None


ParseEvent = (
    TextEvent
    | ToolCallEvent
    | SubAgentCallEvent
    | CommandEvent
    | CommandResultEvent
    | OutputCallEvent
    | BlockStartEvent
    | BlockEndEvent
    | AssistantImageEvent
)


def is_action_event(event: ParseEvent) -> bool:
    """Return whether an event requires controller action."""
    return isinstance(event, (ToolCallEvent, SubAgentCallEvent, CommandEvent))


def is_text_event(event: ParseEvent) -> bool:
    """Return whether an event contains plain text."""
    return isinstance(event, TextEvent)
