"""Shared state types for the output router.

Lives in its own module so :class:`OutputRouter` (``router.py``) and
the parser-event mixin (``router_parsing.py``) can both import these
types without an import cycle.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, auto


@dataclass
class CompletedOutput:
    """Record of a completed output event.

    Tracked by the router so the controller can surface "your output
    landed" feedback without re-reading the output module's state.
    """

    target: str
    content: str
    timestamp: datetime = field(default_factory=datetime.now)
    success: bool = True
    error: str | None = None

    def preview(self, max_len: int = 100) -> str:
        """Get a preview of the content."""
        if len(self.content) <= max_len:
            return self.content
        return self.content[:max_len] + "..."

    def to_feedback_line(self) -> str:
        """Format as a single feedback line for controller."""
        time_str = self.timestamp.strftime("%H:%M:%S")
        if self.success:
            preview = self.preview(80)
            # Escape newlines for single-line display
            preview = preview.replace("\n", "\\n")
            return f'- [{self.target}] ({time_str}): "{preview}"'
        return f"- [{self.target}] ({time_str}): FAILED - {self.error}"


class OutputState(Enum):
    """Output routing state machine.

    Determines how raw text from the LLM is routed under different
    parser contexts. The router transitions between states as parser
    block-start/block-end events arrive.
    """

    NORMAL = auto()  # Regular text output (stdout)
    TOOL_BLOCK = auto()  # Inside tool call block (suppress output)
    SUBAGENT_BLOCK = auto()  # Inside sub-agent block (suppress output)
    COMMAND_BLOCK = auto()  # Inside command block
    OUTPUT_BLOCK = auto()  # Inside explicit output block
