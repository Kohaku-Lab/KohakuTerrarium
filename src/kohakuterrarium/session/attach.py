"""Compatibility re-exports for session attachment helpers.

The concrete implementation now lives in
:mod:`kohakuterrarium.session.attachment_service`. This module remains as a
thin compatibility layer for older imports.
"""

from kohakuterrarium.session.attachment_service import attach_agent_to_session
from kohakuterrarium.session.attachment_service import detach_agent_from_session
from kohakuterrarium.session.attachment_service import get_attach_state

__all__ = [
    "attach_agent_to_session",
    "detach_agent_from_session",
    "get_attach_state",
]
