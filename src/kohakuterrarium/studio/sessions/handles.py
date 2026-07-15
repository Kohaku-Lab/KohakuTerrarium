"""Define read-only handles for active Terrarium graph sessions.

A session uniformly represents a graph, whether it contains one standalone
creature or a recipe-built team. Lifecycle and topology mutations live in the
neighboring session modules.
"""

import os
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Session:
    """A live engine session — one graph plus its creatures.

    ``home_node`` records which lab site this session's graph runs
    on (``"_host"`` in standalone or for host-local graphs; the
    worker's Lab ``client_id`` for remote graphs).  Each entry in
    ``creatures`` also carries ``home_node`` so the frontend can
    chip-render the site without cross-referencing.
    """

    session_id: str
    name: str
    creatures: list[dict] = field(default_factory=list)
    channels: list[dict] = field(default_factory=list)
    created_at: str = ""
    config_path: str = ""
    pwd: str = ""
    has_root: bool = False
    home_node: str = "_host"
    # Resume UIs use this to request a replacement for a missing saved directory.
    pwd_exists: bool = True

    def __post_init__(self) -> None:
        self.pwd_exists = (not self.pwd) or os.path.isdir(self.pwd)

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "name": self.name,
            "creatures": self.creatures,
            "channels": self.channels,
            "created_at": self.created_at,
            "config_path": self.config_path,
            "pwd": self.pwd,
            "pwd_exists": self.pwd_exists,
            "has_root": self.has_root,
            "home_node": self.home_node,
        }


@dataclass
class SessionListing:
    """A short-form listing entry used by ``list_sessions`` for UI tabs.

    ``node_id`` is the home node — ``"_host"`` for standalone-mode
    sessions (or host-local sessions in lab-host mode); the worker's
    Lab ``client_id`` for remote-hosted sessions.  Frontends in
    standalone mode can ignore the field; the lab-host node-picker
    UI uses it for the per-session badge.
    """

    session_id: str
    name: str
    running: bool = True
    creatures: int = 0
    node_id: str = "_host"

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "name": self.name,
            "running": self.running,
            "creatures": self.creatures,
            "node_id": self.node_id,
        }
