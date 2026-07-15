"""Define typed output events and renderer replies for the output bus."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

from kohakuterrarium.llm.message import ContentPart

# Unsupported renderer surfaces fall back to chat rather than dropping events.
Surface = Literal["chat", "modal", "toast", "side-panel", "status"]

ACTION_TIMEOUT = "__timeout__"
ACTION_SUPERSEDED = "__superseded__"


@dataclass
class OutputEvent:
    """Universal output-side event. Counterpart to TriggerEvent."""

    type: str
    content: str | list[ContentPart] = ""
    payload: dict[str, Any] = field(default_factory=dict)
    id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=datetime.now)

    surface: Surface = "chat"
    interactive: bool = False
    update_target: str | None = None
    timeout_s: float | None = None
    correlation_id: str | None = None


@dataclass
class UIReply:
    """Represent a renderer response with protocol-level timeout or supersede actions."""

    event_id: str
    action_id: str
    values: dict[str, Any] = field(default_factory=dict)
    user: str | None = None
    timestamp: float = 0.0

    @property
    def is_timeout(self) -> bool:
        return self.action_id == ACTION_TIMEOUT

    @property
    def is_superseded(self) -> bool:
        return self.action_id == ACTION_SUPERSEDED
