"""Read-only on-disk history (per-target) for saved sessions.

Verbatim port of the ``GET /sessions/{name}/history`` and
``GET /sessions/{name}/history/{target}`` handler bodies from the
former ``api/routes/sessions.py``. The HTTP route layer resolves the
session name to a path and delegates here.
"""

from pathlib import Path
from typing import Any

from fastapi import HTTPException

from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.studio.persistence.store import (
    session_history_payload,
    session_targets,
)


def history_index_payload(path: Path) -> dict[str, Any]:
    """Return ``{session_name, meta, targets}`` for a saved session."""
    try:
        store = SessionStore(path)
        meta = store.load_meta()
        targets = session_targets(store, meta)
        store.close(update_status=False)
        return {"session_name": path.stem, "meta": meta, "targets": targets}
    except Exception as e:
        raise HTTPException(500, f"History index load failed: {e}")


def history_payload(path: Path, target: str) -> dict[str, Any]:
    """Return read-only saved history for an agent/root/channel target."""
    try:
        store = SessionStore(path)
        meta = store.load_meta()
        valid_targets = set(session_targets(store, meta))
        if target not in valid_targets:
            raise HTTPException(404, f"Target not found in session: {target}")
        payload = session_history_payload(store, target)
        payload["session_name"] = path.stem
        payload["meta"] = meta
        store.close(update_status=False)
        return payload
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"History load failed: {e}")
