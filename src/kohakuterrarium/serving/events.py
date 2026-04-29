"""Legacy serving event dataclasses.

Kept as a tiny compatibility shim for older integrations and tests that
imported ``kohakuterrarium.serving.events`` before the serving facade moved to
the terrarium/studio runtime modules.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class ChannelEvent:
    """A message observed on a terrarium channel."""

    terrarium_id: str
    channel: str
    sender: str
    content: str
    message_id: str
    timestamp: datetime = field(default_factory=datetime.now)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class OutputEvent:
    """A text/activity event emitted by a running agent."""

    agent_id: str
    event_type: str
    content: str
    timestamp: datetime = field(default_factory=datetime.now)
    metadata: dict[str, Any] = field(default_factory=dict)


__all__ = ["ChannelEvent", "OutputEvent"]
