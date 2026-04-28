"""Studio sessions — engine-backed runtime ops on creatures and topology.

A *session* in studio vocabulary is a Terrarium engine *graph* — one
or more creatures sharing an environment.  Standalone agents live in
1-creature sessions; recipe-built terrariums live in N-creature
sessions.  Per-creature operations (interrupt / chat / scratchpad /
plugins / model / command / state) take ``(session_id, creature_id)``
and resolve through the engine.
"""

from kohakuterrarium.studio.sessions import lifecycle
from kohakuterrarium.studio.sessions.handles import Session, SessionListing

__all__ = [
    "Session",
    "SessionListing",
    "lifecycle",
]
