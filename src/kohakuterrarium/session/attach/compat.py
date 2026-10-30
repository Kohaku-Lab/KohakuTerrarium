"""Preserve legacy imports for session attachment helpers."""

from kohakuterrarium.session.attach.service import attach_agent_to_session
from kohakuterrarium.session.attach.service import detach_agent_from_session
from kohakuterrarium.session.attach.service import get_attach_state

__all__ = [
    "attach_agent_to_session",
    "detach_agent_from_session",
    "get_attach_state",
]
