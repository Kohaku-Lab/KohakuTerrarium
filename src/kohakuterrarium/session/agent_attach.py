"""High-level Agent attach/detach helpers.

The low-level attach service lives in
:mod:`kohakuterrarium.session.attachment_service`. This module provides the
methods rebound onto ``Agent`` while keeping ``core.agent_observability``
independent of the session attach stack.
"""

from typing import Any

from kohakuterrarium.session.attachment_service import attach_agent_to_session
from kohakuterrarium.session.attachment_service import detach_agent_from_session


def attach_to_session(self: Any, session: Any, role: str) -> None:
    """Bind ``self`` to ``session`` under ``role`` (Wave F)."""
    attach_agent_to_session(self, session, role)


def detach_from_session(self: Any) -> None:
    """Unbind ``self`` from its attached session (Wave F)."""
    detach_agent_from_session(self)
