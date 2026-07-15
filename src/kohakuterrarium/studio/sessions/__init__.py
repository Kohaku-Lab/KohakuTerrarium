"""Expose Studio operations and handles for Terrarium graph sessions.

Standalone creatures and recipe-built teams share the same session abstraction.
Per-creature operations identify both the graph and the creature within it.
"""

from kohakuterrarium.studio.sessions import lifecycle
from kohakuterrarium.studio.sessions.handles import Session, SessionListing

__all__ = [
    "Session",
    "SessionListing",
    "lifecycle",
]
