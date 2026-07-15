"""Unified multimodal events exchanged across agent runtime components."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from kohakuterrarium.core.tool_output import render_content_text
from kohakuterrarium.llm.message import ContentPart, normalize_content_parts

EventContent = str | list[ContentPart]


@dataclass
class TriggerEvent:
    """Carry typed event content, context, and batching behavior across modules."""

    type: str
    content: EventContent = ""
    context: dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=datetime.now)
    job_id: str | None = None
    prompt_override: str | None = None
    stackable: bool = True

    def __post_init__(self) -> None:
        """Reject events without a routing type."""
        if not self.type:
            raise ValueError("TriggerEvent type cannot be empty")

    def get_text_content(self) -> str:
        """Render text and safe placeholders without exposing binary payloads."""
        return render_content_text(self.content)

    def is_multimodal(self) -> bool:
        """Return whether the event contains structured content parts."""
        return isinstance(self.content, list)

    def with_context(self, **kwargs: Any) -> "TriggerEvent":
        """Return a copy with merged context, leaving this event unchanged."""
        new_context = {**self.context, **kwargs}
        return TriggerEvent(
            type=self.type,
            content=self.content,
            context=new_context,
            timestamp=self.timestamp,
            job_id=self.job_id,
            prompt_override=self.prompt_override,
            stackable=self.stackable,
        )

    def __repr__(self) -> str:
        parts = [f"TriggerEvent(type={self.type!r}"]
        if self.content:
            if isinstance(self.content, str):
                content_preview = (
                    self.content[:50] + "..."
                    if len(self.content) > 50
                    else self.content
                )
                parts.append(f"content={content_preview!r}")
            else:
                parts.append(f"content=[{len(self.content)} parts]")
        if self.job_id:
            parts.append(f"job_id={self.job_id!r}")
        if self.context:
            parts.append(f"context_keys={list(self.context.keys())}")
        parts.append(f"stackable={self.stackable}")
        return ", ".join(parts) + ")"


class EventType:
    """Canonical event type names used across runtime components."""

    USER_INPUT = "user_input"
    IDLE = "idle"
    TIMER = "timer"
    CONTEXT_UPDATE = "context_update"
    TOOL_COMPLETE = "tool_complete"
    SUBAGENT_OUTPUT = "subagent_output"
    CHANNEL_MESSAGE = "channel_message"
    CREATURE_OUTPUT = "creature_output"
    MONITOR = "monitor"
    ERROR = "error"
    STARTUP = "startup"
    SHUTDOWN = "shutdown"


def create_user_input_event(
    content: EventContent,
    source: str = "cli",
    **extra_context: Any,
) -> TriggerEvent:
    """Create a user input event with standard context."""
    normalized = normalize_content_parts(content)
    return TriggerEvent(
        type=EventType.USER_INPUT,
        content=normalized if normalized is not None else "",
        context={"source": source, **extra_context},
        stackable=True,
    )


def create_tool_complete_event(
    job_id: str,
    content: EventContent,
    exit_code: int | None = None,
    error: str | None = None,
    **extra_context: Any,
) -> TriggerEvent:
    """Create a tool completion event with standard context."""
    context: dict[str, Any] = extra_context
    normalized = normalize_content_parts(content)
    if exit_code is not None:
        context["exit_code"] = exit_code
    if error is not None:
        context["error"] = error
    return TriggerEvent(
        type=EventType.TOOL_COMPLETE,
        content=normalized if normalized is not None else "",
        context=context,
        job_id=job_id,
        stackable=True,
    )


def create_creature_output_event(
    source: str,
    target: str,
    content: str,
    *,
    with_content: bool = True,
    source_event_type: str = "",
    turn_index: int = 0,
    prompt_override: str | None = None,
) -> TriggerEvent:
    """Create a turn-end event for output-wiring delivery.

    Context distinguishes content deliveries from metadata-only notifications and
    preserves the source trigger and monotonic turn index for observability.
    """
    return TriggerEvent(
        type=EventType.CREATURE_OUTPUT,
        content=content,
        context={
            "source": source,
            "target": target,
            "with_content": with_content,
            "source_event_type": source_event_type,
            "turn_index": turn_index,
        },
        prompt_override=prompt_override,
        stackable=True,
    )


def create_error_event(
    error_type: str,
    message: str,
    job_id: str | None = None,
    **extra_context: Any,
) -> TriggerEvent:
    """Create an error event with standard context."""
    return TriggerEvent(
        type=EventType.ERROR,
        content=message,
        context={"error_type": error_type, **extra_context},
        job_id=job_id,
        stackable=False,  # Errors retain their own turn for immediate handling.
    )
