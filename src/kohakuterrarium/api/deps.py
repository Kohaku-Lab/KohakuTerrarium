"""FastAPI dependencies.

Exposes the runtime engine as a process-level singleton:

- :func:`get_engine` — the :class:`Terrarium` runtime that owns every
  active session.  Routes inject it via ``Depends(get_engine)`` and
  reach for the studio sessions modules to do real work.

The legacy ``KohakuManager`` facade (and the corresponding
``get_manager`` helper) was removed in Phase 3 of the studio cleanup —
the studio layer is now the only path.
"""

import os
from pathlib import Path

from kohakuterrarium.terrarium import Terrarium

_engine: Terrarium | None = None

_DEFAULT_SESSION_DIR = str(Path.home() / ".kohakuterrarium" / "sessions")


def _session_dir() -> str:
    return os.environ.get("KT_SESSION_DIR", _DEFAULT_SESSION_DIR)


def get_engine() -> Terrarium:
    """Return the singleton :class:`Terrarium` engine.

    The engine is the programmatic surface for multi-agent runtime —
    see ``plans/structure-hierarchy/02-terrarium.md``.
    """
    global _engine
    if _engine is None:
        _engine = Terrarium(session_dir=_session_dir())
    _engine._runtime_prompt.attach()
    return _engine
