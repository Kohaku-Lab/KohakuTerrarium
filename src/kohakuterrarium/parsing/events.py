"""
Parse events emitted by the stream parser.

These events represent parsed segments from LLM output:
- TextEvent: Regular text content
- ToolCallEvent: Tool call block
- SubAgentCallEvent: Sub-agent call block
- CommandEvent: Legacy/custom-format framework command
"""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class TextEvent:
    """
    Regular text content from LLM output.

    Emitted for text outside of any special blocks.
    """

    text: str

    def __bool__(self) -> bool:
        return bool(self.text)


@dataclass
class ToolCallEvent:
    """
    Tool call detected in LLM output.

    Attributes:
        name: Tool name
        args: Parsed arguments (dict)
        raw: Raw content of the tool block
    """

    name: str
    args: dict[str, Any] = field(default_factory=dict)
    raw: str = ""

    def __repr__(self) -> str:
        return f"ToolCallEvent(name={self.name!r}, args={self.args})"


@dataclass
class SubAgentCallEvent:
    """
    Sub-agent call detected in LLM output.

    Attributes:
        name: Sub-agent name
        args: Parsed arguments (dict)
        raw: Raw content of the sub-agent block
    """

    name: str
    args: dict[str, Any] = field(default_factory=dict)
    raw: str = ""

    def __repr__(self) -> str:
        return f"SubAgentCallEvent(name={self.name!r}, args={self.args})"


@dataclass
class CommandEvent:
    """
    Framework command detected in LLM output.

    Commands parsed from the configured text tool-call format.

    Attributes:
        command: Command name (read, info, etc.)
        args: Command arguments as string
        raw: Raw content of the command
    """

    command: str
    args: str = ""
    raw: str = ""

    def __repr__(self) -> str:
        return f"CommandEvent(command={self.command!r}, args={self.args!r})"


@dataclass
class OutputCallEvent:
    """
    Explicit output block detected in LLM output.

    Format: [/output_<target>]content[output_<target>/]
    Example: [/output_discord]Hello![output_discord/]

    Attributes:
        target: Output target name (e.g., "discord", "tts")
        content: Content to output
        raw: Raw content of the block
    """

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
    """
    Signals the start of a block (tool, subagent, etc.).

    Used for early resource allocation before block completes.
    """

    block_type: str  # "tool", "subagent", "command"
    name: str | None = None


@dataclass
class BlockEndEvent:
    """
    Signals the end of a block.

    Used to finalize block processing.
    """

    block_type: str
    success: bool = True
    error: str | None = None


@dataclass
class CommandResultEvent:
    """
    Result of an inline framework command.

    NOT LLM output - this is injected by the controller after executing
    a command (read, info, wait, jobs). Should be routed to agent feedback
    context, NOT to user output.

    Attributes:
        command: Command that was executed
        content: Result content (empty if error)
        error: Error message (empty if success)
    """

    command: str
    content: str = ""
    error: str | None = None


@dataclass
class AssistantImageEvent:
    """Structured image part emitted by the assistant mid / end of turn.

    Produced by the controller after persisting an ImagePart returned
    via a provider-native tool (e.g. Codex `image_generation`). The
    output router forwards this to secondary outputs so live UIs can
    render the image without waiting for a save/resume round-trip.

    Attributes:
        url: Final image URL — typically a session-local
            ``/api/sessions/{id}/artifacts/...`` path.
        detail: Vision detail hint from the original ImagePart.
        source_type: Optional tag (e.g. ``"image_gen"``).
        source_name: Optional id / filename hint.
        revised_prompt: Optional backend-revised prompt text.
    """

    url: str
    detail: str = "auto"
    source_type: str | None = None
    source_name: str | None = None
    revised_prompt: str | None = None


# Union type for all parse events
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
    """Check if event requires action (tool/subagent/command)."""
    return isinstance(event, (ToolCallEvent, SubAgentCallEvent, CommandEvent))


def is_text_event(event: ParseEvent) -> bool:
    """Check if event is text content."""
    return isinstance(event, TextEvent)
