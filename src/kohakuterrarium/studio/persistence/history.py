"""Read-only on-disk history (per-target) for saved sessions.

Builds history indexes and target payloads for saved or live sessions. Live
callers reuse the engine-owned store because reopening an actively written
SQLite file can fail on POSIX.
"""

from pathlib import Path
from typing import Any

from kohakuterrarium.errors import NotFoundError, SessionError, SessionNotFoundError
from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.studio.persistence.store import (
    session_history_payload,
    session_targets,
)


def history_index_from_store(store: SessionStore, session_name: str) -> dict[str, Any]:
    """Build session metadata and history targets from an open store."""
    try:
        meta = store.load_meta()
        targets = session_targets(store, meta)
        return {"session_name": session_name, "meta": meta, "targets": targets}
    except Exception as e:
        raise SessionError(f"History index load failed: {e}") from e


def history_index_payload(path: Path) -> dict[str, Any]:
    """Return session metadata and history targets for a saved session.

    Missing paths are rejected before ``SessionStore`` can create them. Other
    load failures are wrapped in ``SessionError``.
    """
    path = Path(path)
    if not path.exists():
        raise SessionNotFoundError(f"Session not found: {path}")
    store: SessionStore | None = None
    try:
        store = SessionStore(path)
        return history_index_from_store(store, path.stem)
    except SessionError:
        raise
    except Exception as e:
        raise SessionError(f"History index load failed: {e}") from e
    finally:
        if store is not None:
            store.close(update_status=False)


def history_from_store(
    store: SessionStore,
    session_name: str,
    target: str,
    live_job_ids: set[str] | None = None,
) -> dict[str, Any]:
    """Build validated target history from an already-open store."""
    try:
        meta = store.load_meta()
        valid_targets = set(session_targets(store, meta))
        if target not in valid_targets:
            raise NotFoundError(f"Target not found in session: {target}")
        payload = session_history_payload(store, target, live_job_ids=live_job_ids)
        payload["session_name"] = session_name
        payload["meta"] = meta
        return payload
    except NotFoundError:
        raise
    except Exception as e:
        raise SessionError(f"History load failed: {e}") from e


def history_payload(
    path: Path,
    target: str,
    live_job_ids: set[str] | None = None,
) -> dict[str, Any]:
    """Return read-only history for an agent, root, or channel target.

    ``live_job_ids`` prevents active work from being synthesized as interrupted.
    Saved sessions omit it because unmatched starts are no longer running.
    Missing sessions and targets retain their typed errors; other failures are
    wrapped in ``SessionError``.
    """
    path = Path(path)
    if not path.exists():
        raise SessionNotFoundError(f"Session not found: {path}")
    store: SessionStore | None = None
    try:
        store = SessionStore(path)
        return history_from_store(store, path.stem, target, live_job_ids)
    except (NotFoundError, SessionError):
        raise
    except Exception as e:
        raise SessionError(f"History load failed: {e}") from e
    finally:
        # Deterministic closure prevents failed lookups from leaving Windows
        # file handles that block deletion.
        if store is not None:
            store.close(update_status=False)
