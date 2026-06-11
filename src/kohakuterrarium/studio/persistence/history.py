"""Read-only on-disk history (per-target) for saved sessions.

Verbatim port of the ``GET /sessions/{name}/history`` and
``GET /sessions/{name}/history/{target}`` handler bodies from the
former ``api/routes/sessions.py``. The HTTP route layer resolves the
session name to a path and delegates here.
"""

from pathlib import Path
from typing import Any

from kohakuterrarium.errors import NotFoundError, SessionError, SessionNotFoundError
from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.studio.persistence.store import (
    session_history_payload,
    session_targets,
)


def history_index_payload(path: Path) -> dict[str, Any]:
    """Return ``{session_name, meta, targets}`` for a saved session.

    Raises :class:`SessionNotFoundError` when ``path`` is missing (before
    any store open — ``SessionStore(path)`` creates the file) and
    :class:`SessionError` on load failure.
    """
    path = Path(path)
    if not path.exists():
        raise SessionNotFoundError(f"Session not found: {path}")
    store: SessionStore | None = None
    try:
        store = SessionStore(path)
        meta = store.load_meta()
        targets = session_targets(store, meta)
        return {"session_name": path.stem, "meta": meta, "targets": targets}
    except Exception as e:
        raise SessionError(f"History index load failed: {e}") from e
    finally:
        if store is not None:
            store.close(update_status=False)


def history_payload(path: Path, target: str) -> dict[str, Any]:
    """Return read-only saved history for an agent/root/channel target.

    Raises :class:`SessionNotFoundError` for a missing session file,
    :class:`NotFoundError` for an unknown target, and
    :class:`SessionError` on load failure.
    """
    path = Path(path)
    if not path.exists():
        raise SessionNotFoundError(f"Session not found: {path}")
    store: SessionStore | None = None
    try:
        store = SessionStore(path)
        meta = store.load_meta()
        valid_targets = set(session_targets(store, meta))
        if target not in valid_targets:
            raise NotFoundError(f"Target not found in session: {target}")
        payload = session_history_payload(store, target)
        payload["session_name"] = path.stem
        payload["meta"] = meta
        return payload
    except NotFoundError:
        raise
    except Exception as e:
        raise SessionError(f"History load failed: {e}") from e
    finally:
        # Close on EVERY path — the unknown-target raise used to leak
        # the SQLite handle until GC (Windows then blocks deletes).
        if store is not None:
            store.close(update_status=False)
