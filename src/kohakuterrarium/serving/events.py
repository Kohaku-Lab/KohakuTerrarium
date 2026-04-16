"""Transport-agnostic event types for streaming."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from kohakuterrarium.core.events import EventContent


@dataclass
class ChannelEvent:
    """A channel message observed in a terrarium."""

    terrarium_id: str
    channel: str
    sender: str
    content: EventContent
    message_id: str
    timestamp: datetime = field(default_factory=datetime.now)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class OutputEvent:
    """An agent output event (text chunk, tool activity).

    event_type values:
        "text"              - A text chunk from the agent
        "tool_start"        - A tool execution has started
        "tool_done"         - A tool execution completed
        "tool_error"        - A tool execution failed
        "processing_start"  - The agent began processing input
        "processing_end"    - The agent finished processing input
    """

    agent_id: str
    event_type: str
    content: EventContent
    timestamp: datetime = field(default_factory=datetime.now)
    metadata: dict[str, Any] = field(default_factory=dict)
