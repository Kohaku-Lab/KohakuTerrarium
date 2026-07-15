"""Expose session attachment methods for binding onto ``Agent``."""

from typing import Any

from kohakuterrarium.session.attachment_service import attach_agent_to_session
from kohakuterrarium.session.attachment_service import detach_agent_from_session


def attach_to_session(self: Any, session: Any, role: str) -> None:
    """Attach this agent to a session under the given role."""
    attach_agent_to_session(self, session, role)


def detach_from_session(self: Any) -> None:
    """Detach this agent from its current session."""
    detach_agent_from_session(self)
