"""FastAPI dependencies.

Exposes two service layers as singletons:

- :func:`get_manager` — the legacy ``KohakuManager`` facade with
  agent_*/terrarium_*/creature_* methods.  Still used by every existing
  API route until they're cut over to the new engine.
- :func:`get_engine` — the new :class:`Terrarium` runtime engine.  New
  code (Studio layer, daemon mode, programmatic users) reaches for
  this instead.

Both are singletons; both carry their own state.  Routes will migrate
from ``get_manager`` to ``get_engine`` over time; until then the two
coexist.
"""

import os
from pathlib import Path

from kohakuterrarium.serving import KohakuManager
from kohakuterrarium.terrarium import Terrarium

_manager: KohakuManager | None = None
_engine: Terrarium | None = None

_DEFAULT_SESSION_DIR = str(Path.home() / ".kohakuterrarium" / "sessions")


def _session_dir() -> str:
    return os.environ.get("KT_SESSION_DIR", _DEFAULT_SESSION_DIR)


def get_manager() -> KohakuManager:
    """Return the singleton :class:`KohakuManager` instance."""
    global _manager
    if _manager is None:
        _manager = KohakuManager(session_dir=_session_dir())
    return _manager


def get_engine() -> Terrarium:
    """Return the singleton :class:`Terrarium` engine.

    The engine is the new programmatic surface for multi-agent
    runtime — see ``plans/structure-hierarchy/02-terrarium.md``.  HTTP
    routes that have been cut over use this; older routes still go
    through :func:`get_manager`.
    """
    global _engine
    if _engine is None:
        _engine = Terrarium(session_dir=_session_dir())
    return _engine
