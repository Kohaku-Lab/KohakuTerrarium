"""Open and inspect session files for resume.

Shared by the sync and async resume flows: migration-aware store opening,
workspace preflight, and session-type detection. Pure file work — callers
decide which thread or executor runs it.
"""

import os
from pathlib import Path

from kohakuterrarium.errors import SessionNotResumableError
from kohakuterrarium.session.migrations import (
    ensure_latest_version,
    latest_readable_version,
)
from kohakuterrarium.session.readonly import read_session_meta
from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


def open_store_with_migration(
    session_path: str | Path, *, writer_lock: bool = False
) -> SessionStore:
    """Open a session file, auto-migrating older formats upward first.

    Migration resolves the newest readable file while preserving the original
    path in failures. Live resumes may request a writer lock; preview consumers
    remain lock-free.
    """
    try:
        resolved = ensure_latest_version(session_path)
    except Exception as exc:
        raise RuntimeError(
            f"Failed to migrate session at {session_path}: {exc}"
        ) from exc
    if str(resolved) != str(session_path):
        logger.info(
            "Session auto-migrated before resume",
            original=str(session_path),
            opened=str(resolved),
        )
    return SessionStore(resolved, writer_lock=writer_lock)


def preflight_legacy_workspace(
    session_path: str | Path,
    pwd_override: str | None = None,
) -> str:
    """Resolve a legacy workspace without migration or writer acquisition."""
    path = latest_readable_version(session_path)
    meta = read_session_meta(path)
    dirty_state = meta.get("workspace_resume_state")
    if isinstance(dirty_state, dict) and dirty_state.get("status") == "partial_dirty":
        raise SessionNotResumableError(
            "Session has an incomplete workspace rollback and must be repaired"
        )
    saved_pwd = meta.get("pwd")
    pwd = pwd_override or saved_pwd
    if not (pwd and os.path.isdir(pwd)):
        source = "override" if pwd_override else "saved"
        raise SessionNotResumableError(
            f"The {source} working directory is missing or invalid: {pwd!r}. "
            "Choose a replacement directory or open the session history."
        )
    return str(Path(pwd).resolve())


def detect_session_type(session_path: str | Path) -> str:
    """Detect whether a session file is an agent or terrarium.

    Resolve migrations first so detection reflects the newest readable file.
    Missing type metadata defaults to ``"agent"``.
    """
    try:
        resolved = ensure_latest_version(session_path)
    except Exception:
        resolved = Path(session_path)
    store = SessionStore(resolved)
    try:
        meta = store.load_meta()
        return meta.get("config_type", "agent")
    finally:
        store.close(update_status=False)
