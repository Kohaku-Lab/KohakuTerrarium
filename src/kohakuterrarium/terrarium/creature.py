"""Creature handle - wrapper around an Agent with terrarium metadata."""

from dataclasses import dataclass, field

from kohakuterrarium.core.agent import Agent
from kohakuterrarium.terrarium.config import CreatureConfig
from kohakuterrarium.terrarium.output_log import LogEntry, OutputLogCapture


@dataclass
class CreatureHandle:
    """Pair a live agent with its configuration, channels, and output capture."""

    name: str
    agent: Agent
    config: CreatureConfig
    listen_channels: list[str] = field(default_factory=list)
    send_channels: list[str] = field(default_factory=list)
    output_log: OutputLogCapture | None = None

    @property
    def is_running(self) -> bool:
        """Check if the underlying agent is running."""
        return self.agent.is_running

    def get_log_entries(self, last_n: int = 20) -> list[LogEntry]:
        """Get recent output log entries."""
        if self.output_log:
            return self.output_log.get_entries(last_n=last_n)
        return []

    def get_log_text(self, last_n: int = 10) -> str:
        """Get recent text output."""
        if self.output_log:
            return self.output_log.get_text(last_n=last_n)
        return ""
