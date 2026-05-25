"""Per-session filesystem + history helpers for the persistence layer.

The HTTP route files in ``api/routes/persistence/`` provide the
FastAPI surface; all filesystem + per-store helpers live here so
CLI and HTTP share one implementation.

Listing, search, and aggregation no longer live here — they are
served by the session-index sidecar
(``studio/persistence/session_index/``).  This module owns only the
per-session operations: resolve / list-files / delete / history /
disk-usage.
"""

import gc
import os
import time
from pathlib import Path
from typing import Any

from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.studio.persistence.session_index import (
    get_session_index_default,
)
from kohakuterrarium.studio.persistence.viewer.paths import (
    all_session_files,
    all_versions_for_session,
    normalize_session_stem,
    pick_canonical_per_session,
    resolve_session_path,
)
from kohakuterrarium.utils.config_dir import config_dir
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

# Default session directory. The HTTP route layer monkey-patches this
# in tests via ``studio.persistence.store._SESSION_DIR``; the helpers
# below also accept an explicit ``session_dir`` argument so callers
# that need full isolation (CLI tooling) can opt out of the singleton.
_SESSION_DIR = Path.home() / ".kohakuterrarium" / "sessions"


def _session_dir() -> Path:
    """Return the live session directory.

    Honours the ``KT_SESSION_DIR`` environment variable — the same
    documented override that ``studio.sessions.lifecycle._session_dir``
    and ``api.deps._session_dir`` already use to decide *where sessions
    are written*. Without this, the persistence namespace (resume /
    saved-list / history / viewer) looked in a different directory than
    the sessions namespace saved to, so a non-default ``KT_SESSION_DIR``
    made every saved session invisible to resume.

    Falls back to the module-global ``_SESSION_DIR`` (which the route
    layer still monkey-patches directly in some tests) when the env var
    is unset. Read fresh each call so both override mechanisms work.
    """
    env = os.environ.get("KT_SESSION_DIR")
    if env:
        return Path(env)
    # Legacy seam: tests still monkey-patch ``_SESSION_DIR`` directly.
    # If the live value differs from the documented hard-coded default,
    # respect the override.  Otherwise fall through to
    # ``config_dir() / "sessions"`` so a test setting only
    # ``KT_CONFIG_DIR`` (the conftest autouse fixture) doesn't leak
    # into the operator's real ``~/.kohakuterrarium/sessions``.
    _docs_default = Path.home() / ".kohakuterrarium" / "sessions"
    if _SESSION_DIR != _docs_default:
        return _SESSION_DIR
    return config_dir() / "sessions"


def all_session_files_default() -> list[Path]:
    """Every session file under the default ``_SESSION_DIR`` (Wave-D-aware)."""
    return all_session_files(_session_dir())


def disk_usage() -> dict[str, Any]:
    """Aggregate disk usage of the saved-session directory.

    Stats every session file + its ``-wal`` / ``-shm`` sidecars. Pure
    filesystem; no DB open. Returns:

        {
            "count": int,            # canonical session entries
            "total_bytes": int,      # incl. sidecars
            "oldest_at": float|None, # min mtime across canonical files
            "newest_at": float|None, # max mtime
            "session_dir": str,
        }
    """
    session_dir = _session_dir()
    if not session_dir.exists():
        return {
            "count": 0,
            "total_bytes": 0,
            "oldest_at": None,
            "newest_at": None,
            "session_dir": str(session_dir),
        }

    canonical = pick_canonical_per_session(session_dir)
    total = 0
    oldest: float | None = None
    newest: float | None = None
    for path in canonical:
        try:
            st = path.stat()
        except OSError:
            continue
        total += st.st_size
        if oldest is None or st.st_mtime < oldest:
            oldest = st.st_mtime
        if newest is None or st.st_mtime > newest:
            newest = st.st_mtime
        # Add sidecars so the surfaced number matches what the user
        # sees on disk.
        for suffix in ("-wal", "-shm"):
            sidecar = str(path) + suffix
            if not os.path.exists(sidecar):
                continue
            try:
                total += os.stat(sidecar).st_size
            except OSError:
                continue

    return {
        "count": len(canonical),
        "total_bytes": total,
        "oldest_at": oldest,
        "newest_at": newest,
        "session_dir": str(session_dir),
    }


def resolve_session_path_default(session_name: str) -> Path | None:
    """Resolve ``session_name`` against the default ``_SESSION_DIR``."""
    return resolve_session_path(session_name, _session_dir())


def all_versions_for_session_default(session_name: str) -> list[Path]:
    """Every file belonging to the given session (v1 + v2 rollback pair)."""
    return all_versions_for_session(session_name, _session_dir())


def session_targets(store: SessionStore, meta: dict[str, Any]) -> list[str]:
    """Return the ordered list of read-only history targets in a session.

    Includes every agent listed in meta + every channel + any extra
    targets discovered from the events / conversation tables.
    """
    targets: list[str] = []
    seen: set[str] = set()

    for target in meta.get("agents", []):
        if target and target not in seen:
            seen.add(target)
            targets.append(target)

    for ch in meta.get("terrarium_channels", []):
        name = ch.get("name", "")
        target = f"ch:{name}" if name else ""
        if target and target not in seen:
            seen.add(target)
            targets.append(target)

    if targets:
        return targets

    for key, _evt in store.get_all_events():
        if ":e" not in key:
            continue
        target = key.split(":e", 1)[0]
        if target and target not in seen:
            seen.add(target)
            targets.append(target)

    for key_bytes in store.conversation.keys(limit=2**31 - 1):
        target = key_bytes.decode() if isinstance(key_bytes, bytes) else key_bytes
        if target and target not in seen:
            seen.add(target)
            targets.append(target)

    return targets


