"""Keyed registry of session-scoped channels, scratchpads, and extensions."""

from dataclasses import dataclass, field
from typing import Any

from kohakuterrarium.core.channel import ChannelRegistry
from kohakuterrarium.core.scratchpad import Scratchpad
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

_DEFAULT_KEY = "__default__"


@dataclass
class Session:
    """Group the shared runtime state owned by one session key."""

    key: str
    channels: ChannelRegistry = field(default_factory=ChannelRegistry)
    scratchpad: Scratchpad = field(default_factory=Scratchpad)
    tui: Any | None = None
    extra: dict[str, Any] = field(default_factory=dict)


_sessions: dict[str, Session] = {}


def get_session(key: str | None = None) -> Session:
    """Return the session for ``key``, creating the default when omitted."""
    k = key or _DEFAULT_KEY
    if k not in _sessions:
        _sessions[k] = Session(key=k)
        logger.debug("Session created", session_key=k)
    return _sessions[k]


def set_session(session: Session, key: str | None = None) -> None:
    """Register a supplied session under ``key`` or the default key."""
    k = key or _DEFAULT_KEY
    _sessions[k] = session
    logger.debug("Session injected", session_key=k)


def remove_session(key: str | None = None) -> None:
    """Remove the session under ``key`` or the default key."""
    k = key or _DEFAULT_KEY
    if _sessions.pop(k, None) is not None:
        logger.debug("Session removed", session_key=k)


def list_sessions() -> list[str]:
    """List all active session keys."""
    return list(_sessions.keys())


def get_scratchpad() -> Scratchpad:
    """Return the default session scratchpad for legacy callers."""
    return get_session().scratchpad


def get_channel_registry() -> ChannelRegistry:
    """Return the default session channel registry for legacy callers."""
    return get_session().channels
