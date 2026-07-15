"""Shared graph resources and creature-private runtime sessions."""

from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from kohakuterrarium.core.channel import ChannelRegistry
from kohakuterrarium.core.session import Session
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


@dataclass
class Environment:
    """Hold shared channels, extension state, and per-creature sessions."""

    env_id: str = field(default_factory=lambda: f"env_{uuid4().hex[:8]}")
    shared_channels: ChannelRegistry = field(default_factory=ChannelRegistry)
    _sessions: dict[str, Session] = field(default_factory=dict)
    _context: dict[str, Any] = field(default_factory=dict)

    def get_session(self, key: str) -> Session:
        """Return the creature-private session for ``key``, creating it if needed."""
        if key not in self._sessions:
            self._sessions[key] = Session(key=key)
            logger.debug(
                "Session created in environment",
                env_id=self.env_id,
                session_key=key,
            )
        return self._sessions[key]

    def list_sessions(self) -> list[str]:
        """Return all creature session keys in this environment."""
        return list(self._sessions.keys())

    def register(self, key: str, value: Any) -> None:
        """Register shared extension state under ``key``."""
        self._context[key] = value

    def get(self, key: str, default: Any = None) -> Any:
        """Return shared extension state or ``default``."""
        return self._context.get(key, default)
