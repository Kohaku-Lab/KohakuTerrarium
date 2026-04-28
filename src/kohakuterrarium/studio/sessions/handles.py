"""Session and SessionListing dataclasses.

A *session* in Studio vocabulary corresponds to a Terrarium engine
*graph*: one or more creatures sharing an environment. A standalone
agent is a 1-creature graph and so is a 1-creature session.

These are read-only handles that describe what is currently running.
The actual mutation entry points live in :mod:`studio.sessions.lifecycle`,
:mod:`studio.sessions.topology`, etc.
"""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Session:
    """A live engine session — one graph plus its creatures.

    ``session_id`` is the graph id minted by the engine's topology
    layer. Solo creatures have a fresh per-creature graph; recipe-built
    terrariums share one graph among all their creatures.
    """

    session_id: str
    kind: str  # "creature" or "terrarium"
    name: str
    creatures: list[dict] = field(default_factory=list)
    channels: list[dict] = field(default_factory=list)
    created_at: str = ""
    config_path: str = ""
    pwd: str = ""
    has_root: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "kind": self.kind,
            "name": self.name,
            "creatures": self.creatures,
            "channels": self.channels,
            "created_at": self.created_at,
            "config_path": self.config_path,
            "pwd": self.pwd,
            "has_root": self.has_root,
        }


@dataclass
class SessionListing:
    """A short-form listing entry used by ``list_sessions`` for UI tabs."""

    session_id: str
    kind: str
    name: str
    running: bool = True
    creatures: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "kind": self.kind,
            "name": self.name,
            "running": self.running,
            "creatures": self.creatures,
        }
