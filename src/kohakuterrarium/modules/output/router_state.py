"""Shared state types for the output router.

Shared state types remain dependency-light to avoid router/mixin import cycles.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, auto


@dataclass
class CompletedOutput:
    """Record output delivery for controller feedback without re-reading targets."""

    target: str
    content: str
    timestamp: datetime = field(default_factory=datetime.now)
    success: bool = True
    error: str | None = None

    def preview(self, max_len: int = 100) -> str:
        """Return a length-limited content preview."""
        if len(self.content) <= max_len:
            return self.content
        return self.content[:max_len] + "..."

    def to_feedback_line(self) -> str:
        """Format one controller feedback line."""
        time_str = self.timestamp.strftime("%H:%M:%S")
        if self.success:
            preview = self.preview(80)
            preview = preview.replace("\n", "\\n")
            return f'- [{self.target}] ({time_str}): "{preview}"'
        return f"- [{self.target}] ({time_str}): FAILED - {self.error}"


class OutputState(Enum):
    """Represent parser context that controls raw-text visibility."""

    NORMAL = auto()
    TOOL_BLOCK = auto()
    SUBAGENT_BLOCK = auto()
    COMMAND_BLOCK = auto()
    OUTPUT_BLOCK = auto()