def session_history_payload(store: SessionStore, target: str) -> dict[str, Any]:
    """Read-only history slice for a given agent/root/channel target."""
    if target.startswith("ch:"):
        channel = target[3:]
        messages = store.get_channel_messages(channel)
        return {
            "target": target,
            "messages": [],
            "events": [
                {
                    "type": "channel_message",
                    "channel": channel,
                    "sender": m.get("sender", ""),
                    "content": m.get("content", ""),
                    "ts": m.get("ts", 0),
                }
                for m in messages
            ],
        }

    get_events = getattr(store, "get_resumable_events", None) or store.get_events
    return {
        "target": target,
        "messages": store.load_conversation(target) or [],
        "events": get_events(target),
    }


def _unlink_with_retry(path: Path, attempts: int = 5, base_delay: float = 0.05) -> None:
    """Best-effort ``unlink`` with exponential backoff.

    The motivating bug (#59): on Windows the user views a session in
    the viewer, the viewer route closes its ``SessionStore`` (which
    ``del``s the native ``_inner`` handles), but the OS-level file
    lock on ``.kohakutr`` / ``-wal`` / ``-shm`` can linger for a few
    milliseconds while Python's refcount-driven destructor finishes
    inside the worker thread.  A delete fired immediately after the
    view close then races and raises ``PermissionError`` (WinError
    32, "the process cannot access the file because it is being
    used by another process").

    Five attempts with 50 / 100 / 200 / 400 / 800 ms gaps cover that
    window cheaply (worst-case ~1.5 s before re-raise — still a
    snappy interaction).  POSIX never hits this branch because
    ``unlink`` succeeds on first try regardless of open handles.
    """
    last_exc: OSError | None = None
    for i in range(attempts):
        try:
            path.unlink()
            return
        except FileNotFoundError:
            return  # already gone — idempotent.
        except PermissionError as e:
            last_exc = e
            # Nudge CPython to release any straggling C-side handles
            # before the next try (KohakuVault's native ``_KVault``
            # holds the SQLite connection via refcount).
            gc.collect()
            time.sleep(base_delay * (2**i))
    assert last_exc is not None
    raise last_exc


def _sidecars_for(path: Path) -> list[Path]:
    """Return existing ``-wal`` / ``-shm`` sidecars for a SQLite file."""
    out: list[Path] = []
    for suffix in ("-wal", "-shm"):
        sidecar = path.with_name(path.name + suffix)
        if sidecar.exists():
            out.append(sidecar)
    return out


def delete_session_files(session_name: str) -> list[Path]:
    """Delete every on-disk file belonging to ``session_name``.

    Returns the list of deleted paths. Returns an empty list when no
    matching file exists; the caller maps that to a 404. Falls back to
    fuzzy lookup if the user passes a legacy raw stem.

    Also drops the deleted entries from the session-index sidecar
    so the next ``list``/``stats`` call doesn't surface them.  Both
    callers (the FastAPI route and ``Studio.persistence.delete``)
    flow through here; without this purge the Studio surface returns
    deleted sessions until the next ``reconcile()``.
    """
    targets = all_versions_for_session_default(session_name)
    if not targets:
        resolved = resolve_session_path_default(session_name)
        if resolved is not None:
            targets = all_versions_for_session_default(normalize_session_stem(resolved))
            if not targets:
                targets = [resolved]

    if not targets:
        return []

    # Each main file may have ``-wal`` + ``-shm`` sidecars.  Delete
    # them too — orphan sidecars don't show up as phantom list rows
    # (the listing globs ``*.kohakutr*``, not ``-wal``) but they
    # waste disk and would confuse a re-create of a session with the
    # same name.  Sidecars first so the main file's lock can release
    # cleanly.
    for path in targets:
        for sidecar in _sidecars_for(path):
            try:
                _unlink_with_retry(sidecar)
            except OSError as e:
                logger.warning(
                    "Failed to remove SQLite sidecar",
                    sidecar=str(sidecar),
                    error=str(e),
                )

    for path in targets:
        _unlink_with_retry(path)

    _purge_index_entries(targets)
    return targets


def _purge_index_entries(deleted_paths: list[Path]) -> None:
    """Drop the just-deleted filenames from the session-index sidecar.

    Best-effort: an exception here doesn't block the delete from
    succeeding — the next ``reconcile`` would catch the orphans
    anyway.  The previously-route-side eager purge moved here so
    every caller path (FastAPI, ``Studio.persistence.delete``) goes
    through the same flow.
    """
    try:
        session_dir = _session_dir()
        index = get_session_index_default(session_dir)
        for path in deleted_paths:
            index.delete(path.name)
    except Exception as exc:  # noqa: BLE001 — best-effort
        logger.debug("session-index purge after delete failed", error=str(exc))
