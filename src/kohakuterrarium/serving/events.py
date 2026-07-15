"""Preserve legacy serving event records for compatibility imports.

New runtime event surfaces live in the terrarium and Studio modules, but older
integrations may still deserialize or import these dataclasses.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class ChannelEvent:
    """Record a message observed on a terrarium channel."""

    terrarium_id: str
    channel: str
    sender: str
    content: str
    message_id: str
    timestamp: datetime = field(default_factory=datetime.now)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class OutputEvent:
    """Record text or activity emitted by a running agent."""

    agent_id: str
    event_type: str
    content: str
    timestamp: datetime = field(default_factory=datetime.now)
    metadata: dict[str, Any] = field(default_factory=dict)


__all__ = ["ChannelEvent", "OutputEvent"]
